"""Cross-user inference cache row.

One row per ``(kind, query_hash)`` — content-addressed, shared across
every user in the system. See migration 0009 for the design rationale.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class InferenceCache(Base):
    __tablename__ = "inference_cache"
    __table_args__ = (
        UniqueConstraint("kind", "query_hash", name="uq_inference_cache_key"),
        Index("ix_inference_cache_kind_expires", "kind", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )

    # Cache namespace (e.g. "ner.person", "authority.viaf", "ai_verdict").
    kind:        Mapped[str] = mapped_column(String(48), nullable=False)
    # SHA-256 of the canonical (sorted-key, ensure_ascii) JSON of
    # query_summary. 64 hex chars.
    query_hash:  Mapped[str] = mapped_column(CHAR(64), nullable=False)

    # Echo of the request for debugging + cache-inspection UIs. Never
    # secret material (no tokens).
    query_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    # Whatever the upstream call returned. We pass through, never
    # transform — so hits return byte-identical results to a fresh
    # call.
    result:        Mapped[Any] = mapped_column(JSONB, nullable=False)

    created_by:  Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    hit_count:   Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_hit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # NULL ⇒ never expires. Some kinds (authority.*) set 30d TTL because
    # the upstream answer mutates over time.
    expires_at:  Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
