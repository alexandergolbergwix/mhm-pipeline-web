"""Event log — the append-only history of every meaningful mutation.

Every state-changing operation in a project (run created, match
approved, member added, project renamed, snapshot tagged, …) appends a
row here. The current state of the world is the fold of these events;
"restore" creates a new event that re-applies a prior state.

For the MVP, restore is scoped to *approvals* (the curator workflow's
unit of truth). Whole-project rollback (renames, members) is a
follow-up — the events are persisted for it from day one so no migration
is needed when it lands.

Entity-scoped events
--------------------

Some events describe changes to a specific entity (a MARC record, an
extraction entity, an authority match, a Wikidata override, a Wikibase
item). For those, the event also carries:

* ``entity_type`` / ``entity_id`` — the entity being mutated.
* ``rev_no`` — monotonic per-entity revision counter.
* ``parent_event_id`` — the event this one was derived from (e.g. the
  ``patch`` that a ``revert`` undoes, or the ``create`` a ``patch``
  follows).
* ``op`` — one of ``create``, ``patch``, ``revert``, ``snapshot``.
* ``patch`` — RFC 6902 JSON Patch array (for ``op="patch"`` / ``revert``).
* ``state`` — full entity state (for ``op="create"`` / ``snapshot``).
* ``message`` — optional human-readable note.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


# Entity-type constants used by ProjectEvent.entity_type.
ENTITY_TYPE_MARC_RECORD = "marc_record"
ENTITY_TYPE_EXTRACTION_ENTITY = "extraction_entity"
ENTITY_TYPE_AUTHORITY_MATCH = "authority_match"
ENTITY_TYPE_WIKIDATA_OVERRIDE = "wikidata_override"
ENTITY_TYPE_WIKIBASE_ITEM = "wikibase_item"

# Op constants used by ProjectEvent.op.
OP_CREATE = "create"
OP_PATCH = "patch"
OP_REVERT = "revert"
OP_SNAPSHOT = "snapshot"

ALL_ENTITY_TYPES: frozenset[str] = frozenset({
    ENTITY_TYPE_MARC_RECORD,
    ENTITY_TYPE_EXTRACTION_ENTITY,
    ENTITY_TYPE_AUTHORITY_MATCH,
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    ENTITY_TYPE_WIKIBASE_ITEM,
})

ALL_OPS: frozenset[str] = frozenset({
    OP_CREATE,
    OP_PATCH,
    OP_REVERT,
    OP_SNAPSHOT,
})


class ProjectEvent(Base):
    __tablename__ = "project_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Semantic, dotted event type. Examples:
    #   project.created, project.updated, project.deleted
    #   member.added, member.removed, member.role_changed
    #   run.created, run.deleted
    #   match.approved, match.unapproved, match.bulk_approved
    #   snapshot.tagged, snapshot.restored
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Free-form mutation payload, validated by the writer at append time.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # Entity-scoped event fields. All optional — only populated when the
    # event describes a mutation to a specific entity (see ALL_ENTITY_TYPES
    # / ALL_OPS for the closed sets).
    entity_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True,
    )
    entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rev_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_events.id"),
        nullable=True,
    )
    op: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # RFC 6902 JSON Patch array (for op="patch" / op="revert").
    # ``none_as_null=True`` so a Python ``None`` is stored as SQL NULL
    # rather than the JSON literal ``"null"`` — the replay logic in
    # app.versioning.core relies on ``patch.isnot(None)`` /
    # ``state.isnot(None)`` to filter out events of the wrong op.
    patch: Mapped[list[Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True,
    )
    # Full entity state (for op="create" / op="snapshot"). Same rationale
    # as ``patch`` above — Python None must round-trip to SQL NULL.
    state: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True,
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )


class ProjectSnapshot(Base):
    __tablename__ = "project_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
