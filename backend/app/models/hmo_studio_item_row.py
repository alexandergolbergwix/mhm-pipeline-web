"""Per-item HMO Studio rows — the SQL-paginated review read-model.

One row per built entity, written at build time (and lazily backfilled
for runs built before this table existed). The review table, exports,
and bulk scopes query these rows with SQL joins + cursor pagination —
the 50-100 MB ``hmo_studio_item_cache.resolved_entities`` blob stays a
build-staleness cache and an AI-verify scope source, never a table read
path (the 2026-09-19 H12s: first page request deserialised the blob for
30-60 s, past Heroku's router timeout).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class HmoStudioItemRow(Base):
    __tablename__ = "hmo_studio_item_rows"
    __table_args__ = (
        UniqueConstraint("run_id", "local_id", name="uq_hmo_item_row_run_local"),
        Index("ix_hmo_item_rows_run_sort", "run_id", "label_sort", "local_id"),
        Index("ix_hmo_item_rows_run_class", "run_id", "class_qid"),
        Index("ix_hmo_item_rows_run_uri", "run_id", "source_uri"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    local_id: Mapped[str] = mapped_column(String(256), nullable=False)

    # Build order preserved for stable cursor ties and export ordering.
    ord: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The raw resolved entity (labels, descriptions, aliases, claims,
    # control_numbers, authority_evidence, skipped_statements, ...).
    entity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    label_en: Mapped[str | None] = mapped_column(String(600), nullable=True)
    label_he: Mapped[str | None] = mapped_column(String(600), nullable=True)
    # lower(coalesce(label_en, label_he, local_id)) — the SQL sort key.
    label_sort: Mapped[str] = mapped_column(String(650), nullable=False, default="")
    class_qid: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_uri: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    control_number: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    shacl_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    has_blocking_shacl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
