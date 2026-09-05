"""HTTP adapter for the durable Wikidata Publication module."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from converter.authority.heading_fidelity import person_authority_given_name_conflict

from app.models.publication import (
    Publication as PublicationRow,
)
from app.models.publication import (
    PublicationApprovalDecision,
    PublicationApprovalSet,
    PublicationDryRunReceipt,
    PublicationExecution,
    PublicationFinding,
    PublicationJournalEvent,
    PublicationPlan,
    PublicationRelease,
)
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.publication.core import ProjectionSource, PublicationModule
from app.publication.digests import thaw_json
from app.publication.gateway import WikidataGateway, WikidataGatewaySession
from app.publication.repository import (
    PublicationNotFoundError,
)
from app.publication.sql_repository import SqlAlchemyPublicationRepository
from app.publication.types import (
    AuditPage,
    AuditQuery,
    CancelCommand,
    DryRunCommand,
    EntityPage,
    EntityPageQuery,
    EntitySelection,
    PrepareRequest,
    ProfileRef,
    PublicationEntityInput,
    PublishCommand,
    ResumeCommand,
    ReviewCommand,
    SourceSnapshotRef,
    SummaryQuery,
    TargetRef,
)
from app.publication.types import (
    PublicationEntity as CorePublicationEntity,
)
from app.publication.types import (
    PublicationSummary as CorePublicationSummary,
)
from app.schemas.publication import (
    AdvancePublicationRequest,
    ApprovalSetSummary,
    CancelPublicationCommand,
    DryRunPublicationCommand,
    DryRunReceiptSummary,
    EntityKeysSelection,
    ExecutionSummary,
    PlanSummary,
    PreparePublicationRequest,
    PublicationAuditEvent,
    PublicationAuditPage,
    PublicationEntitiesQuery,
    PublicationEntity,
    PublicationEntityFinding,
    PublicationEntityPage,
    PublicationMutationResponse,
    PublicationOperation,
    PublicationOperationQuery,
    PublicationOperationRead,
    PublicationReadQuery,
    PublicationSummary,
    PublicationSummaryRead,
    PublishPublicationCommand,
    ReleaseSummary,
    ResumePublicationCommand,
    ReviewPublicationCommand,
)

PROJECTION_PAGE_SIZE = 500
_SNAPSHOT_PREFIX = "wikidata-studio"
_QID = re.compile(r"^Q[1-9][0-9]*$")
_STRONG_IDENTIFIER_PROPERTIES = {"P214", "P8189"}


class PublicationRuntimeError(RuntimeError):
    """Base error for the HTTP adapter."""


class PublicationSourceError(PublicationRuntimeError):
    """The requested Studio source cannot form a safe Release."""


class UnsupportedPublicationActionError(PublicationRuntimeError):
    """The HTTP contract names an action that the core does not implement."""


class PublicationGatewayUnavailableError(PublicationRuntimeError):
    """No production Wikidata gateway adapter is configured."""


class PublicationGatewayFactory(Protocol):
    def __call__(
        self,
        *,
        target: TargetRef,
        actor_id: str,
    ) -> WikidataGateway: ...


class _UnavailableGateway:
    async def open(self, credential_ref: str) -> WikidataGatewaySession:
        del credential_ref
        raise PublicationGatewayUnavailableError(
            "The Wikidata Publication gateway is not configured"
        )


def unavailable_gateway_factory(
    *,
    target: TargetRef,
    actor_id: str,
) -> WikidataGateway:
    del target, actor_id
    return _UnavailableGateway()


def _snapshot_id(run_id: str, source: str, approved_only: bool) -> str:
    return f"{_SNAPSHOT_PREFIX}:{run_id}:{source}:{int(approved_only)}"


def _snapshot_parts(snapshot_id: str) -> tuple[uuid.UUID, str, bool]:
    parts = snapshot_id.split(":")
    if len(parts) != 4 or parts[0] != _SNAPSHOT_PREFIX:
        raise PublicationSourceError("The Source Snapshot identifier is invalid")
    try:
        run_id = uuid.UUID(parts[1])
    except ValueError as exc:
        raise PublicationSourceError("The Source Snapshot run identifier is invalid") from exc
    if parts[2] not in {"legacy", "canonical"} or parts[3] not in {"0", "1"}:
        raise PublicationSourceError("The Source Snapshot selector is invalid")
    return run_id, parts[2], parts[3] == "1"


def _item_entity_key(item: Mapping[str, object]) -> str:
    value = str(item.get("local_id") or item.get("_local_id") or "").strip()
    if not value:
        raise PublicationSourceError("Each Studio item must have a local_id")
    return value


def _item_entity_type(item: Mapping[str, object]) -> str:
    value = str(item.get("entity_type") or "").strip()
    if not value:
        raise PublicationSourceError("Each Studio item must have an entity_type")
    return value


def _statement_property(statement: Mapping[str, object]) -> str:
    return str(statement.get("property_id") or statement.get("property") or "").strip()


def _statement_value(statement: Mapping[str, object]) -> str:
    value = statement.get("value_id", statement.get("value"))
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return str(value.get("id") or value.get("value") or "").strip()
    return ""


def _iter_statements(item: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw = item.get("statements")
    if not isinstance(raw, list):
        return ()
    return tuple(statement for statement in raw if isinstance(statement, Mapping))


def _identity_assertions(item: Mapping[str, object]) -> tuple[str, ...]:
    assertions: set[str] = set()
    qid = str(item.get("existing_qid") or "").strip()
    if _QID.fullmatch(qid):
        assertions.add(f"wikidata:{qid}")
    for statement in _iter_statements(item):
        property_id = _statement_property(statement)
        value = _statement_value(statement)
        if property_id in _STRONG_IDENTIFIER_PROPERTIES and value:
            assertions.add(f"{property_id}:{value}")
    return tuple(sorted(assertions))


def _local_references(item: Mapping[str, object]) -> tuple[str, ...]:
    references: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, str) and value.startswith("__LOCAL:"):
            references.add(value.removeprefix("__LOCAL:"))
        elif isinstance(value, list):
            for entry in value:
                visit(entry)
        elif isinstance(value, Mapping):
            for entry in value.values():
                visit(entry)

    visit(item.get("statements"))
    return tuple(sorted(references))


def _resolve_statement_references(value: object, qids: Mapping[str, str]) -> object:
    if isinstance(value, str) and value.startswith("__LOCAL:"):
        return qids.get(value.removeprefix("__LOCAL:"), value)
    if isinstance(value, list):
        return [_resolve_statement_references(entry, qids) for entry in value]
    if isinstance(value, Mapping):
        return {key: _resolve_statement_references(entry, qids) for key, entry in value.items()}
    return value


def _evidence_refs(item: Mapping[str, object]) -> tuple[str, ...]:
    raw = item.get("records")
    if not isinstance(raw, list):
        return ()
    refs: set[str] = set()
    for value in raw:
        if isinstance(value, str) and value.strip():
            refs.add(f"record:{value.strip()}")
        elif isinstance(value, Mapping):
            record_id = str(
                value.get("control_number") or value.get("record_id") or ""
            ).strip()
            if record_id:
                refs.add(f"record:{record_id}")
    return tuple(sorted(refs))


def _has_blocking_item_issue(item: Mapping[str, object]) -> bool:
    if person_authority_given_name_conflict(item):
        return True
    issues = item.get("validation_issues")
    return isinstance(issues, list) and any(
        isinstance(issue, Mapping) and issue.get("severity") == "error"
        for issue in issues
    )


class StudioCacheProjectionSource(ProjectionSource):
    """Read one immutable Release from the current Studio cache."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current_snapshot(
        self,
        *,
        run_id: uuid.UUID,
        source: str,
        approved_only: bool,
    ) -> SourceSnapshotRef:
        metadata = await self._cache_metadata(run_id, source, approved_only)
        if metadata is None:
            raise PublicationSourceError(
                "Build the selected Wikidata Studio source before preparation"
            )
        input_fingerprint, built_at = metadata
        return SourceSnapshotRef(
            snapshot_id=_snapshot_id(str(run_id), source, approved_only),
            revision=_aware(built_at).isoformat(),
            digest=input_fingerprint,
        )

    async def project(
        self,
        source_snapshot: SourceSnapshotRef,
        profile: ProfileRef,
    ) -> AsyncIterator[tuple[PublicationEntityInput, ...]]:
        if profile.name != "mhm-wikidata" or profile.version not in {"1", "1-nodes"}:
            raise PublicationSourceError("The requested publication profile is unsupported")
        run_id, source, approved_only = _snapshot_parts(source_snapshot.snapshot_id)
        metadata = await self._cache_metadata(run_id, source, approved_only)
        if metadata is None or metadata[0] != source_snapshot.digest:
            raise PublicationSourceError("The selected Studio source changed before preparation")
        qids: dict[str, str] = {}
        entity_keys: set[str] = set()
        async for target in self._stream_items(run_id, source, approved_only, source_snapshot.digest):
            entity_keys.add(_item_entity_key(target))
            qid = str(target.get("existing_qid") or "").strip()
            if _QID.fullmatch(qid) and not _has_blocking_item_issue(target):
                qids[_item_entity_key(target)] = qid
        page: list[PublicationEntityInput] = []
        async for item in self._stream_items(
            run_id,
            source,
            approved_only,
            source_snapshot.digest,
        ):
            if _has_blocking_item_issue(item):
                raise PublicationSourceError(
                    "The selected Studio source contains blocking validation issues"
                )
            item = dict(item)
            item["statements"] = _resolve_statement_references(item.get("statements", []), qids)
            if profile.version == "1-nodes":
                kept: list[Mapping[str, object]] = []
                deferred = list(item.get("deferred_statements") or [])
                for statement in _iter_statements(item):
                    targets = _local_references({"statements": [statement]})
                    if targets and set(targets) <= entity_keys:
                        deferred.append(dict(statement))
                    else:
                        kept.append(statement)
                item["statements"] = kept
                item["deferred_statements"] = deferred
            page.append(
                PublicationEntityInput(
                    entity_key=_item_entity_key(item),
                    entity_type=_item_entity_type(item),
                    document=cast("dict[str, object]", dict(item)),
                    evidence_refs=_evidence_refs(item),
                    identity_assertions=_identity_assertions(item),
                    local_references=_local_references(item),
                )
            )
            if len(page) == PROJECTION_PAGE_SIZE:
                yield tuple(page)
                page = []
        if page:
            yield tuple(page)
        final_metadata = await self._cache_metadata(run_id, source, approved_only)
        if final_metadata is None or final_metadata[0] != source_snapshot.digest:
            raise PublicationSourceError(
                "The selected Studio source changed during preparation"
            )

    async def is_current(
        self,
        run_id: str,
        source_snapshot: SourceSnapshotRef,
    ) -> bool:
        snapshot_run_id, source, approved_only = _snapshot_parts(
            source_snapshot.snapshot_id
        )
        if str(snapshot_run_id) != run_id:
            return False
        metadata = await self._cache_metadata(snapshot_run_id, source, approved_only)
        return metadata is not None and metadata[0] == source_snapshot.digest

    async def _cache_metadata(
        self,
        run_id: uuid.UUID,
        source: str,
        approved_only: bool,
    ) -> tuple[str, datetime] | None:
        return (
            await self._session.execute(
                select(
                    WikidataStudioCache.input_fingerprint,
                    WikidataStudioCache.built_at,
                ).where(
                    WikidataStudioCache.run_id == run_id,
                    WikidataStudioCache.source == source,
                    WikidataStudioCache.approved_only == approved_only,
                )
            )
        ).one_or_none()

    async def _stream_items(
        self,
        run_id: uuid.UUID,
        source: str,
        approved_only: bool,
        input_fingerprint: str,
    ) -> AsyncIterator[Mapping[str, object]]:
        dialect = self._session.bind.dialect.name if self._session.bind is not None else ""
        if dialect == "postgresql":
            statement = text(
                """
                SELECT expanded.item
                FROM wikidata_studio_cache
                CROSS JOIN LATERAL jsonb_array_elements(
                    wikidata_studio_cache.result_items
                ) AS expanded(item)
                WHERE wikidata_studio_cache.run_id = :run_id
                  AND wikidata_studio_cache.source = :source
                  AND wikidata_studio_cache.approved_only = :approved_only
                  AND wikidata_studio_cache.input_fingerprint = :input_fingerprint
                ORDER BY COALESCE(
                    expanded.item->>'local_id',
                    expanded.item->>'_local_id'
                )
                """
            )
        else:
            statement = text(
                """
                SELECT value AS item
                FROM wikidata_studio_cache,
                     json_each(wikidata_studio_cache.result_items)
                WHERE wikidata_studio_cache.run_id = :run_id
                  AND wikidata_studio_cache.source = :source
                  AND wikidata_studio_cache.approved_only = :approved_only
                  AND wikidata_studio_cache.input_fingerprint = :input_fingerprint
                ORDER BY COALESCE(
                    json_extract(value, '$.local_id'),
                    json_extract(value, '$._local_id')
                )
                """
            )
        rows = await self._session.stream(
            statement,
            {
                "run_id": run_id if dialect == "postgresql" else run_id.hex,
                "source": source,
                "approved_only": approved_only,
                "input_fingerprint": input_fingerprint,
            },
        )
        async for row in rows:
            raw = row._mapping["item"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            if not isinstance(raw, Mapping):
                raise PublicationSourceError("A Studio cache item is not an object")
            yield raw


class PublicationRuntime:
    """Translate HTTP commands and nested read models around the core."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        gateway_factory: PublicationGatewayFactory,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._gateway_factory = gateway_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._projection_source = StudioCacheProjectionSource(session)

    async def prepare(
        self,
        *,
        run_id: uuid.UUID,
        request: PreparePublicationRequest,
        actor_id: str,
    ) -> PublicationMutationResponse:
        if request.profile_id != "mhm-wikidata" or request.profile_version not in {"1", "1-nodes"}:
            raise PublicationSourceError("The requested publication profile is unsupported")
        if request.target == "live" and request.source.projection_source != "canonical":
            raise PublicationSourceError("Live publication requires the canonical source")
        snapshot = await self._projection_source.current_snapshot(
            run_id=run_id,
            source=request.source.projection_source,
            approved_only=request.source.approved_only,
        )
        target = _target_ref(request.target)
        module = self._module(target=target, actor_id=actor_id)
        operation = await module.prepare(
            PrepareRequest(
                run_id=str(run_id),
                source_snapshot=snapshot,
                profile=ProfileRef(name=request.profile_id, version=request.profile_version),
                target=target,
                actor_id=actor_id,
                idempotency_key=str(uuid.uuid4()),
            )
        )
        await self._session.commit()
        summary = await module.read(SummaryQuery(operation.publication_id))
        return PublicationMutationResponse(
            publication=await self._summary_view(summary),
        )

    async def advance(
        self,
        *,
        run_id: uuid.UUID,
        publication_id: str,
        request: AdvancePublicationRequest,
        actor_id: str,
    ) -> PublicationMutationResponse:
        publication_row = await self._publication_for_run(run_id, publication_id)
        target = TargetRef(
            site=publication_row.target_site,
            environment=publication_row.target_environment,
        )
        module = self._module(target=target, actor_id=actor_id)
        command = request.command
        if isinstance(command, ResumePublicationCommand):
            operation_id = await self._queue_resume(
                publication_row,
                command.execution_id,
            )
        else:
            operation = await module.advance(
                publication_id,
                _core_command(command, actor_id, _credential_ref(target, actor_id)),
            )
            operation_id = (
                operation.resource_id
                if isinstance(command, (PublishPublicationCommand, CancelPublicationCommand))
                else operation.operation_id
            )
        await self._session.commit()
        summary = await module.read(SummaryQuery(publication_id))
        publication_view = await self._summary_view(summary)
        return PublicationMutationResponse(
            publication=publication_view,
            operation=_operation_view(command.type, operation_id, publication_view),
        )

    async def read(
        self,
        *,
        run_id: uuid.UUID,
        publication_id: str,
        query: PublicationReadQuery,
        actor_id: str,
    ) -> (
        PublicationSummaryRead
        | PublicationOperationRead
        | PublicationEntityPage
        | PublicationAuditPage
    ):
        publication_row = await self._publication_for_run(run_id, publication_id)
        target = TargetRef(
            site=publication_row.target_site,
            environment=publication_row.target_environment,
        )
        module = self._module(target=target, actor_id=actor_id)
        if isinstance(query, PublicationOperationQuery):
            result = await module.read(SummaryQuery(publication_id))
            publication_view = await self._summary_view(
                cast(CorePublicationSummary, result)
            )
            execution = publication_view.execution
            if execution is None or execution.execution_id != query.operation_id:
                raise PublicationNotFoundError(query.operation_id)
            return PublicationOperationRead(
                publication=publication_view,
                operation=_operation_view(
                    "publish",
                    execution.execution_id,
                    publication_view,
                ),
            )
        if isinstance(query, PublicationEntitiesQuery):
            if query.entity_kind or query.review_status or query.query:
                raise UnsupportedPublicationActionError(
                    "Publication entity filters are not implemented"
                )
            result = await module.read(
                EntityPageQuery(
                    publication_id=publication_id,
                    release_id=query.release_id,
                    cursor=query.cursor,
                    limit=query.limit,
                )
            )
            return await self._entity_page_view(
                cast(EntityPage, result),
                publication_row,
            )
        if query.type == "audit":
            result = await module.read(
                AuditQuery(
                    publication_id=publication_id,
                    cursor=query.cursor,
                    limit=query.limit,
                )
            )
            return await self._audit_page_view(cast(AuditPage, result), publication_id)
        result = await module.read(SummaryQuery(publication_id))
        return PublicationSummaryRead(
            publication=await self._summary_view(cast(CorePublicationSummary, result))
        )

    async def execute(
        self,
        *,
        run_id: uuid.UUID,
        publication_id: str,
        execution_id: str,
        actor_id: str,
        worker_id: str,
    ) -> PublicationSummary:
        """Run one queued Execution from a background worker only."""
        publication_row = await self._publication_for_run(run_id, publication_id)
        target = TargetRef(
            site=publication_row.target_site,
            environment=publication_row.target_environment,
        )
        module = self._module(target=target, actor_id=actor_id)
        await module.advance(
            publication_id,
            ResumeCommand(
                execution_id=execution_id,
                credential_ref=_credential_ref(target, actor_id),
                actor_id=worker_id,
                idempotency_key=worker_id,
            ),
        )
        await self._session.commit()
        summary = await module.read(SummaryQuery(publication_id))
        return await self._summary_view(cast(CorePublicationSummary, summary))

    def _module(self, *, target: TargetRef, actor_id: str) -> PublicationModule:
        return PublicationModule(
            projection_source=self._projection_source,
            repository=SqlAlchemyPublicationRepository(self._session),
            gateway=self._gateway_factory(target=target, actor_id=actor_id),
            clock=self._clock,
        )

    async def _publication_for_run(
        self,
        run_id: uuid.UUID,
        publication_id: str,
    ) -> PublicationRow:
        try:
            publication_uuid = uuid.UUID(publication_id)
        except ValueError as exc:
            raise PublicationNotFoundError(publication_id) from exc
        row = await self._session.get(PublicationRow, publication_uuid)
        if row is None or row.run_id != run_id:
            raise PublicationNotFoundError(publication_id)
        return row

    async def _queue_resume(
        self,
        publication: PublicationRow,
        execution_id: str,
    ) -> str:
        if publication.latest_execution_id is None:
            raise PublicationNotFoundError(execution_id)
        try:
            execution_uuid = uuid.UUID(execution_id)
        except ValueError as exc:
            raise PublicationNotFoundError(execution_id) from exc
        if execution_uuid != publication.latest_execution_id:
            raise PublicationNotFoundError(execution_id)
        execution = await self._session.get(PublicationExecution, execution_uuid)
        if execution is None or execution.publication_id != publication.id:
            raise PublicationNotFoundError(execution_id)
        if execution.status == "cancelled":
            raise UnsupportedPublicationActionError(
                "A cancelled Execution cannot resume"
            )
        if execution.status == "succeeded":
            return execution_id
        execution.status = "queued"
        publication.state = "publishing"
        await self._session.flush()
        return execution_id

    async def _summary_view(
        self,
        summary: CorePublicationSummary,
    ) -> PublicationSummary:
        publication = await self._session.get(PublicationRow, uuid.UUID(summary.publication_id))
        release = await self._session.get(PublicationRelease, uuid.UUID(summary.release_id))
        if publication is None or release is None:
            raise PublicationNotFoundError(summary.publication_id)
        severity_rows = (
            await self._session.execute(
                select(PublicationFinding.severity, func.count())
                .where(PublicationFinding.release_id == release.id)
                .group_by(PublicationFinding.severity)
            )
        ).all()
        finding_counts = {"error": 0, "warning": 0, "info": 0}
        for severity, count in severity_rows:
            key = "error" if severity in {"blocker", "error"} else severity
            if key in finding_counts:
                finding_counts[key] += count
        approval = (
            await self._session.get(
                PublicationApprovalSet,
                publication.latest_approval_set_id,
            )
            if publication.latest_approval_set_id is not None
            else None
        )
        plan = (
            await self._session.get(PublicationPlan, publication.latest_plan_id)
            if publication.latest_plan_id is not None
            else None
        )
        receipt = (
            (
                await self._session.execute(
                    select(PublicationDryRunReceipt).where(
                        PublicationDryRunReceipt.plan_id == plan.id
                    )
                )
            ).scalar_one_or_none()
            if plan is not None
            else None
        )
        execution = (
            await self._session.get(PublicationExecution, publication.latest_execution_id)
            if publication.latest_execution_id is not None
            else None
        )
        pending_count = max(
            0,
            summary.entity_count - summary.approved_count - summary.rejected_count,
        )
        approval_view = None
        if approval is not None and summary.approval_digest is not None:
            approval_status = "pending"
            if pending_count == 0 and summary.rejected_count == 0:
                approval_status = "approved"
            elif pending_count == 0 and summary.approved_count == 0:
                approval_status = "rejected"
            approval_view = ApprovalSetSummary(
                approval_set_id=str(approval.id),
                approval_digest=summary.approval_digest,
                release_id=str(approval.release_id),
                release_digest=approval.release_digest or "",
                status=approval_status,
                approved_count=summary.approved_count,
                rejected_count=summary.rejected_count,
                pending_count=pending_count,
                created_at=_aware(approval.created_at),
            )
        now = self._clock()
        plan_view = None
        receipt_view = None
        if plan is not None and summary.plan_digest is not None:
            expired = receipt is not None and _aware(receipt.expires_at) <= now
            plan_status = "executed" if execution is not None else "ready"
            if summary.plan_blocked_count:
                plan_status = "blocked"
            elif expired:
                plan_status = "expired"
            plan_view = PlanSummary(
                plan_id=str(plan.id),
                plan_digest=summary.plan_digest,
                release_id=str(plan.release_id),
                release_digest=plan.release_digest or "",
                approval_set_id=str(plan.approval_set_id),
                status=plan_status,
                expires_at=_aware(receipt.expires_at) if receipt else None,
                action_counts={
                    "create": summary.plan_create_count,
                    "update": summary.plan_update_count,
                    "skip": summary.plan_skip_count,
                    "blocked": summary.plan_blocked_count,
                },
            )
            if receipt is not None:
                receipt_status = "valid"
                if not receipt.passed:
                    receipt_status = "failed"
                elif expired:
                    receipt_status = "stale"
                receipt_view = DryRunReceiptSummary(
                    dry_run_receipt_id=str(receipt.id),
                    receipt_digest=receipt.receipt_digest,
                    plan_id=str(plan.id),
                    plan_digest=summary.plan_digest,
                    status=receipt_status,
                    checked_at=_aware(receipt.created_at),
                    expires_at=_aware(receipt.expires_at),
                )
        execution_view = None
        if execution is not None:
            failed = (
                summary.execution_pre_send_retryable_count
                + summary.execution_outcome_unknown_count
                + summary.execution_blocked_count
            )
            processed = min(
                execution.total_count,
                summary.execution_succeeded_count + failed + summary.plan_skip_count,
            )
            terminal = execution.status in {"succeeded", "failed", "cancelled"}
            execution_view = ExecutionSummary(
                execution_id=str(execution.id),
                plan_id=str(execution.plan_id),
                status=cast("str", execution.status),
                processed=processed,
                total=execution.total_count,
                succeeded=summary.execution_succeeded_count,
                failed=failed,
                skipped=summary.plan_skip_count,
                current_entity_label=None,
                started_at=_aware(execution.created_at),
                finished_at=_aware(execution.updated_at) if terminal else None,
            )
        status = _publication_status(summary, execution)
        return PublicationSummary(
            publication_id=summary.publication_id,
            run_id=summary.run_id,
            profile_id=publication.profile_name,
            profile_version=publication.profile_version,
            target="live" if publication.target_environment == "production" else "test",
            status=status,
            source_current=summary.source_current,
            current_release=ReleaseSummary(
                release_id=summary.release_id,
                release_digest=summary.release_digest,
                revision=1,
                created_at=_aware(release.created_at),
                entity_count=summary.entity_count,
                finding_counts=finding_counts,
            ),
            approval_set=approval_view,
            plan=plan_view,
            dry_run_receipt=receipt_view,
            execution=execution_view,
        )

    async def _entity_page_view(
        self,
        page: EntityPage,
        publication: PublicationRow,
    ) -> PublicationEntityPage:
        release_id = (
            uuid.UUID(page.items[0].release_id)
            if page.items
            else publication.latest_release_id
        )
        release = await self._session.get(PublicationRelease, release_id)
        if release is None or release.release_digest is None:
            raise PublicationNotFoundError(str(publication.id))
        entity_keys = [entity.entity_key for entity in page.items]
        decisions: dict[str, str] = {}
        if publication.latest_approval_set_id is not None and entity_keys:
            rows = (
                await self._session.execute(
                    select(
                        PublicationApprovalDecision.entity_key,
                        PublicationApprovalDecision.decision,
                    ).where(
                        PublicationApprovalDecision.approval_set_id
                        == publication.latest_approval_set_id,
                        PublicationApprovalDecision.entity_key.in_(entity_keys),
                    )
                )
            ).all()
            decisions = dict(rows)
        finding_rows = []
        if entity_keys:
            finding_rows = (
                (
                    await self._session.execute(
                        select(PublicationFinding).where(
                            PublicationFinding.release_id == release.id,
                            PublicationFinding.entity_key.in_(entity_keys),
                        )
                    )
                )
                .scalars()
                .all()
            )
        findings_by_key: dict[str, list[PublicationEntityFinding]] = {}
        for finding in finding_rows:
            if finding.entity_key is None:
                continue
            severity = "error" if finding.severity in {"blocker", "error"} else finding.severity
            findings_by_key.setdefault(finding.entity_key, []).append(
                PublicationEntityFinding(
                    finding_id=str(finding.id),
                    code=finding.code,
                    severity=cast("str", severity),
                    message=finding.message,
                    gate=severity == "error",
                )
            )
        return PublicationEntityPage(
            release_id=str(release.id),
            release_digest=release.release_digest,
            items=[
                _entity_view(
                    entity,
                    decisions.get(entity.entity_key, "pending"),
                    findings_by_key.get(entity.entity_key, []),
                )
                for entity in page.items
            ],
            next_cursor=page.next_cursor,
            total=release.entity_count,
        )

    async def _audit_page_view(
        self,
        page: AuditPage,
        publication_id: str,
    ) -> PublicationAuditPage:
        sequences = [entry.sequence for entry in page.items]
        rows = []
        if sequences:
            rows = (
                (
                    await self._session.execute(
                        select(PublicationJournalEvent).where(
                            PublicationJournalEvent.publication_id == uuid.UUID(publication_id),
                            PublicationJournalEvent.sequence.in_(sequences),
                        )
                    )
                )
                .scalars()
                .all()
            )
        rows_by_sequence = {row.sequence: row for row in rows}
        total = (
            await self._session.execute(
                select(func.count()).select_from(PublicationJournalEvent).where(
                    PublicationJournalEvent.publication_id == uuid.UUID(publication_id)
                )
            )
        ).scalar_one()
        events: list[PublicationAuditEvent] = []
        for entry in page.items:
            row = rows_by_sequence.get(entry.sequence)
            occurred_at = _aware(row.created_at) if row is not None else self._clock()
            events.append(
                PublicationAuditEvent(
                    event_id=f"journal:{publication_id}:{entry.sequence}",
                    sequence=entry.sequence,
                    event_type=f"write_intent.{entry.state}",
                    occurred_at=occurred_at,
                    actor_label=None,
                    release_id=None,
                    entity_id=entry.entity_key,
                    message=entry.detail or f"The Write Intent state is {entry.state}.",
                    details={
                        "execution_id": entry.execution_id,
                        "intent_id": entry.intent_id,
                        "state": entry.state,
                    },
                )
            )
        return PublicationAuditPage(
            publication_id=publication_id,
            items=events,
            next_cursor=page.next_cursor,
            total=total,
        )


def _target_ref(target: str) -> TargetRef:
    if target == "live":
        return TargetRef(site="www.wikidata.org", environment="production")
    return TargetRef(site="test.wikidata.org", environment="test")


def _credential_ref(target: TargetRef, actor_id: str) -> str:
    return f"wikidata:{target.environment}:{actor_id}"


def _core_command(command, actor_id: str, credential_ref: str):
    idempotency_key = str(uuid.uuid4())
    if isinstance(command, ReviewPublicationCommand):
        selection = (
            EntitySelection.entities(*command.selection.entity_keys)
            if isinstance(command.selection, EntityKeysSelection)
            else EntitySelection.all()
        )
        return ReviewCommand(
            release_id=command.release_id,
            expected_release_digest=command.expected_release_digest,
            selection=selection,
            decision=command.decision,
            actor_id=actor_id,
            reason=command.reason,
            idempotency_key=idempotency_key,
        )
    if isinstance(command, DryRunPublicationCommand):
        return DryRunCommand(
            approval_set_id=command.approval_set_id,
            expected_approval_digest=command.expected_approval_digest,
            credential_ref=credential_ref,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
    if isinstance(command, PublishPublicationCommand):
        return PublishCommand(
            plan_id=command.plan_id,
            dry_run_receipt_id=command.dry_run_receipt_id,
            expected_receipt_digest=command.expected_receipt_digest,
            credential_ref=credential_ref,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
    if isinstance(command, ResumePublicationCommand):
        return ResumeCommand(
            execution_id=command.execution_id,
            credential_ref=credential_ref,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
    if isinstance(command, CancelPublicationCommand):
        if not command.operation_id:
            raise UnsupportedPublicationActionError(
                "Cancel requires the current Execution identifier"
            )
        return CancelCommand(
            execution_id=command.operation_id,
            actor_id=actor_id,
            reason=command.reason or "The curator cancelled the Execution.",
            idempotency_key=idempotency_key,
        )
    raise UnsupportedPublicationActionError("The publication command is unsupported")


def _operation_view(
    command: str,
    operation_id: str,
    publication: PublicationSummary,
) -> PublicationOperation:
    execution = publication.execution
    if execution is not None and execution.execution_id == operation_id:
        status = execution.status
        if status == "paused":
            status = "succeeded"
        return PublicationOperation(
            operation_id=operation_id,
            command=cast("str", command),
            status=cast("str", status),
            progress=execution,
            error=None,
        )
    return PublicationOperation(
        operation_id=operation_id,
        command=cast("str", command),
        status="succeeded",
        progress=None,
        error=None,
    )


def _publication_status(
    summary: CorePublicationSummary,
    execution: PublicationExecution | None,
) -> str:
    if execution is not None and execution.status == "paused":
        return "paused"
    if execution is not None and execution.status == "cancelled":
        return "cancelled"
    return {
        "prepared": "ready_for_review",
        "reviewed": "reviewed",
        "dry_run_ready": "dry_run_ready",
        "publishable": "dry_run_ready",
        "publishing": "publishing",
        "completed": "completed",
        "failed": "failed",
    }[summary.state]


def _entity_view(
    entity: CorePublicationEntity,
    review_status: str,
    findings: list[PublicationEntityFinding],
) -> PublicationEntity:
    document = thaw_json(entity.document)
    item = document if isinstance(document, dict) else {}
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    descriptions = (
        item.get("descriptions") if isinstance(item.get("descriptions"), dict) else {}
    )
    label = str(labels.get("en") or labels.get("he") or entity.entity_key)
    description_value = descriptions.get("en") or descriptions.get("he")
    target_qid = str(item.get("existing_qid") or "").strip() or None
    statements = item.get("statements") if isinstance(item.get("statements"), list) else []
    blocked = any(finding.gate for finding in findings)
    return PublicationEntity(
        entity_id=entity.entity_key,
        entity_digest=entity.entity_digest,
        entity_kind=entity.entity_type,
        label=label,
        description=str(description_value) if description_value else None,
        target_qid=target_qid,
        statement_count=len(statements),
        review_status=cast("str", review_status),
        proposed_action="blocked" if blocked else "update" if target_qid else "create",
        findings=findings,
        deferred_statements=item.get("deferred_statements") or [],
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
