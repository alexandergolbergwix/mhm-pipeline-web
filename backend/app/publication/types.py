"""Public value types for the publication module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, JsonValue] | list[JsonValue]


@dataclass(frozen=True, slots=True)
class SourceSnapshotRef:
    snapshot_id: str
    revision: str
    digest: str


@dataclass(frozen=True, slots=True)
class ProfileRef:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class TargetRef:
    site: str
    environment: str


@dataclass(frozen=True, slots=True)
class PublicationEntityInput:
    entity_key: str
    entity_type: str
    document: JsonValue
    evidence_refs: tuple[str, ...] = ()
    identity_assertions: tuple[str, ...] = ()
    local_references: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PrepareRequest:
    run_id: str
    source_snapshot: SourceSnapshotRef
    profile: ProfileRef
    target: TargetRef
    actor_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class OperationRef:
    operation_id: str
    publication_id: str
    resource_type: Literal["release", "approval_set", "plan", "execution"]
    resource_id: str
    status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True, slots=True)
class SummaryQuery:
    publication_id: str
    kind: Literal["summary"] = field(default="summary", init=False)


@dataclass(frozen=True, slots=True)
class EntityPageQuery:
    publication_id: str
    release_id: str
    cursor: str | None = None
    limit: int = 100
    kind: Literal["entities"] = field(default="entities", init=False)


@dataclass(frozen=True, slots=True)
class EntitySelection:
    mode: Literal["all", "entities"]
    entity_keys: tuple[str, ...] = ()

    @classmethod
    def all(cls) -> EntitySelection:
        return cls(mode="all")

    @classmethod
    def entities(cls, *entity_keys: str) -> EntitySelection:
        return cls(mode="entities", entity_keys=tuple(sorted(set(entity_keys))))


@dataclass(frozen=True, slots=True)
class ReviewCommand:
    release_id: str
    expected_release_digest: str
    selection: EntitySelection
    decision: Literal["approve", "reject"]
    actor_id: str
    reason: str
    idempotency_key: str
    kind: Literal["review"] = field(default="review", init=False)


@dataclass(frozen=True, slots=True)
class ForeignQidConsent:
    entity_key: str
    qid: str
    remote_revision: int
    entity_digest: str


@dataclass(frozen=True, slots=True)
class DryRunCommand:
    approval_set_id: str
    expected_approval_digest: str
    credential_ref: str
    actor_id: str
    idempotency_key: str
    receipt_ttl_seconds: int = 3_600
    foreign_qid_consents: tuple[ForeignQidConsent, ...] = ()
    kind: Literal["dry_run"] = field(default="dry_run", init=False)


@dataclass(frozen=True, slots=True)
class PublishCommand:
    plan_id: str
    dry_run_receipt_id: str
    expected_receipt_digest: str
    credential_ref: str
    actor_id: str
    idempotency_key: str
    kind: Literal["publish"] = field(default="publish", init=False)


@dataclass(frozen=True, slots=True)
class ResumeCommand:
    execution_id: str
    credential_ref: str
    actor_id: str
    idempotency_key: str
    kind: Literal["resume"] = field(default="resume", init=False)


@dataclass(frozen=True, slots=True)
class CancelCommand:
    execution_id: str
    actor_id: str
    reason: str
    idempotency_key: str
    kind: Literal["cancel"] = field(default="cancel", init=False)


@dataclass(frozen=True, slots=True)
class AuditQuery:
    publication_id: str
    cursor: str | None = None
    limit: int = 100
    kind: Literal["audit"] = field(default="audit", init=False)


@dataclass(frozen=True, slots=True)
class PublicationEntity:
    release_id: str
    entity_key: str
    entity_type: str
    entity_digest: str
    document: JsonValue
    evidence_refs: tuple[str, ...]
    identity_assertions: tuple[str, ...]
    local_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublicationSummary:
    publication_id: str
    run_id: str
    state: Literal[
        "prepared",
        "reviewed",
        "dry_run_ready",
        "publishable",
        "publishing",
        "completed",
        "failed",
        "cancelled",
    ]
    release_id: str
    release_digest: str
    entity_count: int
    finding_count: int
    source_current: bool
    approval_set_id: str | None = None
    approval_digest: str | None = None
    approved_count: int = 0
    rejected_count: int = 0
    plan_id: str | None = None
    plan_digest: str | None = None
    dry_run_receipt_id: str | None = None
    dry_run_receipt_digest: str | None = None
    dry_run_expires_at: datetime | None = None
    plan_create_count: int = 0
    plan_update_count: int = 0
    plan_skip_count: int = 0
    plan_blocked_count: int = 0
    execution_id: str | None = None
    execution_status: (
        Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"] | None
    ) = None
    execution_succeeded_count: int = 0
    execution_pre_send_retryable_count: int = 0
    execution_outcome_unknown_count: int = 0
    execution_blocked_count: int = 0


@dataclass(frozen=True, slots=True)
class EntityPage:
    items: tuple[PublicationEntity, ...]
    next_cursor: str | None


type WriteIntentState = Literal[
    "in_flight",
    "succeeded",
    "pre_send_retryable",
    "outcome_unknown",
    "blocked",
    "skipped",
]


@dataclass(frozen=True, slots=True)
class AuditEntry:
    sequence: int
    execution_id: str
    entity_key: str
    intent_id: str
    state: WriteIntentState
    detail: str | None


@dataclass(frozen=True, slots=True)
class AuditPage:
    items: tuple[AuditEntry, ...]
    next_cursor: str | None


type ReadQuery = SummaryQuery | EntityPageQuery | AuditQuery
type ReadResult = PublicationSummary | EntityPage | AuditPage
type AdvanceCommand = (
    ReviewCommand | DryRunCommand | PublishCommand | ResumeCommand | CancelCommand
)
