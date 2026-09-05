"""Repository seam and the local in-memory adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from app.publication.types import (
    AuditEntry,
    ProfileRef,
    PublicationEntity,
    SourceSnapshotRef,
    TargetRef,
    WriteIntentState,
)


class PublicationNotFoundError(LookupError):
    """The requested publication does not exist."""


class ReleaseNotFoundError(LookupError):
    """The requested release does not exist for the publication."""


@dataclass(frozen=True, slots=True)
class PublicationRecord:
    publication_id: str
    run_id: str
    source_snapshot: SourceSnapshotRef
    profile: ProfileRef
    target: TargetRef
    state: str
    latest_release_id: str
    idempotency_key: str
    latest_approval_set_id: str | None = None
    latest_plan_id: str | None = None
    latest_execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    release_id: str
    publication_id: str
    release_digest: str
    entity_count: int
    finding_count: int = 0


@dataclass(frozen=True, slots=True)
class ApprovalDecisionRecord:
    approval_set_id: str
    entity_key: str
    entity_digest: str
    decision: Literal["approve", "reject"]


@dataclass(frozen=True, slots=True)
class ApprovalSetRecord:
    approval_set_id: str
    publication_id: str
    release_id: str
    release_digest: str
    approval_digest: str
    actor_id: str
    reason: str
    idempotency_key: str
    approved_count: int
    rejected_count: int


@dataclass(frozen=True, slots=True)
class PlanActionRecord:
    plan_id: str
    entity_key: str
    entity_digest: str
    action: Literal["create", "update", "skip", "block"]
    observation_status: Literal["absent", "present_owned", "present_foreign", "unknown"]
    target_qid: str | None
    target_fingerprint: str | None
    target_revision: int | None
    allow_foreign_update: bool
    detail: str | None


@dataclass(frozen=True, slots=True)
class DryRunReceiptRecord:
    receipt_id: str
    plan_id: str
    release_digest: str
    approval_digest: str
    plan_digest: str
    receipt_digest: str
    passed: bool
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PlanRecord:
    plan_id: str
    publication_id: str
    release_id: str
    release_digest: str
    approval_set_id: str
    approval_digest: str
    plan_digest: str
    idempotency_key: str
    create_count: int
    update_count: int
    skip_count: int
    blocked_count: int
    receipt: DryRunReceiptRecord


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    execution_id: str
    publication_id: str
    plan_id: str
    receipt_id: str
    receipt_digest: str
    actor_id: str
    idempotency_key: str
    status: Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"]
    total_count: int
    succeeded_count: int = 0
    pre_send_retryable_count: int = 0
    outcome_unknown_count: int = 0
    blocked_count: int = 0


@dataclass(frozen=True, slots=True)
class WriteIntentRecord:
    intent_id: str
    execution_id: str
    entity_key: str
    action: Literal["create", "update"]
    target_qid: str | None
    mutation_digest: str
    request_key: str
    attempt: int
    state: WriteIntentState
    detail: str | None = None
    result_qid: str | None = None
    result_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionActionRecord:
    execution_id: str
    action_key: str
    entity_key: str
    ordinal: int
    phase: Literal["entity", "deferred_edge"]
    action: Literal["create", "update", "skip", "block"]
    target_qid: str | None
    target_fingerprint: str | None
    target_revision: int | None
    allow_foreign_update: bool
    attempt_count: int


class PublicationRepository(Protocol):
    async def checkpoint(self) -> None: ...

    async def find_prepared(
        self,
        *,
        run_id: str,
        idempotency_key: str,
    ) -> tuple[PublicationRecord, ReleaseRecord] | None: ...

    async def begin_prepare(
        self,
        publication: PublicationRecord,
        release_id: str,
    ) -> None: ...

    async def add_release_entities(
        self,
        release_id: str,
        entities: tuple[PublicationEntity, ...],
    ) -> None: ...

    async def validate_release(self, release_id: str) -> int: ...

    async def complete_prepare(
        self,
        publication: PublicationRecord,
        release: ReleaseRecord,
    ) -> None: ...

    async def get_publication(self, publication_id: str) -> PublicationRecord: ...

    async def get_release(
        self,
        publication_id: str,
        release_id: str,
    ) -> ReleaseRecord: ...

    async def page_release_entities(
        self,
        release_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[tuple[PublicationEntity, ...], bool]: ...

    async def find_approval(
        self,
        *,
        publication_id: str,
        idempotency_key: str,
    ) -> ApprovalSetRecord | None: ...

    async def begin_approval(self, approval_set_id: str) -> None: ...

    async def add_approval_decisions(
        self,
        approval_set_id: str,
        decisions: tuple[ApprovalDecisionRecord, ...],
    ) -> None: ...

    async def complete_approval(
        self,
        publication: PublicationRecord,
        approval_set: ApprovalSetRecord,
    ) -> None: ...

    async def get_approval_set(self, approval_set_id: str) -> ApprovalSetRecord: ...

    async def page_approved_entities(
        self,
        approval_set_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[tuple[PublicationEntity, ...], bool]: ...

    async def find_plan(
        self,
        *,
        publication_id: str,
        idempotency_key: str,
    ) -> PlanRecord | None: ...

    async def begin_plan(self, plan_id: str) -> None: ...

    async def add_plan_actions(
        self,
        plan_id: str,
        actions: tuple[PlanActionRecord, ...],
    ) -> None: ...

    async def complete_plan(
        self,
        publication: PublicationRecord,
        plan: PlanRecord,
    ) -> None: ...

    async def get_plan(self, plan_id: str) -> PlanRecord: ...

    async def page_plan_actions(
        self,
        plan_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[tuple[PlanActionRecord, ...], bool]: ...

    async def get_release_entity(
        self,
        release_id: str,
        entity_key: str,
    ) -> PublicationEntity: ...

    async def get_release_entities(
        self,
        release_id: str,
        entity_keys: tuple[str, ...],
    ) -> tuple[PublicationEntity, ...]: ...

    async def find_execution(
        self,
        *,
        publication_id: str,
        idempotency_key: str,
    ) -> ExecutionRecord | None: ...

    async def begin_execution(
        self,
        publication: PublicationRecord,
        execution: ExecutionRecord,
    ) -> None: ...

    async def get_execution(self, execution_id: str) -> ExecutionRecord: ...

    async def claim_execution_actions(
        self,
        execution_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ExecutionActionRecord, ...]: ...

    async def latest_write_intent(
        self,
        execution_id: str,
        entity_key: str,
    ) -> WriteIntentRecord | None: ...

    async def add_write_intent(self, intent: WriteIntentRecord) -> None: ...

    async def update_write_intent(self, intent: WriteIntentRecord) -> None: ...

    async def complete_execution(
        self,
        publication: PublicationRecord,
        execution: ExecutionRecord,
    ) -> None: ...

    async def summarize_write_intents(
        self,
        execution_id: str,
    ) -> dict[WriteIntentState, int]: ...

    async def page_audit(
        self,
        publication_id: str,
        *,
        after: int | None,
        limit: int,
    ) -> tuple[tuple[AuditEntry, ...], bool]: ...


class InMemoryPublicationRepository:
    """Store immutable publication records for tests and local callers."""

    def __init__(self) -> None:
        self._publications: dict[str, PublicationRecord] = {}
        self._releases: dict[str, ReleaseRecord] = {}
        self._entities: dict[str, dict[str, PublicationEntity]] = {}
        self._approval_sets: dict[str, ApprovalSetRecord] = {}
        self._approval_decisions: dict[str, dict[str, ApprovalDecisionRecord]] = {}
        self._prepare_keys: dict[tuple[str, str], str] = {}
        self._approval_keys: dict[tuple[str, str], str] = {}
        self._plans: dict[str, PlanRecord] = {}
        self._plan_actions: dict[str, dict[str, PlanActionRecord]] = {}
        self._plan_keys: dict[tuple[str, str], str] = {}
        self._executions: dict[str, ExecutionRecord] = {}
        self._execution_actions: dict[str, dict[str, dict[str, object]]] = {}
        self._execution_keys: dict[tuple[str, str], str] = {}
        self._write_intents: dict[str, list[WriteIntentRecord]] = {}
        self._audit: dict[str, list[AuditEntry]] = {}
        self._next_audit_sequence = 1
        self._finding_counts: dict[str, int] = {}

    async def checkpoint(self) -> None:
        return None

    async def find_prepared(
        self,
        *,
        run_id: str,
        idempotency_key: str,
    ) -> tuple[PublicationRecord, ReleaseRecord] | None:
        publication_id = self._prepare_keys.get((run_id, idempotency_key))
        if publication_id is None:
            return None
        publication = self._publications[publication_id]
        return publication, self._releases[publication.latest_release_id]

    async def begin_prepare(
        self,
        publication: PublicationRecord,
        release_id: str,
    ) -> None:
        self._entities[release_id] = {}
        self._publications[publication.publication_id] = publication

    async def add_release_entities(
        self,
        release_id: str,
        entities: tuple[PublicationEntity, ...],
    ) -> None:
        destination = self._entities[release_id]
        for entity in entities:
            destination[entity.entity_key] = entity

    async def validate_release(self, release_id: str) -> int:
        entities = self._entities[release_id]
        identities: dict[str, set[str]] = {}
        for entity in entities.values():
            for assertion in entity.identity_assertions:
                identities.setdefault(assertion, set()).add(entity.entity_key)
        identity_collisions = sum(1 for entity_keys in identities.values() if len(entity_keys) > 1)
        dangling_references = sum(
            1
            for entity in entities.values()
            for target in entity.local_references
            if target not in entities
        )
        deferred_references = sum(len(entity.local_references) for entity in entities.values())
        count = identity_collisions + dangling_references + deferred_references
        self._finding_counts[release_id] = count
        return count

    async def complete_prepare(
        self,
        publication: PublicationRecord,
        release: ReleaseRecord,
    ) -> None:
        self._releases[release.release_id] = release
        self._prepare_keys[(publication.run_id, publication.idempotency_key)] = (
            publication.publication_id
        )

    async def get_publication(self, publication_id: str) -> PublicationRecord:
        try:
            return self._publications[publication_id]
        except KeyError as exc:
            raise PublicationNotFoundError(publication_id) from exc

    async def get_release(
        self,
        publication_id: str,
        release_id: str,
    ) -> ReleaseRecord:
        try:
            release = self._releases[release_id]
        except KeyError as exc:
            raise ReleaseNotFoundError(release_id) from exc
        if release.publication_id != publication_id:
            raise ReleaseNotFoundError(release_id)
        return release

    async def page_release_entities(
        self,
        release_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[tuple[PublicationEntity, ...], bool]:
        if release_id not in self._releases:
            raise ReleaseNotFoundError(release_id)
        keys = sorted(key for key in self._entities[release_id] if after is None or key > after)
        selected = keys[: limit + 1]
        has_more = len(selected) > limit
        selected = selected[:limit]
        return (
            tuple(self._entities[release_id][key] for key in selected),
            has_more,
        )

    async def find_approval(
        self,
        *,
        publication_id: str,
        idempotency_key: str,
    ) -> ApprovalSetRecord | None:
        approval_set_id = self._approval_keys.get((publication_id, idempotency_key))
        if approval_set_id is None:
            return None
        return self._approval_sets[approval_set_id]

    async def begin_approval(self, approval_set_id: str) -> None:
        self._approval_decisions[approval_set_id] = {}

    async def add_approval_decisions(
        self,
        approval_set_id: str,
        decisions: tuple[ApprovalDecisionRecord, ...],
    ) -> None:
        destination = self._approval_decisions[approval_set_id]
        for decision in decisions:
            destination[decision.entity_key] = decision

    async def complete_approval(
        self,
        publication: PublicationRecord,
        approval_set: ApprovalSetRecord,
    ) -> None:
        self._approval_sets[approval_set.approval_set_id] = approval_set
        self._publications[publication.publication_id] = publication
        self._approval_keys[(publication.publication_id, approval_set.idempotency_key)] = (
            approval_set.approval_set_id
        )

    async def get_approval_set(self, approval_set_id: str) -> ApprovalSetRecord:
        try:
            return self._approval_sets[approval_set_id]
        except KeyError as exc:
            raise LookupError(approval_set_id) from exc

    async def page_approved_entities(
        self,
        approval_set_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[tuple[PublicationEntity, ...], bool]:
        approval_set = await self.get_approval_set(approval_set_id)
        decisions = self._approval_decisions[approval_set_id]
        release_entities = self._entities[approval_set.release_id]
        keys = sorted(
            key
            for key, decision in decisions.items()
            if decision.decision == "approve" and (after is None or key > after)
        )
        selected = keys[: limit + 1]
        has_more = len(selected) > limit
        return tuple(release_entities[key] for key in selected[:limit]), has_more

    async def find_plan(
        self,
        *,
        publication_id: str,
        idempotency_key: str,
    ) -> PlanRecord | None:
        plan_id = self._plan_keys.get((publication_id, idempotency_key))
        return self._plans.get(plan_id) if plan_id is not None else None

    async def begin_plan(self, plan_id: str) -> None:
        self._plan_actions[plan_id] = {}

    async def add_plan_actions(
        self,
        plan_id: str,
        actions: tuple[PlanActionRecord, ...],
    ) -> None:
        destination = self._plan_actions[plan_id]
        for action in actions:
            destination[action.entity_key] = action

    async def complete_plan(
        self,
        publication: PublicationRecord,
        plan: PlanRecord,
    ) -> None:
        self._plans[plan.plan_id] = plan
        self._publications[publication.publication_id] = publication
        self._plan_keys[(publication.publication_id, plan.idempotency_key)] = plan.plan_id

    async def get_plan(self, plan_id: str) -> PlanRecord:
        try:
            return self._plans[plan_id]
        except KeyError as exc:
            raise LookupError(plan_id) from exc

    async def page_plan_actions(
        self,
        plan_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[tuple[PlanActionRecord, ...], bool]:
        keys = sorted(key for key in self._plan_actions[plan_id] if after is None or key > after)
        selected = keys[: limit + 1]
        has_more = len(selected) > limit
        return tuple(self._plan_actions[plan_id][key] for key in selected[:limit]), has_more

    async def get_release_entity(
        self,
        release_id: str,
        entity_key: str,
    ) -> PublicationEntity:
        try:
            return self._entities[release_id][entity_key]
        except KeyError as exc:
            raise LookupError(entity_key) from exc

    async def get_release_entities(
        self,
        release_id: str,
        entity_keys: tuple[str, ...],
    ) -> tuple[PublicationEntity, ...]:
        try:
            return tuple(self._entities[release_id][key] for key in entity_keys)
        except KeyError as exc:
            raise LookupError(str(exc)) from exc

    async def find_execution(
        self,
        *,
        publication_id: str,
        idempotency_key: str,
    ) -> ExecutionRecord | None:
        execution_id = self._execution_keys.get((publication_id, idempotency_key))
        return self._executions.get(execution_id) if execution_id is not None else None

    async def begin_execution(
        self,
        publication: PublicationRecord,
        execution: ExecutionRecord,
    ) -> None:
        self._publications[publication.publication_id] = publication
        self._executions[execution.execution_id] = execution
        self._execution_keys[(publication.publication_id, execution.idempotency_key)] = (
            execution.execution_id
        )
        self._write_intents[execution.execution_id] = []
        actions = sorted(
            self._plan_actions[execution.plan_id].values(),
            key=lambda action: action.entity_key,
        )
        self._execution_actions[execution.execution_id] = {
            f"entity:{action.entity_key}": {
                "record": ExecutionActionRecord(
                    execution_id=execution.execution_id,
                    action_key=f"entity:{action.entity_key}",
                    entity_key=action.entity_key,
                    ordinal=ordinal,
                    phase="entity",
                    action=action.action,
                    target_qid=action.target_qid,
                    target_fingerprint=action.target_fingerprint,
                    target_revision=action.target_revision,
                    allow_foreign_update=action.allow_foreign_update,
                    attempt_count=0,
                ),
                "state": "succeeded" if action.action == "skip" else "pending",
                "lease_owner": None,
                "lease_expires_at": None,
            }
            for ordinal, action in enumerate(actions, start=1)
        }
        self._audit.setdefault(publication.publication_id, [])

    async def get_execution(self, execution_id: str) -> ExecutionRecord:
        try:
            return self._executions[execution_id]
        except KeyError as exc:
            raise LookupError(execution_id) from exc

    async def claim_execution_actions(
        self,
        execution_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ExecutionActionRecord, ...]:
        execution = await self.get_execution(execution_id)
        if execution.status != "running":
            return ()
        claimed: list[ExecutionActionRecord] = []
        rows = self._execution_actions[execution_id]
        for row in sorted(rows.values(), key=lambda item: item["record"].ordinal):
            if len(claimed) >= limit:
                break
            if row["state"] not in {
                "pending",
                "pre_send_retryable",
                "in_flight",
                "outcome_unknown",
            }:
                continue
            lease_expires_at = row["lease_expires_at"]
            if isinstance(lease_expires_at, datetime) and lease_expires_at > now:
                continue
            row["lease_owner"] = worker_id
            row["lease_expires_at"] = now + lease_duration
            claimed.append(row["record"])
        return tuple(claimed)

    async def latest_write_intent(
        self,
        execution_id: str,
        entity_key: str,
    ) -> WriteIntentRecord | None:
        matches = [
            intent
            for intent in self._write_intents[execution_id]
            if intent.entity_key == entity_key
        ]
        return matches[-1] if matches else None

    async def add_write_intent(self, intent: WriteIntentRecord) -> None:
        self._write_intents[intent.execution_id].append(intent)
        action = self._execution_actions[intent.execution_id][f"entity:{intent.entity_key}"]
        action["state"] = "in_flight"
        record = action["record"]
        if isinstance(record, ExecutionActionRecord):
            action["record"] = ExecutionActionRecord(
                execution_id=record.execution_id,
                action_key=record.action_key,
                entity_key=record.entity_key,
                ordinal=record.ordinal,
                phase=record.phase,
                action=record.action,
                target_qid=record.target_qid,
                target_fingerprint=record.target_fingerprint,
                target_revision=record.target_revision,
                allow_foreign_update=record.allow_foreign_update,
                attempt_count=intent.attempt,
            )
        await self._append_audit(intent)

    async def update_write_intent(self, intent: WriteIntentRecord) -> None:
        intents = self._write_intents[intent.execution_id]
        for index, current in enumerate(intents):
            if current.intent_id == intent.intent_id:
                intents[index] = intent
                action = self._execution_actions[intent.execution_id][
                    f"entity:{intent.entity_key}"
                ]
                action["state"] = intent.state
                action["lease_owner"] = None
                action["lease_expires_at"] = None
                await self._append_audit(intent)
                return
        raise LookupError(intent.intent_id)

    async def complete_execution(
        self,
        publication: PublicationRecord,
        execution: ExecutionRecord,
    ) -> None:
        self._publications[publication.publication_id] = publication
        self._executions[execution.execution_id] = execution

    async def summarize_write_intents(
        self,
        execution_id: str,
    ) -> dict[WriteIntentState, int]:
        latest: dict[str, WriteIntentRecord] = {}
        for intent in self._write_intents[execution_id]:
            latest[intent.entity_key] = intent
        counts: dict[WriteIntentState, int] = {
            "in_flight": 0,
            "succeeded": 0,
            "pre_send_retryable": 0,
            "outcome_unknown": 0,
            "blocked": 0,
            "skipped": 0,
        }
        for intent in latest.values():
            counts[intent.state] += 1
        return counts

    async def page_audit(
        self,
        publication_id: str,
        *,
        after: int | None,
        limit: int,
    ) -> tuple[tuple[AuditEntry, ...], bool]:
        events = [
            event
            for event in self._audit.get(publication_id, [])
            if after is None or event.sequence > after
        ][: limit + 1]
        return tuple(events[:limit]), len(events) > limit

    async def _append_audit(self, intent: WriteIntentRecord) -> None:
        execution = self._executions[intent.execution_id]
        event = AuditEntry(
            sequence=self._next_audit_sequence,
            execution_id=intent.execution_id,
            entity_key=intent.entity_key,
            intent_id=intent.intent_id,
            state=intent.state,
            detail=intent.detail,
        )
        self._next_audit_sequence += 1
        self._audit[execution.publication_id].append(event)
