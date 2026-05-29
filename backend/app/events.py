"""Helpers to append events + project current state from the log.

Phase 7 (real-time collab) wires Postgres LISTEN/NOTIFY off the same
events; Phase 6 ships the persistence + history view + restore for
approvals.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import ProjectEvent
from app.models.run import AuthorityMatch


async def append_event(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    type: str,
    payload: dict[str, Any] | None = None,
) -> ProjectEvent:
    e = ProjectEvent(
        project_id=project_id,
        actor_id=actor_id,
        type=type,
        payload=payload or {},
    )
    db.add(e)
    await db.flush()
    return e


async def apply_restore_to_approvals(
    db: AsyncSession, *, project_id: uuid.UUID, target_event_id: uuid.UUID,
) -> int:
    """Re-apply the approval state that existed *at* ``target_event_id``.

    Implementation: walk the event log forward up to (and including) the
    target, fold the ``match.approved`` / ``match.unapproved`` / ``match.bulk_approved``
    events into a per-match desired state, then UPDATE the matches table
    to match. Returns the count of matches changed.

    Approvals are the unit of restore for the MVP — other mutations
    (renames, member changes) are tracked in the log for audit but not
    yet rewindable.
    """
    target = (
        await db.execute(select(ProjectEvent).where(ProjectEvent.id == target_event_id))
    ).scalar_one_or_none()
    if target is None or target.project_id != project_id:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Target event not in this project",
        )

    events: Iterable[ProjectEvent] = (
        (
            await db.execute(
                select(ProjectEvent)
                .where(
                    ProjectEvent.project_id == project_id,
                    ProjectEvent.created_at <= target.created_at,
                )
                .order_by(ProjectEvent.created_at.asc())
            )
        ).scalars().all()
    )

    # Fold into desired state: match_id → bool.
    desired: dict[uuid.UUID, bool] = {}
    for e in events:
        if e.type in ("match.approved", "match.unapproved"):
            mid = e.payload.get("match_id")
            if mid:
                desired[uuid.UUID(mid)] = e.type == "match.approved"
        elif e.type == "match.bulk_approved":
            ids = e.payload.get("match_ids") or []
            approved = bool(e.payload.get("approved", True))
            for mid in ids:
                desired[uuid.UUID(mid)] = approved

    changed = 0
    if desired:
        rows = (
            await db.execute(
                select(AuthorityMatch).where(AuthorityMatch.id.in_(list(desired.keys())))
            )
        ).scalars().all()
        actor_id = None  # restore is a system action; original actor is on each event
        for m in rows:
            target_state = desired[m.id]
            if m.approved != target_state:
                m.approved = target_state
                m.approved_by = None
                m.approved_at = datetime.now(timezone.utc) if target_state else None
                changed += 1

    await append_event(
        db, project_id=project_id, actor_id=None, type="snapshot.restored",
        payload={"target_event_id": str(target_event_id), "matches_changed": changed},
    )
    return changed
