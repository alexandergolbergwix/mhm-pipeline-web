"""Per-item curator overrides for HMO Wikibase Studio item builds."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class HmoStudioItemOverride(Base):
    __tablename__ = "hmo_studio_item_overrides"
    __table_args__ = (
        UniqueConstraint("run_id", "local_id", name="uq_hmo_item_override_run_local"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    local_id: Mapped[str] = mapped_column(String(256), nullable=False)

    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    descriptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    add_statements: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    remove_statements: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    statement_edits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    approved: Mapped[bool | None] = mapped_column(Boolean(), nullable=True, default=None)

    ai_verdict: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
    ai_verdict_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
