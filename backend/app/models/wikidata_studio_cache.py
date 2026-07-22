"""Per-run Wikidata Studio build cache.

One row per (run_id, approved_only).  The row is upserted after every
rebuild and checked before starting one:

  fingerprint matches  →  return cached result immediately (no build)
  fingerprint differs  →  return cached result with ``cache_stale``; curator
                         must click Rebuild to refresh (no passive background job)
  no cache row         →  409 + background build job

The fingerprint is a SHA-256 over the serialised state of every input
that affects the build: MARC control numbers, approved authority match
fields (including payload), NER approval overrides, and curator item
overrides.  A single approval checkbox tick changes the fingerprint and
triggers an automatic rebuild on the next page load.

The result columns duplicate StudioBuildResponse verbatim so the
router can reconstruct the full response object with no extra queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, CHAR, DateTime, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class WikidataStudioCache(Base):
    __tablename__ = "wikidata_studio_cache"
    __table_args__ = (
        UniqueConstraint("run_id", "approved_only", "source", name="uq_wikidata_studio_cache_run_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )
    approved_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="legacy")

    # SHA-256 of the canonical JSON of all build inputs.
    input_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)

    # Full StudioBuildResponse payload.
    result_items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    quickstatements: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    approved_match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
