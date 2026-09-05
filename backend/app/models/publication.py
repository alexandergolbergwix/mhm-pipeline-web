"""Normalized durable state for the Wikidata publication module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_publications_run_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_snapshot_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(256), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_name: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    target_site: Mapped[str] = mapped_column(String(256), nullable=False)
    target_environment: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    latest_release_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    latest_approval_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    latest_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    latest_execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PublicationRelease(Base):
    __tablename__ = "publication_releases"
    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "release_digest",
            name="uq_publication_releases_digest",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    release_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublicationEntityRow(Base):
    __tablename__ = "publication_entities"
    __table_args__ = (
        Index(
            "ix_publication_entities_release_type_key",
            "release_id",
            "entity_type",
            "entity_key",
        ),
    )

    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_releases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PublicationIdentityAssertion(Base):
    __tablename__ = "publication_identity_assertions"
    __table_args__ = (
        Index(
            "ix_publication_identity_release_assertion",
            "release_id",
            "assertion",
        ),
    )

    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_releases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    assertion: Mapped[str] = mapped_column(String(512), primary_key=True)


class PublicationEntityReference(Base):
    __tablename__ = "publication_entity_references"

    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_releases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_entity_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    target_entity_key: Mapped[str] = mapped_column(String(512), primary_key=True)


class PublicationFinding(Base):
    __tablename__ = "publication_findings"
    __table_args__ = (Index("ix_publication_findings_release_severity", "release_id", "severity"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_releases.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PublicationApprovalSet(Base):
    __tablename__ = "publication_approval_sets"
    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "idempotency_key",
            name="uq_publication_approval_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    publication_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_releases.id", ondelete="CASCADE"),
        nullable=True,
    )
    release_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublicationApprovalDecision(Base):
    __tablename__ = "publication_approval_decisions"

    approval_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_approval_sets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    entity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)


class PublicationPlan(Base):
    __tablename__ = "publication_plans"
    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "idempotency_key",
            name="uq_publication_plan_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    publication_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_releases.id", ondelete="CASCADE"),
        nullable=True,
    )
    release_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_approval_sets.id", ondelete="CASCADE"),
        nullable=True,
    )
    approval_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    create_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    update_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublicationPlanAction(Base):
    __tablename__ = "publication_plan_actions"
    __table_args__ = (Index("ix_publication_plan_actions_action", "plan_id", "action"),)

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_plans.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    entity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    observation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    target_qid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    allow_foreign_update: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class PublicationDryRunReceipt(Base):
    __tablename__ = "publication_dry_run_receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_plans.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    release_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicationExecution(Base):
    __tablename__ = "publication_executions"
    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "idempotency_key",
            name="uq_publication_execution_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_dry_run_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pre_send_retryable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome_unknown_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PublicationExecutionAction(Base):
    __tablename__ = "publication_execution_actions"
    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "ordinal",
            name="uq_publication_execution_action_ordinal",
        ),
        Index(
            "ix_publication_execution_actions_claim",
            "execution_id",
            "state",
            "next_attempt_at",
            "ordinal",
        ),
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_executions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    action_key: Mapped[str] = mapped_column(String(768), primary_key=True)
    entity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    lease_owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    target_qid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    allow_foreign_update: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    result_qid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PublicationWriteIntent(Base):
    __tablename__ = "publication_write_intents"
    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "entity_key",
            "attempt",
            name="uq_publication_write_intent_attempt",
        ),
        UniqueConstraint("request_key", name="uq_publication_write_request_key"),
        Index("ix_publication_write_intents_state", "execution_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    target_qid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mutation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_qid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PublicationWriteReceipt(Base):
    __tablename__ = "publication_write_receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_write_intents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    qid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PublicationJournalEvent(Base):
    __tablename__ = "publication_journal_events"
    __table_args__ = (
        Index("ix_publication_journal_publication_sequence", "publication_id", "sequence"),
    )

    sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="CASCADE"),
        nullable=False,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_write_intents.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
