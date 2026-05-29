"""Event log — the append-only history of every meaningful mutation.

Every state-changing operation in a project (run created, match
approved, member added, project renamed, snapshot tagged, …) appends a
row here. The current state of the world is the fold of these events;
"restore" creates a new event that re-applies a prior state.

For the MVP, restore is scoped to *approvals* (the curator workflow's
unit of truth). Whole-project rollback (renames, members) is a
follow-up — the events are persisted for it from day one so no migration
is needed when it lands.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


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
