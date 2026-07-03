"""Idempotency ledger for entities created on ``mhm-hmo.wikibase.cloud``.

One row per ontology URI (or per-run instance) that already has a live
Wikibase QID/PID. Schema rows (``run_id IS NULL``) are global — created
once by the schema bootstrap (Phase 3) and reused by every run's item
export (Phase 4/5). Instance rows (``run_id`` set) record one manuscript/
person/place/etc. item created for a specific run.

This table is the write-dedup mechanism, not a build cache: mapping rows
are read fresh on every bootstrap/build/upload pass so a schema created
while building run A is immediately visible to run B. The fingerprinted
per-run build cache (``hmo_studio_item_cache``, Phase 4) is a separate
table for a separate concern (avoiding recomputation, not avoiding
duplicate writes).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid

ENTITY_KIND_CLASS = "class"
ENTITY_KIND_PROPERTY = "property"
ENTITY_KIND_INSTANCE = "instance"


class WikibaseEntityMapping(Base):
    __tablename__ = "wikibase_entity_mappings"
    __table_args__ = (
        Index("ix_wikibase_entity_mappings_entity_kind", "entity_kind"),
        Index("ix_wikibase_entity_mappings_run_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )

    ontology_uri: Mapped[str] = mapped_column(Text, nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    wikibase_id: Mapped[str] = mapped_column(String(32), nullable=False)

    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    local_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    datatype: Mapped[str | None] = mapped_column(String(32), nullable=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
