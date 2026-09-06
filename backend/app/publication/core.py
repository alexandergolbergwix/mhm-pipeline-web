"""Deep publication module with prepare, advance, and read entry points."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Awaitable, AsyncIterator, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, cast

from app.publication.digests import (
    CanonicalSequenceDigest,
    ReleaseDigestAccumulator,
    canonical_digest,
    entity_digest,
    freeze_json,
)
from app.publication.gateway import (
    GatewayRecoveryRequest,
    GatewayWriteRequest,
    WikidataGateway,
    WikidataGatewaySession,
    WriteOutcome,
)
from app.publication.repository import (
    ApprovalDecisionRecord,
    ApprovalSetRecord,
    DryRunReceiptRecord,
    ExecutionActionRecord,
    ExecutionRecord,
    PlanActionRecord,
    PlanRecord,
    PublicationRecord,
    PublicationRepository,
    ReleaseRecord,
    WriteIntentRecord,
)
from app.publication.types import (
    AdvanceCommand,
    AuditPage,
    AuditQuery,
    CancelCommand,
    DryRunCommand,
    EntityPage,
    EntityPageQuery,
    OperationRef,
    PrepareRequest,
    ProfileRef,
    PublicationEntity,
    PublicationEntityInput,
    PublicationSummary,
    PublishCommand,
    ReadQuery,
    ReadResult,
    ResumeCommand,
    ReviewCommand,
    SourceSnapshotRef,
)


class ProjectionSource(Protocol):
    """Project an immutable source snapshot without network access."""

    def project(
        self,
        source_snapshot: SourceSnapshotRef,
        profile: ProfileRef,
    ) -> AsyncIterator[tuple[PublicationEntityInput, ...]]: ...

    async def is_current(
        self,
        run_id: str,
        source_snapshot: SourceSnapshotRef,
    ) -> bool: ...


class UnsupportedCommandError(ValueError):
    """The caller supplied a command that the module does not support."""


class InvalidCursorError(ValueError):
    """The cursor does not belong to the requested release."""


class StaleDigestError(ValueError):
    """The command refers to content that is no longer current."""


class EmptySelectionError(ValueError):
    """The review selection did not match a release entity."""


class BlockingFindingsError(ValueError):
    """The Release has findings that prohibit approval."""


class CancelledExecutionError(ValueError):
    """The caller tried to resume a cancelled Execution."""


class PublicationModule:
    """Coordinate publication state behind three stable entry points."""

    def __init__(
        self,
        *,
        projection_source: ProjectionSource,
        repository: PublicationRepository,
        gateway: WikidataGateway,
        clock: Callable[[], datetime] | None = None,
        dry_run_progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> None:
        self._projection_source = projection_source
        self._repository = repository
        self._gateway = gateway
        self._clock = clock or (lambda: datetime.now(UTC))
        self._dry_run_progress = dry_run_progress

    async def prepare(self, request: PrepareRequest) -> OperationRef:
        prior = await self._repository.find_prepared(
            run_id=request.run_id,
            idempotency_key=request.idempotency_key,
        )
        if prior is not None:
            publication, release = prior
            return OperationRef(
                operation_id=str(uuid.uuid4()),
                publication_id=publication.publication_id,
                resource_type="release",
                resource_id=release.release_id,
            )

        release_id = str(uuid.uuid4())
        publication_id = str(uuid.uuid4())
        publication = PublicationRecord(
            publication_id=publication_id,
            run_id=request.run_id,
            source_snapshot=request.source_snapshot,
            profile=request.profile,
            target=request.target,
            state="prepared",
            latest_release_id=release_id,
            idempotency_key=request.idempotency_key,
        )
        await self._repository.begin_prepare(publication, release_id)
        accumulator = ReleaseDigestAccumulator(
            {
                "source_snapshot": {
                    "snapshot_id": request.source_snapshot.snapshot_id,
                    "revision": request.source_snapshot.revision,
                    "digest": request.source_snapshot.digest,
                },
                "profile": {
                    "name": request.profile.name,
                    "version": request.profile.version,
                },
                "target": {
                    "site": request.target.site,
                    "environment": request.target.environment,
                },
            }
        )
        entity_count = 0
        previous_key: str | None = None
        async for input_page in self._projection_source.project(
            request.source_snapshot,
            request.profile,
        ):
            if len(input_page) > 1_000:
                raise ValueError("A projection page cannot contain more than 1000 entities")
            entities: list[PublicationEntity] = []
            for item in input_page:
                if previous_key is not None and item.entity_key <= previous_key:
                    raise ValueError("The projection source must return unique canonical keys")
                digest = entity_digest(item)
                entity = PublicationEntity(
                    release_id=release_id,
                    entity_key=item.entity_key,
                    entity_type=item.entity_type,
                    entity_digest=digest,
                    document=freeze_json(item.document),
                    evidence_refs=tuple(sorted(item.evidence_refs)),
                    identity_assertions=tuple(sorted(item.identity_assertions)),
                    local_references=tuple(sorted(item.local_references)),
                )
                entities.append(entity)
                accumulator.add_entity(item.entity_key, digest)
                previous_key = item.entity_key
                entity_count += 1
            await self._repository.add_release_entities(release_id, tuple(entities))
        finding_count = await self._repository.validate_release(release_id)
        release = ReleaseRecord(
            release_id=release_id,
            publication_id=publication_id,
            release_digest=accumulator.hexdigest(),
            entity_count=entity_count,
            finding_count=finding_count,
        )
        await self._repository.complete_prepare(publication, release)
        return OperationRef(
            operation_id=str(uuid.uuid4()),
            publication_id=publication_id,
            resource_type="release",
            resource_id=release_id,
        )

    async def advance(
        self,
        publication_id: str,
        command: AdvanceCommand,
    ) -> OperationRef:
        if isinstance(command, ReviewCommand):
            return await self._review(publication_id, command)
        if isinstance(command, DryRunCommand):
            return await self._dry_run(publication_id, command)
        if isinstance(command, PublishCommand):
            return await self._publish(publication_id, command)
        if isinstance(command, ResumeCommand):
            return await self._resume(publication_id, command)
        if isinstance(command, CancelCommand):
            return await self._cancel(publication_id, command)
        raise UnsupportedCommandError("The advance command is not supported")

    async def read(self, query: ReadQuery) -> ReadResult:
        publication = await self._repository.get_publication(query.publication_id)
        if isinstance(query, AuditQuery):
            if not 1 <= query.limit <= 500:
                raise ValueError("The audit page limit must be from 1 through 500")
            after = self._decode_audit_cursor(query.cursor, query.publication_id)
            items, has_more = await self._repository.page_audit(
                query.publication_id,
                after=after,
                limit=query.limit,
            )
            next_cursor = (
                self._encode_audit_cursor(query.publication_id, items[-1].sequence)
                if has_more and items
                else None
            )
            return AuditPage(items=items, next_cursor=next_cursor)
        if isinstance(query, EntityPageQuery):
            if not 1 <= query.limit <= 500:
                raise ValueError("The entity page limit must be from 1 through 500")
            release = await self._repository.get_release(
                publication.publication_id,
                query.release_id,
            )
            start_key = self._decode_cursor(query.cursor, release.release_id)
            items, has_more = await self._repository.page_release_entities(
                release.release_id,
                after=start_key,
                limit=query.limit,
            )
            next_cursor = (
                self._encode_cursor(release.release_id, items[-1].entity_key)
                if has_more and items
                else None
            )
            return EntityPage(items=items, next_cursor=next_cursor)

        release = await self._repository.get_release(
            publication.publication_id,
            publication.latest_release_id,
        )
        approval_set = (
            await self._repository.get_approval_set(publication.latest_approval_set_id)
            if publication.latest_approval_set_id is not None
            else None
        )
        plan = (
            await self._repository.get_plan(publication.latest_plan_id)
            if publication.latest_plan_id is not None
            else None
        )
        source_current = await self._projection_source.is_current(
            publication.run_id,
            publication.source_snapshot,
        )
        execution = (
            await self._repository.get_execution(publication.latest_execution_id)
            if publication.latest_execution_id is not None
            else None
        )
        return PublicationSummary(
            publication_id=publication.publication_id,
            run_id=publication.run_id,
            state=cast(
                "Literal['prepared', 'reviewed', 'dry_run_ready', 'publishable', "
                "'publishing', 'completed', 'failed', 'cancelled']",
                publication.state,
            ),
            release_id=release.release_id,
            release_digest=release.release_digest,
            entity_count=release.entity_count,
            finding_count=release.finding_count,
            source_current=source_current,
            approval_set_id=(approval_set.approval_set_id if approval_set else None),
            approval_digest=(approval_set.approval_digest if approval_set else None),
            approved_count=(approval_set.approved_count if approval_set else 0),
            rejected_count=(approval_set.rejected_count if approval_set else 0),
            plan_id=(plan.plan_id if plan else None),
            plan_digest=(plan.plan_digest if plan else None),
            dry_run_receipt_id=(plan.receipt.receipt_id if plan else None),
            dry_run_receipt_digest=(plan.receipt.receipt_digest if plan else None),
            dry_run_expires_at=(plan.receipt.expires_at if plan else None),
            plan_create_count=(plan.create_count if plan else 0),
            plan_update_count=(plan.update_count if plan else 0),
            plan_skip_count=(plan.skip_count if plan else 0),
            plan_blocked_count=(plan.blocked_count if plan else 0),
            execution_id=(execution.execution_id if execution else None),
            execution_status=(execution.status if execution else None),
            execution_succeeded_count=(execution.succeeded_count if execution else 0),
            execution_pre_send_retryable_count=(
                execution.pre_send_retryable_count if execution else 0
            ),
            execution_outcome_unknown_count=(execution.outcome_unknown_count if execution else 0),
            execution_blocked_count=(execution.blocked_count if execution else 0),
        )

    async def _review(
        self,
        publication_id: str,
        command: ReviewCommand,
    ) -> OperationRef:
        prior = await self._repository.find_approval(
            publication_id=publication_id,
            idempotency_key=command.idempotency_key,
        )
        if prior is not None:
            return OperationRef(
                operation_id=str(uuid.uuid4()),
                publication_id=publication_id,
                resource_type="approval_set",
                resource_id=prior.approval_set_id,
            )
        publication = await self._repository.get_publication(publication_id)
        if command.release_id != publication.latest_release_id:
            raise StaleDigestError("The review does not target the current Release")
        release = await self._repository.get_release(publication_id, command.release_id)
        if command.expected_release_digest != release.release_digest:
            raise StaleDigestError("The Release digest changed before review")
        if command.decision == "approve" and release.finding_count:
            raise BlockingFindingsError(
                "The Release has cross-entity findings that require correction"
            )

        approval_set_id = str(uuid.uuid4())
        await self._repository.begin_approval(approval_set_id)
        selected_keys = set(command.selection.entity_keys)
        digest = CanonicalSequenceDigest(
            "wikidata-publication-approval-set-v1",
            {
                "release_id": release.release_id,
                "release_digest": release.release_digest,
                "decision": command.decision,
            },
        )
        reviewed_count = 0
        after: str | None = None
        while True:
            entities, has_more = await self._repository.page_release_entities(
                release.release_id,
                after=after,
                limit=1_000,
            )
            decisions = tuple(
                ApprovalDecisionRecord(
                    approval_set_id=approval_set_id,
                    entity_key=entity.entity_key,
                    entity_digest=entity.entity_digest,
                    decision=command.decision,
                )
                for entity in entities
                if command.selection.mode == "all" or entity.entity_key in selected_keys
            )
            for decision in decisions:
                digest.add(
                    {
                        "entity_key": decision.entity_key,
                        "entity_digest": decision.entity_digest,
                        "decision": decision.decision,
                    }
                )
                selected_keys.discard(decision.entity_key)
            await self._repository.add_approval_decisions(approval_set_id, decisions)
            reviewed_count += len(decisions)
            if not has_more:
                break
            after = entities[-1].entity_key

        if reviewed_count == 0 or selected_keys:
            raise EmptySelectionError("The review selection does not match the Release")
        approval_set = ApprovalSetRecord(
            approval_set_id=approval_set_id,
            publication_id=publication_id,
            release_id=release.release_id,
            release_digest=release.release_digest,
            approval_digest=digest.hexdigest(),
            actor_id=command.actor_id,
            reason=command.reason,
            idempotency_key=command.idempotency_key,
            approved_count=reviewed_count if command.decision == "approve" else 0,
            rejected_count=reviewed_count if command.decision == "reject" else 0,
        )
        reviewed_publication = replace(
            publication,
            state="reviewed",
            latest_approval_set_id=approval_set_id,
        )
        await self._repository.complete_approval(reviewed_publication, approval_set)
        return OperationRef(
            operation_id=str(uuid.uuid4()),
            publication_id=publication_id,
            resource_type="approval_set",
            resource_id=approval_set_id,
        )

    async def _dry_run(
        self,
        publication_id: str,
        command: DryRunCommand,
    ) -> OperationRef:
        prior = await self._repository.find_plan(
            publication_id=publication_id,
            idempotency_key=command.idempotency_key,
        )
        if prior is not None:
            return OperationRef(
                operation_id=str(uuid.uuid4()),
                publication_id=publication_id,
                resource_type="plan",
                resource_id=prior.plan_id,
            )
        publication = await self._repository.get_publication(publication_id)
        if not await self._projection_source.is_current(
            publication.run_id,
            publication.source_snapshot,
        ):
            raise StaleDigestError("The Source Snapshot is no longer current")
        if command.approval_set_id != publication.latest_approval_set_id:
            raise StaleDigestError("The dry-run does not target the current Approval Set")
        approval_set = await self._repository.get_approval_set(command.approval_set_id)
        if command.expected_approval_digest != approval_set.approval_digest:
            raise StaleDigestError("The Approval Set digest changed before the dry-run")
        release = await self._repository.get_release(
            publication_id,
            approval_set.release_id,
        )
        if approval_set.approved_count != release.entity_count:
            raise ValueError("Every Release entity must have approval before a dry-run")
        if command.receipt_ttl_seconds < 1:
            raise ValueError("The dry-run receipt TTL must be positive")

        if self._dry_run_progress is not None:
            await self._dry_run_progress(0, release.entity_count)
        plan_id = str(uuid.uuid4())
        await self._repository.begin_plan(plan_id)
        await self._repository.checkpoint()
        session = await self._gateway.open(command.credential_ref, publication.target)
        consents = {consent.entity_key: consent for consent in command.foreign_qid_consents}
        if len(consents) != len(command.foreign_qid_consents):
            raise ValueError("A dry-run cannot contain duplicate foreign QID consents")
        plan_digest = CanonicalSequenceDigest(
            "wikidata-publication-plan-v1",
            {
                "release_id": release.release_id,
                "release_digest": release.release_digest,
                "approval_set_id": approval_set.approval_set_id,
                "approval_digest": approval_set.approval_digest,
            },
        )
        counts = {"create": 0, "update": 0, "skip": 0, "block": 0}
        after: str | None = None
        while True:
            entities, has_more = await self._repository.page_approved_entities(
                approval_set.approval_set_id,
                after=after,
                limit=50,
            )
            await self._repository.checkpoint()
            observations = await session.reconcile_batch(entities)
            by_key = {observation.entity_key: observation for observation in observations}
            actions: list[PlanActionRecord] = []
            for entity in entities:
                observation = by_key.get(entity.entity_key)
                if observation is None:
                    action_name = "block"
                    observation_status = "unknown"
                    target_qid = None
                    target_fingerprint = None
                    target_revision = None
                    allow_foreign_update = False
                    detail = "The gateway omitted the entity observation"
                else:
                    consent = consents.get(entity.entity_key)
                    consent_matches = (
                        observation.status == "present_foreign"
                        and consent is not None
                        and consent.qid == observation.qid
                        and consent.remote_revision == observation.remote_revision
                        and consent.entity_digest == entity.entity_digest
                    )
                    action_name = (
                        "update"
                        if consent_matches
                        else {
                            "absent": "create",
                            "present_owned": "update",
                            "present_foreign": "block",
                            "unknown": "block",
                        }[observation.status]
                    )
                    observation_status = observation.status
                    target_qid = observation.qid
                    target_fingerprint = observation.fingerprint
                    target_revision = observation.remote_revision
                    allow_foreign_update = consent_matches
                    detail = (
                        observation.detail
                        if observation.status != "present_foreign" or consent_matches
                        else (
                            "Foreign QID consent does not match the QID, revision, "
                            "and entity digest"
                        )
                    )
                action = PlanActionRecord(
                    plan_id=plan_id,
                    entity_key=entity.entity_key,
                    entity_digest=entity.entity_digest,
                    action=cast(
                        "Literal['create', 'update', 'skip', 'block']",
                        action_name,
                    ),
                    observation_status=cast(
                        "Literal['absent', 'present_owned', 'present_foreign', 'unknown']",
                        observation_status,
                    ),
                    target_qid=target_qid,
                    target_fingerprint=target_fingerprint,
                    target_revision=target_revision,
                    allow_foreign_update=allow_foreign_update,
                    detail=detail,
                )
                actions.append(action)
                counts[action.action] += 1
                plan_digest.add(
                    {
                        "entity_key": action.entity_key,
                        "entity_digest": action.entity_digest,
                        "action": action.action,
                        "observation_status": action.observation_status,
                        "target_qid": action.target_qid,
                        "target_fingerprint": action.target_fingerprint,
                        "target_revision": action.target_revision,
                        "allow_foreign_update": action.allow_foreign_update,
                    }
                )
            await self._repository.add_plan_actions(plan_id, tuple(actions))
            if self._dry_run_progress is not None:
                await self._repository.checkpoint()
                await self._dry_run_progress(sum(counts.values()), release.entity_count)
            if not has_more:
                break
            after = entities[-1].entity_key

        now = self._clock()
        expires_at = now + timedelta(seconds=command.receipt_ttl_seconds)
        plan_digest_value = plan_digest.hexdigest()
        receipt_id = str(uuid.uuid4())
        passed = counts["block"] == 0
        receipt_digest = canonical_digest(
            {
                "schema": "wikidata-publication-dry-run-receipt-v1",
                "plan_id": plan_id,
                "plan_digest": plan_digest_value,
                "release_digest": release.release_digest,
                "approval_digest": approval_set.approval_digest,
                "passed": passed,
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
        )
        receipt = DryRunReceiptRecord(
            receipt_id=receipt_id,
            plan_id=plan_id,
            release_digest=release.release_digest,
            approval_digest=approval_set.approval_digest,
            plan_digest=plan_digest_value,
            receipt_digest=receipt_digest,
            passed=passed,
            created_at=now,
            expires_at=expires_at,
        )
        plan = PlanRecord(
            plan_id=plan_id,
            publication_id=publication_id,
            release_id=release.release_id,
            release_digest=release.release_digest,
            approval_set_id=approval_set.approval_set_id,
            approval_digest=approval_set.approval_digest,
            plan_digest=plan_digest_value,
            idempotency_key=command.idempotency_key,
            create_count=counts["create"],
            update_count=counts["update"],
            skip_count=counts["skip"],
            blocked_count=counts["block"],
            receipt=receipt,
        )
        planned_publication = replace(
            publication,
            state="publishable" if passed else "dry_run_ready",
            latest_plan_id=plan_id,
        )
        await self._repository.complete_plan(planned_publication, plan)
        return OperationRef(
            operation_id=str(uuid.uuid4()),
            publication_id=publication_id,
            resource_type="plan",
            resource_id=plan_id,
        )

    async def _publish(
        self,
        publication_id: str,
        command: PublishCommand,
    ) -> OperationRef:
        prior = await self._repository.find_execution(
            publication_id=publication_id,
            idempotency_key=command.idempotency_key,
        )
        if prior is not None:
            return OperationRef(
                operation_id=str(uuid.uuid4()),
                publication_id=publication_id,
                resource_type="execution",
                resource_id=prior.execution_id,
            )
        publication = await self._repository.get_publication(publication_id)
        if not await self._projection_source.is_current(
            publication.run_id,
            publication.source_snapshot,
        ):
            raise StaleDigestError("The Source Snapshot is no longer current")
        if command.plan_id != publication.latest_plan_id:
            raise StaleDigestError("The publish command does not target the current Plan")
        plan = await self._repository.get_plan(command.plan_id)
        receipt = plan.receipt
        if command.dry_run_receipt_id != receipt.receipt_id:
            raise StaleDigestError("The Dry-run Receipt is not current")
        if command.expected_receipt_digest != receipt.receipt_digest:
            raise StaleDigestError("The Dry-run Receipt digest changed")
        if not receipt.passed or plan.blocked_count:
            raise ValueError("The Dry-run Receipt did not pass all gates")
        if receipt.expires_at <= self._clock():
            raise ValueError("The Dry-run Receipt expired")

        execution_id = str(uuid.uuid4())
        execution = ExecutionRecord(
            execution_id=execution_id,
            publication_id=publication_id,
            plan_id=plan.plan_id,
            receipt_id=receipt.receipt_id,
            receipt_digest=receipt.receipt_digest,
            actor_id=command.actor_id,
            idempotency_key=command.idempotency_key,
            status="queued",
            total_count=plan.create_count + plan.update_count,
        )
        publishing = replace(
            publication,
            state="publishing",
            latest_execution_id=execution_id,
        )
        await self._repository.begin_execution(publishing, execution)
        return OperationRef(
            operation_id=str(uuid.uuid4()),
            publication_id=publication_id,
            resource_type="execution",
            resource_id=execution_id,
        )

    async def _resume(
        self,
        publication_id: str,
        command: ResumeCommand,
    ) -> OperationRef:
        publication = await self._repository.get_publication(publication_id)
        if command.execution_id != publication.latest_execution_id:
            raise StaleDigestError("The resume command does not target the current Execution")
        execution = await self._repository.get_execution(command.execution_id)
        if execution.publication_id != publication_id:
            raise StaleDigestError("The Execution belongs to another Publication")
        if execution.status == "cancelled":
            raise CancelledExecutionError("A cancelled Execution cannot resume")
        if execution.status == "succeeded":
            return OperationRef(
                operation_id=str(uuid.uuid4()),
                publication_id=publication_id,
                resource_type="execution",
                resource_id=execution.execution_id,
            )
        running = replace(execution, status="running")
        await self._repository.complete_execution(
            replace(publication, state="publishing"),
            running,
        )
        await self._continue_execution(
            replace(publication, state="publishing"),
            running,
            command.credential_ref,
            worker_id=f"{command.actor_id}:{command.idempotency_key}",
        )
        return OperationRef(
            operation_id=str(uuid.uuid4()),
            publication_id=publication_id,
            resource_type="execution",
            resource_id=execution.execution_id,
        )

    async def _cancel(
        self,
        publication_id: str,
        command: CancelCommand,
    ) -> OperationRef:
        publication = await self._repository.get_publication(publication_id)
        if command.execution_id != publication.latest_execution_id:
            raise StaleDigestError("The cancel command does not target the current Execution")
        execution = await self._repository.get_execution(command.execution_id)
        if execution.publication_id != publication_id:
            raise StaleDigestError("The Execution belongs to another Publication")
        if execution.status == "succeeded":
            raise ValueError("A completed Execution cannot be cancelled")
        if not command.reason.strip():
            raise ValueError("A cancellation reason is required")
        cancelled = replace(execution, status="cancelled")
        await self._repository.complete_execution(
            replace(publication, state="cancelled"),
            cancelled,
        )
        return OperationRef(
            operation_id=str(uuid.uuid4()),
            publication_id=publication_id,
            resource_type="execution",
            resource_id=execution.execution_id,
        )

    async def _continue_execution(
        self,
        publication: PublicationRecord,
        execution: ExecutionRecord,
        credential_ref: str,
        *,
        worker_id: str,
    ) -> None:
        plan = await self._repository.get_plan(execution.plan_id)
        session = None
        while True:
            current = await self._repository.get_execution(execution.execution_id)
            if current.status == "cancelled":
                return
            actions = await self._repository.claim_execution_actions(
                execution.execution_id,
                worker_id=worker_id,
                now=self._clock(),
                lease_duration=timedelta(minutes=5),
                limit=50,
            )
            if not actions:
                break
            if session is None:
                await self._repository.checkpoint()
                try:
                    session = await self._gateway.open(credential_ref, publication.target)
                except Exception:
                    await self._repository.complete_execution(
                        replace(publication, state="publishing"),
                        replace(execution, status="paused"),
                    )
                    raise
            await self._process_execution_actions(
                execution=execution,
                plan=plan,
                actions=actions,
                session=session,
            )
            counts = await self._repository.summarize_write_intents(execution.execution_id)
            if (
                counts["blocked"]
                or counts["pre_send_retryable"]
                or counts["outcome_unknown"]
                or counts["in_flight"]
            ):
                await self._finish_execution_from_counts(publication, execution, counts)
                return
        counts = await self._repository.summarize_write_intents(execution.execution_id)
        await self._finish_execution_from_counts(publication, execution, counts)

    async def _process_execution_actions(
        self,
        *,
        execution: ExecutionRecord,
        plan: PlanRecord,
        actions: tuple[ExecutionActionRecord, ...],
        session: WikidataGatewaySession,
    ) -> None:
        entities = await self._repository.get_release_entities(
            plan.release_id,
            tuple(action.entity_key for action in actions),
        )
        entities_by_key = {entity.entity_key: entity for entity in entities}
        for action in actions:
            current = await self._repository.get_execution(execution.execution_id)
            if current.status == "cancelled":
                return
            entity = entities_by_key[action.entity_key]
            mutation = await session.compile_mutation(
                entity,
                action=action.action,
                target_qid=action.target_qid,
                expected_revision=action.target_revision,
                allow_foreign_update=action.allow_foreign_update,
            )
            latest = await self._repository.latest_write_intent(
                execution.execution_id,
                action.entity_key,
            )
            await self._repository.checkpoint()
            if latest is not None and latest.state == "succeeded":
                continue
            if latest is not None and latest.state in {
                "in_flight",
                "outcome_unknown",
            }:
                recovery = await session.recover_ambiguous(
                    GatewayRecoveryRequest(
                        intent_id=latest.intent_id,
                        request_key=latest.request_key,
                        mutation=mutation,
                    )
                )
                if recovery.status == "confirmed_not_applied":
                    latest = replace(
                        latest,
                        state="pre_send_retryable",
                        detail=recovery.detail,
                    )
                    await self._repository.update_write_intent(latest)
                else:
                    await self._record_write_outcome(latest, recovery)
                    continue
            if latest is not None and latest.state == "blocked":
                continue

            attempt = (latest.attempt + 1) if latest is not None else 1
            intent_id = str(uuid.uuid4())
            intent = WriteIntentRecord(
                intent_id=intent_id,
                execution_id=execution.execution_id,
                entity_key=action.entity_key,
                action=action.action,
                target_qid=action.target_qid,
                mutation_digest=mutation.payload_digest,
                request_key=(f"{execution.execution_id}:{action.entity_key}:{attempt}"),
                attempt=attempt,
                state="in_flight",
            )
            await self._repository.add_write_intent(intent)
            try:
                outcome = await session.write(
                    GatewayWriteRequest(
                        intent_id=intent.intent_id,
                        request_key=intent.request_key,
                        mutation=mutation,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                outcome = WriteOutcome.outcome_unknown(str(exc))
            await self._record_write_outcome(intent, outcome)

    async def _finish_execution_from_counts(
        self,
        publication: PublicationRecord,
        execution: ExecutionRecord,
        counts: Mapping[str, int],
    ) -> None:
        if counts["blocked"]:
            status = "failed"
            publication_state = "failed"
        elif counts["pre_send_retryable"] or counts["outcome_unknown"] or counts["in_flight"]:
            status = "paused"
            publication_state = "publishing"
        elif counts["succeeded"] == execution.total_count:
            status = "succeeded"
            publication_state = "completed"
        else:
            status = "paused"
            publication_state = "publishing"
        completed = replace(
            execution,
            status=cast(
                "Literal['queued', 'running', 'paused', 'succeeded', 'failed', 'cancelled']",
                status,
            ),
            succeeded_count=counts["succeeded"],
            pre_send_retryable_count=counts["pre_send_retryable"],
            outcome_unknown_count=counts["outcome_unknown"] + counts["in_flight"],
            blocked_count=counts["blocked"],
        )
        await self._repository.complete_execution(
            replace(publication, state=publication_state),
            completed,
        )

    async def _record_write_outcome(
        self,
        intent: WriteIntentRecord,
        outcome: WriteOutcome,
    ) -> None:
        state = "outcome_unknown" if outcome.status == "confirmed_not_applied" else outcome.status
        updated = replace(
            intent,
            state=cast(
                "Literal['in_flight', 'succeeded', 'pre_send_retryable', "
                "'outcome_unknown', 'blocked', 'skipped']",
                state,
            ),
            detail=outcome.detail,
            result_qid=outcome.qid,
            result_fingerprint=outcome.fingerprint,
        )
        await self._repository.update_write_intent(updated)

    @staticmethod
    def _encode_cursor(release_id: str, entity_key: str) -> str:
        raw = json.dumps(
            {"release_id": release_id, "after": entity_key},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None, release_id: str) -> str | None:
        if cursor is None:
            return None
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        except (ValueError, json.JSONDecodeError) as exc:
            raise InvalidCursorError("The entity cursor is invalid") from exc
        if not isinstance(payload, Mapping) or payload.get("release_id") != release_id:
            raise InvalidCursorError("The entity cursor belongs to another release")
        after = payload.get("after")
        if not isinstance(after, str):
            raise InvalidCursorError("The entity cursor is invalid")
        return after

    @staticmethod
    def _encode_audit_cursor(publication_id: str, sequence: int) -> str:
        raw = json.dumps(
            {"publication_id": publication_id, "after": sequence},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_audit_cursor(cursor: str | None, publication_id: str) -> int | None:
        if cursor is None:
            return None
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        except (ValueError, json.JSONDecodeError) as exc:
            raise InvalidCursorError("The audit cursor is invalid") from exc
        if (
            not isinstance(payload, Mapping)
            or payload.get("publication_id") != publication_id
            or not isinstance(payload.get("after"), int)
        ):
            raise InvalidCursorError("The audit cursor belongs to another Publication")
        return cast(int, payload["after"])
