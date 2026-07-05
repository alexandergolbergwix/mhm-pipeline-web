"""Per-run HMO Wikibase item build cache.

One row per run, upserted after every rebuild and checked before
starting one:

  fingerprint matches  ->  return cached resolved entities immediately
  fingerprint differs  ->  rebuild (fingerprint covers the RDF TTL bytes
                            AND the schema-mapping version, so a schema
                            bootstrap invalidates every run's cached build)
  no cache row         ->  rebuild

This is a build-staleness cache, not the write-dedup mechanism — that's
``wikibase_entity_mappings`` (Phase 3). The two are deliberately
separate tables: a schema bootstrap mutates mappings independently of
any one run's cached build.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, DateTime, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class HmoStudioItemCache(Base):
    __tablename__ = "hmo_studio_item_cache"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_hmo_studio_item_cache_run"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    input_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)

    resolved_entities: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deferred_link_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_statement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    shacl_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)

    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
