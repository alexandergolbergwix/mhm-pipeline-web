"""Background job rows for long-running curator operations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"

JOB_KIND_AUTHORITY_RE_ENRICH = "authority_re_enrich"
JOB_KIND_EXTRACTION = "extraction"
JOB_KIND_NER_VERIFY = "ner_verify"
JOB_KIND_AUTHORITY_VERIFY = "authority_verify"
JOB_KIND_WIKIDATA_VERIFY = "wikidata_verify"
JOB_KIND_RDF_BUILD = "rdf_build"
JOB_KIND_WIKIDATA_STUDIO_BUILD = "wikidata_studio_build"
JOB_KIND_WIKIDATA_UPLOAD = "wikidata_upload"
JOB_KIND_HMO_SCHEMA_BOOTSTRAP = "hmo_schema_bootstrap"
JOB_KIND_HMO_COVERAGE = "hmo_coverage"
JOB_KIND_HMO_ITEM_UPLOAD = "hmo_item_upload"
JOB_KIND_HMO_ITEM_VERIFY = "hmo_item_verify"
JOB_KIND_HMO_ITEM_BULK_APPROVE = "hmo_item_bulk_approve"
JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE = "wikidata_item_bulk_approve"

SUPPORTED_JOB_KINDS = frozenset({
    JOB_KIND_AUTHORITY_RE_ENRICH,
    JOB_KIND_EXTRACTION,
    JOB_KIND_NER_VERIFY,
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_KIND_RDF_BUILD,
    JOB_KIND_WIKIDATA_STUDIO_BUILD,
    JOB_KIND_WIKIDATA_UPLOAD,
    JOB_KIND_HMO_SCHEMA_BOOTSTRAP,
    JOB_KIND_HMO_COVERAGE,
    JOB_KIND_HMO_ITEM_UPLOAD,
    JOB_KIND_HMO_ITEM_VERIFY,
    JOB_KIND_HMO_ITEM_BULK_APPROVE,
    JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE,
})

TERMINAL_JOB_STATUSES = frozenset({
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED,
})

ACTIVE_JOB_STATUSES = frozenset({
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
})


class RunJob(Base):
    __tablename__ = "run_jobs"
    __table_args__ = (
        Index(
            "uq_run_jobs_active_kind",
            "run_id",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JOB_STATUS_QUEUED,
    )
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
