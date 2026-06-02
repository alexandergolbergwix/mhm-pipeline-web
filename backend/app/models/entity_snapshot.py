"""Entity-state snapshots — periodic full-state checkpoints per entity.

The event log (``ProjectEvent``) is the source of truth for every entity
mutation, but folding from event zero is O(N) in the number of edits.
``EntitySnapshot`` captures the full ``state`` of an entity at a given
revision so the fold can start from the most recent snapshot and replay
only the events after it.

Snapshots are bucketed by ``(bucket, slot)`` so writers can keep a small
rolling set (e.g. one slot per day, one slot per N revisions) without
having to GC them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class EntitySnapshot(Base):
    __tablename__ = "entity_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Matches ProjectEvent.entity_type column width (closed set — see
    # app.models.event.ALL_ENTITY_TYPES).
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[date] = mapped_column(Date, nullable=False)
    slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rev_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # ``none_as_null=True`` is defensive — state is non-null by schema, but
    # this keeps the Python-None → SQL-NULL contract uniform with the
    # ProjectEvent.state column so a downstream regression that tries to
    # write ``None`` here fails loudly with a NOT NULL violation instead
    # of silently persisting the JSON literal ``"null"``.
    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
