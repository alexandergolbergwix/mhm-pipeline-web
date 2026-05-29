"""History router — project event log + named snapshots + restore."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_perms import (
    ProjectContext,
    require_editor,
    require_viewer,
)
from app.crypto import pii
from app.db import get_session
from app.events import append_event, apply_restore_to_approvals
from app.models.event import ProjectEvent, ProjectSnapshot
from app.models.user import User

router = APIRouter(prefix="/projects", tags=["history"])


class EventResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_name: str | None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SnapshotCreate(BaseModel):
    event_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)


class SnapshotResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    event_id: uuid.UUID
    name: str
    created_by: uuid.UUID | None
    created_at: datetime


class RestoreResponse(BaseModel):
    ok: Literal[True] = True
    matches_changed: int


@router.get("/{project_id}/events", response_model=list[EventResponse])
async def list_events(
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
    limit: int = 200,
) -> list[EventResponse]:
    rows = (
        await db.execute(
            select(ProjectEvent)
            .where(ProjectEvent.project_id == ctx.project.id)
            .order_by(desc(ProjectEvent.created_at))
            .limit(min(max(limit, 1), 1000))
        )
    ).scalars().all()
    # Decrypt actor names in one batch.
    actor_ids = {e.actor_id for e in rows if e.actor_id is not None}
    names: dict[uuid.UUID, str] = {}
    if actor_ids:
        users = (
            await db.execute(select(User).where(User.id.in_(list(actor_ids))))
        ).scalars().all()
        names = {u.id: pii.decrypt_pii(u.name_encrypted) for u in users}
    return [
        EventResponse(
            id=e.id, project_id=e.project_id, actor_id=e.actor_id,
            actor_name=names.get(e.actor_id) if e.actor_id else None,
            type=e.type, payload=e.payload, created_at=e.created_at,
        )
        for e in rows
    ]


@router.get("/{project_id}/snapshots", response_model=list[SnapshotResponse])
async def list_snapshots(
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> list[SnapshotResponse]:
    rows = (
        await db.execute(
            select(ProjectSnapshot)
            .where(ProjectSnapshot.project_id == ctx.project.id)
            .order_by(desc(ProjectSnapshot.created_at))
        )
    ).scalars().all()
    return [
        SnapshotResponse(
            id=s.id, project_id=s.project_id, event_id=s.event_id,
            name=s.name, created_by=s.created_by, created_at=s.created_at,
        )
        for s in rows
    ]


@router.post(
    "/{project_id}/snapshots", response_model=SnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_snapshot(
    payload: SnapshotCreate,
    ctx: ProjectContext = Depends(require_editor),
    db: AsyncSession = Depends(get_session),
) -> SnapshotResponse:
    e = (
        await db.execute(select(ProjectEvent).where(ProjectEvent.id == payload.event_id))
    ).scalar_one_or_none()
    if e is None or e.project_id != ctx.project.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Event not found in this project",
        )
    snap = ProjectSnapshot(
        project_id=ctx.project.id, event_id=e.id, name=payload.name,
        created_by=ctx.user_id,
    )
    db.add(snap)
    await db.flush()
    await append_event(
        db, project_id=ctx.project.id, actor_id=ctx.user_id, type="snapshot.tagged",
        payload={"snapshot_id": str(snap.id), "name": snap.name, "event_id": str(e.id)},
    )
    await db.commit()
    return SnapshotResponse(
        id=snap.id, project_id=snap.project_id, event_id=snap.event_id,
        name=snap.name, created_by=snap.created_by, created_at=snap.created_at,
    )


@router.post(
    "/{project_id}/restore/{event_id}", response_model=RestoreResponse,
)
async def restore_to(
    event_id: uuid.UUID,
    ctx: ProjectContext = Depends(require_editor),
    db: AsyncSession = Depends(get_session),
) -> RestoreResponse:
    """Restore the project's *approvals* to the state at ``event_id``.

    Non-approval mutations (renames, member changes, run uploads) are
    audited but not yet rewindable; the v1 scope covers the curator
    workflow's unit of truth and surfaces clearly what was changed.
    """
    changed = await apply_restore_to_approvals(
        db, project_id=ctx.project.id, target_event_id=event_id,
    )
    await db.commit()
    return RestoreResponse(matches_changed=changed)
