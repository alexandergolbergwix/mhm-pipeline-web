"""Per-item Wikidata Studio rows — the SQL-paginated review read-model.

One row per built entity per build key ``(run_id, approved_only,
source)``, written at build time (and lazily backfilled for runs built
before this table existed). The bulk review-table loads, verify/upload
scope fetches, and exports page these rows with SQL keyset pagination
— the ``wikidata_studio_cache.result_items`` JSONB blob stays a
build-staleness cache and an upload source, never a table read path
(mirrors the 2026-09-19 H12 fix on ``hmo_studio_item_rows``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class WikidataStudioItemRow(Base):
    __tablename__ = "wikidata_studio_item_rows"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "approved_only", "source", "local_id",
            name="uq_wd_item_row_run_local",
        ),
        Index(
            "ix_wd_item_rows_run_sort",
            "run_id", "approved_only", "source", "label_sort", "local_id",
        ),
        Index(
            "ix_wd_item_rows_run_ord",
            "run_id", "approved_only", "source", "ord",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    approved_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="legacy")

    local_id: Mapped[str] = mapped_column(String(256), nullable=False)

    # Build order preserved for stable cursor ties and export ordering.
    ord: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The raw built item (labels, descriptions, aliases, statements,
    # validation_issues, authority_evidence, work_candidate_evidence, ...).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    label_en: Mapped[str | None] = mapped_column(String(600), nullable=True)
    label_he: Mapped[str | None] = mapped_column(String(600), nullable=True)
    # lower(coalesce(label_en, label_he, local_id)) — the SQL sort key.
    label_sort: Mapped[str] = mapped_column(String(650), nullable=False, default="")
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    existing_qid: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_uri: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    record_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    statement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
