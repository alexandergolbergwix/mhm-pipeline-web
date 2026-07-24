"""Wikidata Studio per-item overrides.

Studio items are rebuilt from records + authority_matches on every
visit; without persisted overrides the curator's edits would vanish on
the next rebuild. This table stores the curator-supplied diff per
(run_id, local_id):

* ``labels`` / ``descriptions`` / ``aliases``  — sparse maps, each
  language entry overrides whatever the builder produced.
* ``add_statements``     — extra statements appended after the builder's.
* ``remove_statements``  — indices into the builder's statement list to drop.
* ``statement_edits``    — sparse {idx: partial_statement_dict} that
  shallow-merges over the builder's statement at that index.

``local_id`` is the item's stable handle inside a run — usually the
control number for manuscripts, the person key (mazal_id / viaf_id /
normalised name) for persons, etc.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, String, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class WikidataItemOverride(Base):
    __tablename__ = "wikidata_item_overrides"
    __table_args__ = (
        UniqueConstraint("run_id", "local_id", name="uq_override_run_local"),
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

    # Curator item-level approval — distinct from authority-match approval.
    # None = not reviewed; True = approved for export; False = explicitly rejected.
    approved: Mapped[bool | None] = mapped_column(Boolean(), nullable=True, default=None)

    # Explicit per-entity accept to UPDATE a foreign Wikidata QID (not created
    # by the acting user's token). Must match the reconciled QID at upload time.
    accept_foreign_modify: Mapped[bool | None] = mapped_column(
        Boolean(), nullable=True, default=None,
    )
    accepted_foreign_qid: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None,
    )

    ai_verdict: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
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
