"""HTTP shapes for the run-scoped Wikidata Publication seam."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class PublicationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunPublicationSource(PublicationSchema):
    kind: Literal["run"] = "run"
    projection_source: Literal["legacy", "canonical"]
    approved_only: bool = True


class PreparePublicationRequest(PublicationSchema):
    profile_id: str = Field(min_length=1, max_length=128)
    profile_version: str = Field(min_length=1, max_length=64)
    target: Literal["test", "live"]
    source: RunPublicationSource


class EligibleReleaseSelection(PublicationSchema):
    mode: Literal["eligible_release"]


class EntityKeysSelection(PublicationSchema):
    mode: Literal["entities"]
    entity_keys: list[str] = Field(min_length=1, max_length=500)


ReviewSelection = Annotated[
    EligibleReleaseSelection | EntityKeysSelection,
    Field(discriminator="mode"),
]


class ReviewPublicationCommand(PublicationSchema):
    type: Literal["review"]
    release_id: str = Field(min_length=1, max_length=128)
    expected_release_digest: str = Field(min_length=1, max_length=128)
    selection: ReviewSelection
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=2_000)


class DryRunPublicationCommand(PublicationSchema):
    type: Literal["dry_run"]
    approval_set_id: str = Field(min_length=1, max_length=128)
    expected_approval_digest: str = Field(min_length=1, max_length=128)


class PublishPublicationCommand(PublicationSchema):
    type: Literal["publish"]
    plan_id: str = Field(min_length=1, max_length=128)
    dry_run_receipt_id: str = Field(min_length=1, max_length=128)
    expected_receipt_digest: str = Field(min_length=1, max_length=128)


class ResumePublicationCommand(PublicationSchema):
    type: Literal["resume"]
    execution_id: str = Field(min_length=1, max_length=128)


class CancelPublicationCommand(PublicationSchema):
    type: Literal["cancel"]
    operation_id: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=2_000)


PublicationAdvanceCommand = Annotated[
    ReviewPublicationCommand
    | DryRunPublicationCommand
    | PublishPublicationCommand
    | ResumePublicationCommand
    | CancelPublicationCommand,
    Field(discriminator="type"),
]


class AdvancePublicationRequest(PublicationSchema):
    command: PublicationAdvanceCommand


class PublicationSummaryQuery(PublicationSchema):
    type: Literal["summary"]


class PublicationEntitiesQuery(PublicationSchema):
    type: Literal["entities"]
    release_id: str = Field(min_length=1, max_length=128)
    cursor: str | None = Field(default=None, max_length=2_048)
    limit: int = Field(default=100, ge=1, le=500)
    entity_kind: str | None = Field(default=None, max_length=64)
    review_status: Literal["pending", "approved", "rejected", "stale"] | None = None
    query: str | None = Field(default=None, max_length=256)


class PublicationOperationQuery(PublicationSchema):
    type: Literal["operation"]
    operation_id: str = Field(min_length=1, max_length=128)


class PublicationAuditQuery(PublicationSchema):
    type: Literal["audit"]
    cursor: str | None = Field(default=None, max_length=2_048)
    limit: int = Field(default=100, ge=1, le=500)


PublicationReadQuery = Annotated[
    PublicationSummaryQuery
    | PublicationEntitiesQuery
    | PublicationOperationQuery
    | PublicationAuditQuery,
    Field(discriminator="type"),
]


class ReadPublicationRequest(PublicationSchema):
    query: PublicationReadQuery


class PublicationFindingCounts(PublicationSchema):
    error: int = 0
    warning: int = 0
    info: int = 0


class ReleaseSummary(PublicationSchema):
    release_id: str
    release_digest: str
    revision: int
    created_at: datetime
    entity_count: int
    finding_counts: PublicationFindingCounts


class ApprovalSetSummary(PublicationSchema):
    approval_set_id: str
    approval_digest: str
    release_id: str
    release_digest: str
    status: Literal["pending", "approved", "rejected", "stale"]
    approved_count: int
    rejected_count: int
    pending_count: int
    created_at: datetime


class PlanSummary(PublicationSchema):
    plan_id: str
    plan_digest: str
    release_id: str
    release_digest: str
    approval_set_id: str
    status: Literal["ready", "blocked", "expired", "executed"]
    expires_at: datetime | None
    action_counts: dict[str, int]


class DryRunReceiptSummary(PublicationSchema):
    dry_run_receipt_id: str
    receipt_digest: str
    plan_id: str
    plan_digest: str
    status: Literal["valid", "stale", "failed"]
    checked_at: datetime
    expires_at: datetime | None


class ExecutionSummary(PublicationSchema):
    execution_id: str
    plan_id: str
    status: Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"]
    processed: int
    total: int
    succeeded: int
    failed: int
    skipped: int
    current_entity_label: str | None
    started_at: datetime | None
    finished_at: datetime | None


class PublicationOperation(PublicationSchema):
    operation_id: str
    command: Literal["prepare", "review", "dry_run", "publish", "resume", "cancel"]
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: ExecutionSummary | None
    error: str | None


class PublicationSummary(PublicationSchema):
    publication_id: str
    run_id: str
    profile_id: str
    profile_version: str
    target: Literal["test", "live"]
    status: Literal[
        "preparing",
        "ready_for_review",
        "reviewed",
        "dry_run_ready",
        "publishing",
        "paused",
        "completed",
        "cancelled",
        "failed",
    ]
    source_current: bool
    current_release: ReleaseSummary
    approval_set: ApprovalSetSummary | None
    plan: PlanSummary | None
    dry_run_receipt: DryRunReceiptSummary | None
    execution: ExecutionSummary | None


class PublicationMutationResponse(PublicationSchema):
    publication: PublicationSummary | None = None
    operation: PublicationOperation | None = None


class PublicationSummaryRead(PublicationSchema):
    publication: PublicationSummary


class PublicationOperationRead(PublicationSchema):
    operation: PublicationOperation
    publication: PublicationSummary


class PublicationEntityFinding(PublicationSchema):
    finding_id: str
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    gate: bool


class PublicationEntity(PublicationSchema):
    entity_id: str
    entity_digest: str
    entity_kind: str
    label: str
    description: str | None
    target_qid: str | None
    statement_count: int
    review_status: Literal["pending", "approved", "rejected", "stale"]
    proposed_action: Literal["create", "update", "adopt", "skip", "blocked"] | None
    findings: list[PublicationEntityFinding]


class PublicationEntityPage(PublicationSchema):
    release_id: str
    release_digest: str
    items: list[PublicationEntity]
    next_cursor: str | None
    total: int


class PublicationAuditEvent(PublicationSchema):
    event_id: str
    sequence: int
    event_type: str
    occurred_at: datetime
    actor_label: str | None
    release_id: str | None
    entity_id: str | None
    message: str
    details: dict[str, object]


class PublicationAuditPage(PublicationSchema):
    publication_id: str
    items: list[PublicationAuditEvent]
    next_cursor: str | None
    total: int
