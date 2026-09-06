"""SQLAlchemy adapter for durable publication state."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import and_, func, insert, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.publication import (
    Publication as PublicationRow,
)
from app.models.publication import (
    PublicationApprovalDecision as ApprovalDecisionRow,
)
from app.models.publication import (
    PublicationApprovalSet as ApprovalSetRow,
)
from app.models.publication import (
    PublicationDryRunReceipt as DryRunReceiptRow,
)
from app.models.publication import (
    PublicationEntityReference as EntityReferenceRow,
)
from app.models.publication import (
    PublicationEntityRow,
)
from app.models.publication import (
    PublicationExecution as ExecutionRow,
)
from app.models.publication import (
    PublicationExecutionAction as ExecutionActionRow,
)
from app.models.publication import (
    PublicationFinding as FindingRow,
)
from app.models.publication import (
    PublicationIdentityAssertion as IdentityAssertionRow,
)
from app.models.publication import (
    PublicationJournalEvent as JournalEventRow,
)
from app.models.publication import (
    PublicationPlan as PlanRow,
)
from app.models.publication import (
    PublicationPlanAction as PlanActionRow,
)
from app.models.publication import (
    PublicationRelease as ReleaseRow,
)
from app.models.publication import (
    PublicationWriteIntent as WriteIntentRow,
)
from app.models.publication import (
    PublicationWriteReceipt as WriteReceiptRow,
)
from app.publication.digests import freeze_json, thaw_json
from app.publication.repository import (
    ApprovalDecisionRecord,
    ApprovalSetRecord,
    DryRunReceiptRecord,
    ExecutionActionRecord,
    ExecutionRecord,
    PlanActionRecord,
    PlanRecord,
    PublicationNotFoundError,
    PublicationRecord,
    ReleaseNotFoundError,
    ReleaseRecord,
    WriteIntentRecord,
)
from app.publication.types import (
    AuditEntry,
    JsonValue,
    ProfileRef,
    PublicationEntity,
    SourceSnapshotRef,
    TargetRef,
    WriteIntentState,
)


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


class SqlAlchemyPublicationRepository:
    """Persist each entity and action as a separate, keyset-readable row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def checkpoint(self) -> None:
        await self._session.commit()

    async def find_prepared(
        self,
        *,
        run_id: str,
        idempotency_key: str,
    ) -> tuple[PublicationRecord, ReleaseRecord] | None:
        row = (
            await self._session.execute(
                select(PublicationRow).where(
                    PublicationRow.run_id == _uuid(run_id),
                    PublicationRow.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        release = await self._release_row(row.id, row.latest_release_id)
        if release.status != "sealed" or release.release_digest is None:
            return None
        return self._publication_record(row), self._release_record(release)

    async def begin_prepare(
        self,
        publication: PublicationRecord,
        release_id: str,
    ) -> None:
        publication_id = _uuid(publication.publication_id)
        self._session.add(
            PublicationRow(
                id=publication_id,
                run_id=_uuid(publication.run_id),
                source_snapshot_id=publication.source_snapshot.snapshot_id,
                source_revision=publication.source_snapshot.revision,
                source_digest=publication.source_snapshot.digest,
                profile_name=publication.profile.name,
                profile_version=publication.profile.version,
                target_site=publication.target.site,
                target_environment=publication.target.environment,
                state=publication.state,
                idempotency_key=publication.idempotency_key,
                latest_release_id=_uuid(release_id),
            )
        )
        self._session.add(
            ReleaseRow(
                id=_uuid(release_id),
                publication_id=publication_id,
                status="building",
            )
        )
        await self._session.flush()

    async def add_release_entities(
        self,
        release_id: str,
        entities: tuple[PublicationEntity, ...],
    ) -> None:
        release_uuid = _uuid(release_id)
        for entity in entities:
            document = thaw_json(entity.document)
            self._session.add(
                PublicationEntityRow(
                    release_id=release_uuid,
                    entity_key=entity.entity_key,
                    entity_type=entity.entity_type,
                    entity_digest=entity.entity_digest,
                    document=cast(dict[str, object], document),
                    evidence_refs=list(entity.evidence_refs),
                )
            )
            self._session.add_all(
                IdentityAssertionRow(
                    release_id=release_uuid,
                    entity_key=entity.entity_key,
                    assertion=assertion,
                )
                for assertion in entity.identity_assertions
            )
            self._session.add_all(
                EntityReferenceRow(
                    release_id=release_uuid,
                    source_entity_key=entity.entity_key,
                    target_entity_key=target,
                )
                for target in entity.local_references
            )
        await self._session.flush()

    async def validate_release(self, release_id: str) -> int:
        release_uuid = _uuid(release_id)
        finding_count = 0
        duplicate_rows = await self._session.stream(
            select(
                IdentityAssertionRow.assertion,
                func.count(func.distinct(IdentityAssertionRow.entity_key)),
            )
            .where(IdentityAssertionRow.release_id == release_uuid)
            .group_by(IdentityAssertionRow.assertion)
            .having(func.count(func.distinct(IdentityAssertionRow.entity_key)) > 1)
        )
        async for assertion, entity_count in duplicate_rows:
            self._session.add(
                FindingRow(
                    release_id=release_uuid,
                    entity_key=None,
                    scope="identity_group",
                    code="duplicate_strong_identity",
                    severity="blocker",
                    message="Multiple entities share one strong identity assertion.",
                    details={
                        "assertion": assertion,
                        "entity_count": entity_count,
                    },
                )
            )
            finding_count += 1

        target = aliased(PublicationEntityRow)
        dangling_rows = await self._session.stream(
            select(
                EntityReferenceRow.source_entity_key,
                EntityReferenceRow.target_entity_key,
            )
            .select_from(EntityReferenceRow)
            .outerjoin(
                target,
                and_(
                    target.release_id == EntityReferenceRow.release_id,
                    target.entity_key == EntityReferenceRow.target_entity_key,
                ),
            )
            .where(
                EntityReferenceRow.release_id == release_uuid,
                target.entity_key.is_(None),
            )
        )
        async for source_key, target_key in dangling_rows:
            self._session.add(
                FindingRow(
                    release_id=release_uuid,
                    entity_key=source_key,
                    scope="entity",
                    code="dangling_local_reference",
                    severity="blocker",
                    message="A local reference target is absent from the Release.",
                    details={"target_entity_key": target_key},
                )
            )
            finding_count += 1

        deferred_rows = await self._session.stream(
            select(
                EntityReferenceRow.source_entity_key,
                EntityReferenceRow.target_entity_key,
            ).where(EntityReferenceRow.release_id == release_uuid)
        )
        async for source_key, target_key in deferred_rows:
            self._session.add(
                FindingRow(
                    release_id=release_uuid,
                    entity_key=source_key,
                    scope="entity",
                    code="deferred_local_reference_requires_two_phase_execution",
                    severity="blocker",
                    message=(
                        "The Release contains a local reference that requires "
                        "two-phase Publication execution."
                    ),
                    details={"target_entity_key": target_key},
                )
            )
            finding_count += 1
        await self._session.flush()
        return finding_count

    async def complete_prepare(
        self,
        publication: PublicationRecord,
        release: ReleaseRecord,
    ) -> None:
        row = await self._release_row(
            _uuid(publication.publication_id),
            _uuid(release.release_id),
        )
        row.status = "sealed"
        row.release_digest = release.release_digest
        row.entity_count = release.entity_count
        row.finding_count = release.finding_count
        row.sealed_at = datetime.now(UTC)
        await self._session.flush()

    async def get_publication(self, publication_id: str) -> PublicationRecord:
        row = await self._session.get(PublicationRow, _uuid(publication_id))
        if row is None:
            raise PublicationNotFoundError(publication_id)
        return self._publication_record(row)

    async def get_release(
        self,
        publication_id: str,
        release_id: str,
    ) -> ReleaseRecord:
        row = await self._release_row(_uuid(publication_id), _uuid(release_id))
        return self._release_record(row)

    async def page_release_entities(
        self,
        release_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[tuple[PublicationEntity, ...], bool]:
        conditions = [PublicationEntityRow.release_id == _uuid(release_id)]
        if after is not None:
            conditions.append(PublicationEntityRow.entity_key > after)
        rows = (
            (
                await self._session.execute(
                    select(PublicationEntityRow)
                    .where(*conditions)
                    .order_by(PublicationEntityRow.entity_key)
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        selected = rows[:limit]
        entities = await self._entity_records(selected)
        return entities, len(rows) > limit

    async def find_approval(
        self,
        *,
        publication_id: str,
        idempotency_key: str,
    ) -> ApprovalSetRecord | None:
        row = (
            await self._session.execute(
                select(ApprovalSetRow).where(
                    ApprovalSetRow.publication_id == _uuid(publication_id),
                    ApprovalSetRow.idempotency_key == idempotency_key,
                    ApprovalSetRow.status == "sealed",
                )
            )
        ).scalar_one_or_none()
        return self._approval_record(row) if row is not None else None

    async def begin_approval(self, approval_set_id: str) -> None:
        self._session.add(ApprovalSetRow(id=_uuid(approval_set_id), status="building"))
        await self._session.flush()

    async def add_approval_decisions(
        self,
        approval_set_id: str,
        decisions: tuple[ApprovalDecisionRecord, ...],
    ) -> None:
        self._session.add_all(
            ApprovalDecisionRow(
                approval_set_id=_uuid(approval_set_id),
                entity_key=decision.entity_key,
                entity_digest=decision.entity_digest,
                decision=decision.decision,
            )
            for decision in decisions
        )
        await self._session.flush()

    async def complete_approval(
        self,
        publication: PublicationRecord,
        approval_set: ApprovalSetRecord,
    ) -> None:
        row = await self._session.get(ApprovalSetRow, _uuid(approval_set.approval_set_id))
        if row is None:
            raise LookupError(approval_set.approval_set_id)
        row.status = "sealed"
        row.publication_id = _uuid(approval_set.publication_id)
        row.release_id = _uuid(approval_set.release_id)
        row.release_digest = approval_set.release_digest
        row.approval_digest = approval_set.approval_digest
        row.actor_id = approval_set.actor_id
        row.reason = approval_set.reason
        row.idempotency_key = approval_set.idempotency_key
        row.approved_count = approval_set.approved_count
        row.rejected_count = approval_set.rejected_count
        row.sealed_at = datetime.now(UTC)
        publication_row = await self._publication_row(publication.publication_id)
        self._apply_publication(publication_row, publication)
        await self._session.flush()

    async def get_approval_set(self, approval_set_id: str) -> ApprovalSetRecord:
        row = await self._session.get(ApprovalSetRow, _uuid(approval_set_id))
        if row is None or row.status != "sealed":
            raise LookupError(approval_set_id)
        return self._approval_record(row)

    async def page_approved_entities(
        self,
        approval_set_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[tuple[PublicationEntity, ...], bool]:
        approval = await self._session.get(ApprovalSetRow, _uuid(approval_set_id))
        if approval is None or approval.release_id is None:
            raise LookupError(approval_set_id)
        conditions = [
            PublicationEntityRow.release_id == approval.release_id,
            ApprovalDecisionRow.approval_set_id == approval.id,
            ApprovalDecisionRow.decision == "approve",
        ]
        if after is not None:
            conditions.append(PublicationEntityRow.entity_key > after)
        rows = (
            (
                await self._session.execute(
                    select(PublicationEntityRow)
                    .join(
                        ApprovalDecisionRow,
                        ApprovalDecisionRow.entity_key == PublicationEntityRow.entity_key,
                    )
                    .where(*conditions)
                    .order_by(PublicationEntityRow.entity_key)
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        return (
            await self._entity_records(rows[:limit]),
            len(rows) > limit,
        )

    async def find_plan(
        self,
        *,
        publication_id: str,
        idempotency_key: str,
    ) -> PlanRecord | None:
        row = (
            await self._session.execute(
                select(PlanRow).where(
                    PlanRow.publication_id == _uuid(publication_id),
                    PlanRow.idempotency_key == idempotency_key,
                    PlanRow.status == "sealed",
                )
            )
        ).scalar_one_or_none()
        return await self._plan_record(row) if row is not None else None

    async def begin_plan(self, plan_id: str) -> None:
        self._session.add(PlanRow(id=_uuid(plan_id), status="building"))
        await self._session.flush()

    async def add_plan_actions(
        self,
        plan_id: str,
        actions: tuple[PlanActionRecord, ...],
    ) -> None:
        self._session.add_all(
            PlanActionRow(
                plan_id=_uuid(plan_id),
                entity_key=action.entity_key,
                entity_digest=action.entity_digest,
                action=action.action,
                observation_status=action.observation_status,
                target_qid=action.target_qid,
                target_fingerprint=action.target_fingerprint,
                target_revision=action.target_revision,
                allow_foreign_update=action.allow_foreign_update,
                detail=action.detail,
            )
            for action in actions
        )
        await self._session.flush()

    async def complete_plan(
        self,
        publication: PublicationRecord,
        plan: PlanRecord,
    ) -> None:
        row = await self._session.get(PlanRow, _uuid(plan.plan_id))
        if row is None:
            raise LookupError(plan.plan_id)
        row.status = "sealed"
        row.publication_id = _uuid(plan.publication_id)
        row.release_id = _uuid(plan.release_id)
        row.release_digest = plan.release_digest
        row.approval_set_id = _uuid(plan.approval_set_id)
        row.approval_digest = plan.approval_digest
        row.plan_digest = plan.plan_digest
        row.idempotency_key = plan.idempotency_key
        row.create_count = plan.create_count
        row.update_count = plan.update_count
        row.skip_count = plan.skip_count
        row.blocked_count = plan.blocked_count
        row.sealed_at = datetime.now(UTC)
        receipt = plan.receipt
        self._session.add(
            DryRunReceiptRow(
                id=_uuid(receipt.receipt_id),
                plan_id=row.id,
                release_digest=receipt.release_digest,
                approval_digest=receipt.approval_digest,
                plan_digest=receipt.plan_digest,
                receipt_digest=receipt.receipt_digest,
                passed=receipt.passed,
                created_at=receipt.created_at,
                expires_at=receipt.expires_at,
            )
        )
        publication_row = await self._publication_row(publication.publication_id)
        self._apply_publication(publication_row, publication)
        await self._session.flush()

    async def get_plan(self, plan_id: str) -> PlanRecord:
        row = await self._session.get(PlanRow, _uuid(plan_id))
        if row is None or row.status != "sealed":
            raise LookupError(plan_id)
        return await self._plan_record(row)

    async def page_plan_actions(
        self,
        plan_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[tuple[PlanActionRecord, ...], bool]:
        conditions = [PlanActionRow.plan_id == _uuid(plan_id)]
        if after is not None:
            conditions.append(PlanActionRow.entity_key > after)
        rows = (
            (
                await self._session.execute(
                    select(PlanActionRow)
                    .where(*conditions)
                    .order_by(PlanActionRow.entity_key)
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        return tuple(self._plan_action_record(row) for row in rows[:limit]), len(rows) > limit

    async def get_release_entity(
        self,
        release_id: str,
        entity_key: str,
    ) -> PublicationEntity:
        row = await self._session.get(
            PublicationEntityRow,
            (_uuid(release_id), entity_key),
        )
        if row is None:
            raise LookupError(entity_key)
        return (await self._entity_records([row]))[0]

    async def get_release_entities(
        self,
        release_id: str,
        entity_keys: tuple[str, ...],
    ) -> tuple[PublicationEntity, ...]:
        if not entity_keys:
            return ()
        rows = (
            (
                await self._session.execute(
                    select(PublicationEntityRow)
                    .where(
                        PublicationEntityRow.release_id == _uuid(release_id),
                        PublicationEntityRow.entity_key.in_(entity_keys),
                    )
                    .order_by(PublicationEntityRow.entity_key)
                )
            )
            .scalars()
            .all()
        )
        if len(rows) != len(set(entity_keys)):
            raise LookupError("A Plan entity is absent from its Release")
        return await self._entity_records(rows)

    async def find_execution(
        self,
        *,
        publication_id: str,
        idempotency_key: str,
    ) -> ExecutionRecord | None:
        row = (
            await self._session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.publication_id == _uuid(publication_id),
                    ExecutionRow.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        return self._execution_record(row) if row is not None else None

    async def begin_execution(
        self,
        publication: PublicationRecord,
        execution: ExecutionRecord,
    ) -> None:
        self._session.add(
            ExecutionRow(
                id=_uuid(execution.execution_id),
                publication_id=_uuid(execution.publication_id),
                plan_id=_uuid(execution.plan_id),
                receipt_id=_uuid(execution.receipt_id),
                receipt_digest=execution.receipt_digest,
                actor_id=execution.actor_id,
                idempotency_key=execution.idempotency_key,
                status=execution.status,
                total_count=execution.total_count,
                succeeded_count=execution.succeeded_count,
                pre_send_retryable_count=execution.pre_send_retryable_count,
                outcome_unknown_count=execution.outcome_unknown_count,
                blocked_count=execution.blocked_count,
            )
        )
        publication_row = await self._publication_row(publication.publication_id)
        self._apply_publication(publication_row, publication)
        await self._session.flush()
        execution_uuid = _uuid(execution.execution_id)
        ordinal = func.row_number().over(order_by=PlanActionRow.entity_key)
        await self._session.execute(
            insert(ExecutionActionRow).from_select(
                [
                    "execution_id",
                    "action_key",
                    "entity_key",
                    "ordinal",
                    "phase",
                    "state",
                    "attempt_count",
                    "action",
                    "target_qid",
                    "target_fingerprint",
                    "target_revision",
                    "allow_foreign_update",
                ],
                select(
                    literal(execution_uuid),
                    literal("entity:") + PlanActionRow.entity_key,
                    PlanActionRow.entity_key,
                    ordinal,
                    literal("entity"),
                    literal("pending"),
                    literal(0),
                    PlanActionRow.action,
                    PlanActionRow.target_qid,
                    PlanActionRow.target_fingerprint,
                    PlanActionRow.target_revision,
                    PlanActionRow.allow_foreign_update,
                ).where(PlanActionRow.plan_id == _uuid(execution.plan_id), PlanActionRow.action.in_(("create", "update"))),
            )
        )
        await self._session.commit()

    async def get_execution(self, execution_id: str) -> ExecutionRecord:
        row = await self._session.get(ExecutionRow, _uuid(execution_id))
        if row is None:
            raise LookupError(execution_id)
        return self._execution_record(row)

    async def claim_execution_actions(
        self,
        execution_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ExecutionActionRecord, ...]:
        execution_uuid = _uuid(execution_id)
        statement = (
            select(ExecutionActionRow)
            .join(ExecutionRow, ExecutionRow.id == ExecutionActionRow.execution_id)
            .where(
                ExecutionActionRow.execution_id == execution_uuid,
                ExecutionRow.status == "running",
                ExecutionActionRow.action.in_(("create", "update")),
                ExecutionActionRow.state.in_(
                    ("pending", "pre_send_retryable", "in_flight", "outcome_unknown")
                ),
                or_(
                    ExecutionActionRow.next_attempt_at.is_(None),
                    ExecutionActionRow.next_attempt_at <= now,
                ),
                or_(
                    ExecutionActionRow.lease_expires_at.is_(None),
                    ExecutionActionRow.lease_expires_at <= now,
                ),
            )
            .order_by(ExecutionActionRow.phase, ExecutionActionRow.ordinal)
            .limit(limit)
        )
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            statement = statement.with_for_update(
                skip_locked=True,
                of=ExecutionActionRow,
            )
        rows = (await self._session.execute(statement)).scalars().all()
        lease_expires_at = now + lease_duration
        for row in rows:
            row.lease_owner = worker_id
            row.lease_expires_at = lease_expires_at
        await self._session.commit()
        return tuple(
            ExecutionActionRecord(
                execution_id=str(row.execution_id),
                action_key=row.action_key,
                entity_key=row.entity_key,
                ordinal=row.ordinal,
                phase=cast("object", row.phase),
                action=cast("object", row.action),
                target_qid=row.target_qid,
                target_fingerprint=row.target_fingerprint,
                target_revision=row.target_revision,
                allow_foreign_update=row.allow_foreign_update,
                attempt_count=row.attempt_count,
            )
            for row in rows
        )

    async def latest_write_intent(
        self,
        execution_id: str,
        entity_key: str,
    ) -> WriteIntentRecord | None:
        row = (
            await self._session.execute(
                select(WriteIntentRow)
                .where(
                    WriteIntentRow.execution_id == _uuid(execution_id),
                    WriteIntentRow.entity_key == entity_key,
                )
                .order_by(WriteIntentRow.attempt.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return self._write_intent_record(row) if row is not None else None

    async def add_write_intent(self, intent: WriteIntentRecord) -> None:
        row = WriteIntentRow(
            id=_uuid(intent.intent_id),
            execution_id=_uuid(intent.execution_id),
            entity_key=intent.entity_key,
            action=intent.action,
            target_qid=intent.target_qid,
            mutation_digest=intent.mutation_digest,
            request_key=intent.request_key,
            attempt=intent.attempt,
            state=intent.state,
            detail=intent.detail,
            result_qid=intent.result_qid,
            result_fingerprint=intent.result_fingerprint,
        )
        self._session.add(row)
        await self._session.flush()
        await self._append_audit(intent)
        await self._session.commit()
        await self._session.execute(
            update(ExecutionActionRow)
            .where(
                ExecutionActionRow.execution_id == _uuid(intent.execution_id),
                ExecutionActionRow.entity_key == intent.entity_key,
            )
            .values(
                state="in_flight",
                attempt_count=intent.attempt,
                last_error=None,
            )
        )
        await self._session.commit()

    async def update_write_intent(self, intent: WriteIntentRecord) -> None:
        row = await self._session.get(WriteIntentRow, _uuid(intent.intent_id))
        if row is None:
            raise LookupError(intent.intent_id)
        row.state = intent.state
        row.detail = intent.detail
        row.result_qid = intent.result_qid
        row.result_fingerprint = intent.result_fingerprint
        if intent.state in {"succeeded", "blocked"}:
            existing = (
                await self._session.execute(
                    select(WriteReceiptRow).where(WriteReceiptRow.intent_id == row.id)
                )
            ).scalar_one_or_none()
            if existing is None:
                self._session.add(
                    WriteReceiptRow(
                        intent_id=row.id,
                        status=intent.state,
                        qid=intent.result_qid,
                        fingerprint=intent.result_fingerprint,
                        detail=intent.detail,
                    )
                )
        await self._session.flush()
        await self._append_audit(intent)
        await self._session.commit()
        await self._session.execute(
            update(ExecutionActionRow)
            .where(
                ExecutionActionRow.execution_id == _uuid(intent.execution_id),
                ExecutionActionRow.entity_key == intent.entity_key,
            )
            .values(
                state=intent.state,
                lease_owner=None,
                lease_expires_at=None,
                result_qid=intent.result_qid,
                result_fingerprint=intent.result_fingerprint,
                last_error=intent.detail,
            )
        )
        await self._session.commit()

    async def complete_execution(
        self,
        publication: PublicationRecord,
        execution: ExecutionRecord,
    ) -> None:
        row = await self._session.get(ExecutionRow, _uuid(execution.execution_id))
        if row is None:
            raise LookupError(execution.execution_id)
        row.status = execution.status
        row.succeeded_count = execution.succeeded_count
        row.pre_send_retryable_count = execution.pre_send_retryable_count
        row.outcome_unknown_count = execution.outcome_unknown_count
        row.blocked_count = execution.blocked_count
        publication_row = await self._publication_row(publication.publication_id)
        self._apply_publication(publication_row, publication)
        await self._session.commit()

    async def summarize_write_intents(
        self,
        execution_id: str,
    ) -> dict[WriteIntentState, int]:
        execution_uuid = _uuid(execution_id)
        latest = (
            select(
                WriteIntentRow.entity_key.label("entity_key"),
                func.max(WriteIntentRow.attempt).label("attempt"),
            )
            .where(WriteIntentRow.execution_id == execution_uuid)
            .group_by(WriteIntentRow.entity_key)
            .subquery()
        )
        rows = (
            await self._session.execute(
                select(WriteIntentRow.state, func.count())
                .join(
                    latest,
                    and_(
                        WriteIntentRow.entity_key == latest.c.entity_key,
                        WriteIntentRow.attempt == latest.c.attempt,
                    ),
                )
                .where(WriteIntentRow.execution_id == execution_uuid)
                .group_by(WriteIntentRow.state)
            )
        ).all()
        counts: dict[WriteIntentState, int] = {
            "in_flight": 0,
            "succeeded": 0,
            "pre_send_retryable": 0,
            "outcome_unknown": 0,
            "blocked": 0,
            "skipped": 0,
        }
        for state, count in rows:
            counts[cast(WriteIntentState, state)] = count
        return counts

    async def page_audit(
        self,
        publication_id: str,
        *,
        after: int | None,
        limit: int,
    ) -> tuple[tuple[AuditEntry, ...], bool]:
        conditions = [JournalEventRow.publication_id == _uuid(publication_id)]
        if after is not None:
            conditions.append(JournalEventRow.sequence > after)
        rows = (
            (
                await self._session.execute(
                    select(JournalEventRow)
                    .where(*conditions)
                    .order_by(JournalEventRow.sequence)
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        return (
            tuple(
                AuditEntry(
                    sequence=row.sequence,
                    execution_id=str(row.execution_id),
                    entity_key=row.entity_key,
                    intent_id=str(row.intent_id),
                    state=cast(WriteIntentState, row.state),
                    detail=row.detail,
                )
                for row in rows[:limit]
            ),
            len(rows) > limit,
        )

    async def _append_audit(self, intent: WriteIntentRecord) -> None:
        execution = await self._session.get(ExecutionRow, _uuid(intent.execution_id))
        if execution is None:
            raise LookupError(intent.execution_id)
        self._session.add(
            JournalEventRow(
                publication_id=execution.publication_id,
                execution_id=execution.id,
                intent_id=_uuid(intent.intent_id),
                entity_key=intent.entity_key,
                state=intent.state,
                detail=intent.detail,
            )
        )
        await self._session.flush()

    async def _publication_row(self, publication_id: str) -> PublicationRow:
        row = await self._session.get(PublicationRow, _uuid(publication_id))
        if row is None:
            raise PublicationNotFoundError(publication_id)
        return row

    async def _release_row(
        self,
        publication_id: uuid.UUID,
        release_id: uuid.UUID,
    ) -> ReleaseRow:
        row = await self._session.get(ReleaseRow, release_id)
        if row is None or row.publication_id != publication_id:
            raise ReleaseNotFoundError(str(release_id))
        return row

    async def _entity_records(
        self,
        rows: list[PublicationEntityRow],
    ) -> tuple[PublicationEntity, ...]:
        if not rows:
            return ()
        release_id = rows[0].release_id
        entity_keys = [row.entity_key for row in rows]
        identity_rows = (
            await self._session.execute(
                select(
                    IdentityAssertionRow.entity_key,
                    IdentityAssertionRow.assertion,
                )
                .where(
                    IdentityAssertionRow.release_id == release_id,
                    IdentityAssertionRow.entity_key.in_(entity_keys),
                )
                .order_by(
                    IdentityAssertionRow.entity_key,
                    IdentityAssertionRow.assertion,
                )
            )
        ).all()
        reference_rows = (
            await self._session.execute(
                select(
                    EntityReferenceRow.source_entity_key,
                    EntityReferenceRow.target_entity_key,
                )
                .where(
                    EntityReferenceRow.release_id == release_id,
                    EntityReferenceRow.source_entity_key.in_(entity_keys),
                )
                .order_by(
                    EntityReferenceRow.source_entity_key,
                    EntityReferenceRow.target_entity_key,
                )
            )
        ).all()
        identities: dict[str, list[str]] = {}
        references: dict[str, list[str]] = {}
        for entity_key, assertion in identity_rows:
            identities.setdefault(entity_key, []).append(assertion)
        for entity_key, target_key in reference_rows:
            references.setdefault(entity_key, []).append(target_key)
        return tuple(
            PublicationEntity(
                release_id=str(row.release_id),
                entity_key=row.entity_key,
                entity_type=row.entity_type,
                entity_digest=row.entity_digest,
                document=freeze_json(cast(JsonValue, row.document)),
                evidence_refs=tuple(row.evidence_refs),
                identity_assertions=tuple(identities.get(row.entity_key, ())),
                local_references=tuple(references.get(row.entity_key, ())),
            )
            for row in rows
        )

    async def _plan_record(self, row: PlanRow) -> PlanRecord:
        receipt = (
            await self._session.execute(
                select(DryRunReceiptRow).where(DryRunReceiptRow.plan_id == row.id)
            )
        ).scalar_one()
        if (
            row.publication_id is None
            or row.release_id is None
            or row.release_digest is None
            or row.approval_set_id is None
            or row.approval_digest is None
            or row.plan_digest is None
            or row.idempotency_key is None
        ):
            raise LookupError(str(row.id))
        return PlanRecord(
            plan_id=str(row.id),
            publication_id=str(row.publication_id),
            release_id=str(row.release_id),
            release_digest=row.release_digest,
            approval_set_id=str(row.approval_set_id),
            approval_digest=row.approval_digest,
            plan_digest=row.plan_digest,
            idempotency_key=row.idempotency_key,
            create_count=row.create_count,
            update_count=row.update_count,
            skip_count=row.skip_count,
            blocked_count=row.blocked_count,
            receipt=DryRunReceiptRecord(
                receipt_id=str(receipt.id),
                plan_id=str(receipt.plan_id),
                release_digest=receipt.release_digest,
                approval_digest=receipt.approval_digest,
                plan_digest=receipt.plan_digest,
                receipt_digest=receipt.receipt_digest,
                passed=receipt.passed,
                created_at=receipt.created_at,
                expires_at=receipt.expires_at,
            ),
        )

    @staticmethod
    def _publication_record(row: PublicationRow) -> PublicationRecord:
        return PublicationRecord(
            publication_id=str(row.id),
            run_id=str(row.run_id),
            source_snapshot=SourceSnapshotRef(
                snapshot_id=row.source_snapshot_id,
                revision=row.source_revision,
                digest=row.source_digest,
            ),
            profile=ProfileRef(name=row.profile_name, version=row.profile_version),
            target=TargetRef(site=row.target_site, environment=row.target_environment),
            state=row.state,
            latest_release_id=str(row.latest_release_id),
            idempotency_key=row.idempotency_key,
            latest_approval_set_id=(
                str(row.latest_approval_set_id) if row.latest_approval_set_id is not None else None
            ),
            latest_plan_id=(str(row.latest_plan_id) if row.latest_plan_id else None),
            latest_execution_id=(str(row.latest_execution_id) if row.latest_execution_id else None),
        )

    @staticmethod
    def _release_record(row: ReleaseRow) -> ReleaseRecord:
        if row.release_digest is None:
            raise ReleaseNotFoundError(str(row.id))
        return ReleaseRecord(
            release_id=str(row.id),
            publication_id=str(row.publication_id),
            release_digest=row.release_digest,
            entity_count=row.entity_count,
            finding_count=row.finding_count,
        )

    @staticmethod
    def _approval_record(row: ApprovalSetRow) -> ApprovalSetRecord:
        if (
            row.publication_id is None
            or row.release_id is None
            or row.release_digest is None
            or row.approval_digest is None
            or row.actor_id is None
            or row.reason is None
            or row.idempotency_key is None
        ):
            raise LookupError(str(row.id))
        return ApprovalSetRecord(
            approval_set_id=str(row.id),
            publication_id=str(row.publication_id),
            release_id=str(row.release_id),
            release_digest=row.release_digest,
            approval_digest=row.approval_digest,
            actor_id=row.actor_id,
            reason=row.reason,
            idempotency_key=row.idempotency_key,
            approved_count=row.approved_count,
            rejected_count=row.rejected_count,
        )

    @staticmethod
    def _plan_action_record(row: PlanActionRow) -> PlanActionRecord:
        return PlanActionRecord(
            plan_id=str(row.plan_id),
            entity_key=row.entity_key,
            entity_digest=row.entity_digest,
            action=cast("object", row.action),
            observation_status=cast("object", row.observation_status),
            target_qid=row.target_qid,
            target_fingerprint=row.target_fingerprint,
            target_revision=row.target_revision,
            allow_foreign_update=row.allow_foreign_update,
            detail=row.detail,
        )

    @staticmethod
    def _execution_record(row: ExecutionRow) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=str(row.id),
            publication_id=str(row.publication_id),
            plan_id=str(row.plan_id),
            receipt_id=str(row.receipt_id),
            receipt_digest=row.receipt_digest,
            actor_id=row.actor_id,
            idempotency_key=row.idempotency_key,
            status=cast("object", row.status),
            total_count=row.total_count,
            succeeded_count=row.succeeded_count,
            pre_send_retryable_count=row.pre_send_retryable_count,
            outcome_unknown_count=row.outcome_unknown_count,
            blocked_count=row.blocked_count,
        )

    @staticmethod
    def _write_intent_record(row: WriteIntentRow) -> WriteIntentRecord:
        return WriteIntentRecord(
            intent_id=str(row.id),
            execution_id=str(row.execution_id),
            entity_key=row.entity_key,
            action=cast("object", row.action),
            target_qid=row.target_qid,
            mutation_digest=row.mutation_digest,
            request_key=row.request_key,
            attempt=row.attempt,
            state=cast(WriteIntentState, row.state),
            detail=row.detail,
            result_qid=row.result_qid,
            result_fingerprint=row.result_fingerprint,
        )

    @staticmethod
    def _apply_publication(row: PublicationRow, record: PublicationRecord) -> None:
        row.state = record.state
        row.latest_release_id = _uuid(record.latest_release_id)
        row.latest_approval_set_id = (
            _uuid(record.latest_approval_set_id)
            if record.latest_approval_set_id is not None
            else None
        )
        row.latest_plan_id = (
            _uuid(record.latest_plan_id) if record.latest_plan_id is not None else None
        )
        row.latest_execution_id = (
            _uuid(record.latest_execution_id) if record.latest_execution_id is not None else None
        )
