"""Audit log for live writes to mhm-hmo.wikibase.cloud."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid

CHANNEL_MANIFEST_UPLOAD = "manifest_upload"
CHANNEL_ITEM_UPLOAD = "item_upload"
CHANNEL_SCHEMA_BOOTSTRAP = "schema_bootstrap"

OPERATION_CREATE = "create"
OPERATION_UPDATE = "update"
OPERATION_SKIP = "skip"
OPERATION_UNCHANGED = "unchanged"
OPERATION_FAILED = "failed"
# A reconcile match found a pre-existing Wikibase item for this source_uri —
# distinct from OPERATION_CREATE (brand-new item) so the review table can
# tell curators "linked to an existing item" apart from "created new".
OPERATION_ADOPT = "adopt"

TARGET_PAGE = "page"
TARGET_ITEM = "item"
TARGET_PROPERTY = "property"
TARGET_CLAIM = "claim"


class WikibaseCloudWrite(Base):
    __tablename__ = "wikibase_cloud_writes"
    __table_args__ = (
        Index("ix_wikibase_cloud_writes_actor_user_id", "actor_user_id"),
        Index("ix_wikibase_cloud_writes_project_id", "project_id"),
        Index("ix_wikibase_cloud_writes_run_id", "run_id"),
        Index("ix_wikibase_cloud_writes_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_jobs.id", ondelete="SET NULL"), nullable=True,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_key: Mapped[str] = mapped_column(Text, nullable=False)
    wikibase_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
