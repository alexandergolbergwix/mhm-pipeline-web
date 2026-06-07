"""History router — entity-versioned event log + diff + revert + snapshots.

Two surfaces live in this module:

* **Legacy project-event endpoints** (``GET /projects/{id}/events``,
  ``GET|POST /projects/{id}/snapshots``, ``POST /projects/{id}/restore/{event_id}``).
  Backs the original project-wide event audit/restore UI built on top of
  :class:`~app.models.event.ProjectEvent` and
  :class:`~app.models.event.ProjectSnapshot`.

* **Entity-versioned endpoints** (``/projects/{id}/history`` and below).
  Per-entity timeline, diff, time-travel read, revert, and archive-tier
  snapshot list, backed by :mod:`app.versioning` and the
  :class:`~app.models.entity_snapshot.EntitySnapshot` archive table.

Both surfaces gate on project membership via ``require_viewer`` /
``require_editor`` from :mod:`app.auth.project_perms`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_perms import (
    ProjectContext,
    require_editor,
    require_viewer,
)
from app.crypto import pii
from app.db import get_session
from app.events import append_event, apply_restore_to_approvals
from app.models.entity_snapshot import EntitySnapshot
from app.models.event import (
    ENTITY_TYPE_AUTHORITY_MATCH,
    ENTITY_TYPE_EXTRACTION_ENTITY,
    ENTITY_TYPE_MARC_RECORD,
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    ProjectEvent,
    ProjectSnapshot,
)
from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, RunRecord
from app.models.user import User
from app.schemas.history import (
    DiffPayload,
    EventRow,
    RevertRequest,
    RevertResponse,
    SnapshotRow,
)
from app.versioning import (
    diff_revs,
    event_timeline,
    revert_to_rev,
    state_at_rev,
)

router = APIRouter(prefix="/projects", tags=["history"])


# ── Legacy project-event endpoints ─────────────────────────────────────


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
    limit: int = Query(default=50, ge=1, le=1000),
    before: datetime | None = Query(
        default=None,
        description="ISO-8601 cursor: return only events created before this timestamp (exclusive).",
    ),
) -> list[EventResponse]:
    stmt = (
        select(ProjectEvent)
        .where(ProjectEvent.project_id == ctx.project.id)
        .order_by(desc(ProjectEvent.created_at))
        .limit(min(max(limit, 1), 1000))
    )
    if before is not None:
        stmt = stmt.where(ProjectEvent.created_at < before)
    rows = (
        await db.execute(stmt)
    ).scalars().all()
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
async def list_project_snapshots(
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found in this project",
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


@router.post("/{project_id}/restore/{event_id}", response_model=RestoreResponse)
async def restore_to(
    event_id: uuid.UUID,
    ctx: ProjectContext = Depends(require_editor),
    db: AsyncSession = Depends(get_session),
) -> RestoreResponse:
    """Restore the project's *approvals* to the state at ``event_id``."""
    changed = await apply_restore_to_approvals(
        db, project_id=ctx.project.id, target_event_id=event_id,
    )
    await db.commit()
    return RestoreResponse(matches_changed=changed)


# ── Entity-versioned endpoints (Wave 3) ────────────────────────────────


@router.get("/{project_id}/history", response_model=list[EventRow])
async def list_history(
    entity_type: str = Query(..., description="Closed-set entity type"),
    entity_id: str = Query(..., description="Stringified entity key"),
    limit: int = Query(default=200, ge=1, le=1000),
    before_rev: int | None = Query(default=None, ge=1),
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> list[EventRow]:
    """Paginated, newest-first event timeline for one entity."""
    events = await event_timeline(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
        before_rev=before_rev,
    )
    # Enforce project-scope: an attacker who knew the (type, id) of an
    # entity in another project must NOT be able to read its events.
    events = [e for e in events if e.project_id == ctx.project.id]

    actor_ids = {e.actor_id for e in events if e.actor_id is not None}
    email_map: dict[uuid.UUID, str] = {}
    if actor_ids:
        users = (
            await db.execute(select(User).where(User.id.in_(list(actor_ids))))
        ).scalars().all()
        email_map = {u.id: pii.decrypt_pii(u.email_encrypted) for u in users}

    return [
        EventRow(
            id=str(e.id),
            rev_no=e.rev_no or 0,
            parent_event_id=str(e.parent_event_id) if e.parent_event_id else None,
            op=e.op or "create",  # type: ignore[arg-type]
            actor_email=email_map.get(e.actor_id) if e.actor_id else None,
            message=e.message or "",
            created_at=e.created_at,
        )
        for e in events
    ]


@router.get("/{project_id}/history/diff", response_model=DiffPayload)
async def get_diff(
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    from_: int = Query(..., alias="from", ge=1),
    to_: int = Query(..., alias="to", ge=1),
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> DiffPayload:
    """Return the RFC 6902 patch + before/after states between two revs."""
    await _assert_entity_in_project(db, ctx.project.id, entity_type, entity_id)
    result = await diff_revs(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        from_rev=from_,
        to_rev=to_,
    )
    return DiffPayload(**result)


@router.get("/{project_id}/history/at")
async def state_at(
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    rev: int = Query(..., ge=1),
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> Any:
    """Time-travel read: the folded state of an entity at ``rev``."""
    await _assert_entity_in_project(db, ctx.project.id, entity_type, entity_id)
    st = await state_at_rev(db, entity_type, entity_id, rev)
    if st is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="state not found at that revision",
        )
    return st


@router.post("/{project_id}/history/revert", response_model=RevertResponse)
async def revert(
    payload: RevertRequest,
    ctx: ProjectContext = Depends(require_editor),
    db: AsyncSession = Depends(get_session),
) -> RevertResponse:
    """Append a ``revert`` event that re-applies an earlier entity state.

    Also updates the read-model projection table so the UI reflects the
    revert immediately on its next fetch. Whole transaction commits at
    the end; the new event + the projection update land together.
    """
    await _assert_entity_in_project(
        db, ctx.project.id, payload.entity_type, payload.entity_id,
    )
    try:
        new_event = await revert_to_rev(
            db,
            project_id=ctx.project.id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            target_rev=payload.target_rev,
            actor_id=ctx.user_id,
            message=payload.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    target_state = await state_at_rev(
        db,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        rev_no=payload.target_rev,
    )
    if target_state is not None:
        await _apply_revert_to_read_model(
            db,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            target_state=target_state,
        )

    await db.commit()
    return RevertResponse(
        ok=True,
        new_event_id=str(new_event.id),
        new_rev_no=new_event.rev_no or 0,
    )


@router.get("/{project_id}/history/snapshots", response_model=list[SnapshotRow])
async def list_entity_snapshots(
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    since: str | None = Query(
        default=None, description="ISO date (YYYY-MM-DD); inclusive lower bound.",
    ),
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> list[SnapshotRow]:
    """Archive-tier snapshot timeline for an entity (3/day, retained forever)."""
    stmt = (
        select(EntitySnapshot)
        .where(
            EntitySnapshot.project_id == ctx.project.id,
            EntitySnapshot.entity_type == entity_type,
            EntitySnapshot.entity_id == entity_id,
        )
        .order_by(EntitySnapshot.bucket.desc(), EntitySnapshot.slot.desc())
    )
    if since:
        try:
            since_date = date.fromisoformat(since)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid `since`: {exc}",
            )
        stmt = stmt.where(EntitySnapshot.bucket >= since_date)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        SnapshotRow(
            bucket=r.bucket.isoformat(),
            slot=r.slot,
            rev_no=r.rev_no,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ── helpers ────────────────────────────────────────────────────────────


async def _assert_entity_in_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: str,
) -> None:
    """Refuse to expose an entity whose event log lives in another project.

    Without this guard a member of project A could enumerate events of
    project B simply by knowing a (type, id) pair — the versioning core
    is intentionally project-agnostic so we re-check here.
    """
    exists = (
        await db.execute(
            select(ProjectEvent.id)
            .where(
                ProjectEvent.project_id == project_id,
                ProjectEvent.entity_type == entity_type,
                ProjectEvent.entity_id == entity_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No history for this entity in this project",
        )


async def _apply_revert_to_read_model(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    target_state: dict[str, Any],
) -> None:
    """Push the reverted state back onto the read-model projection table.

    Idempotent: if the target_state dict doesn't carry a field, the
    projection column is left untouched.
    """
    if entity_type == ENTITY_TYPE_EXTRACTION_ENTITY:
        try:
            approval_id = uuid.UUID(entity_id)
        except ValueError:
            return
        values: dict[str, Any] = {}
        if "approved" in target_state:
            values["approved"] = bool(target_state.get("approved", False))
        if "override_type" in target_state:
            values["override_type"] = target_state.get("override_type")
        if "override_role" in target_state:
            values["override_role"] = target_state.get("override_role")
        if "override_text" in target_state:
            values["override_text"] = target_state.get("override_text")
        if "ai_verdict" in target_state:
            values["ai_verdict"] = target_state.get("ai_verdict")
        if values:
            await db.execute(
                update(ExtractionApproval)
                .where(ExtractionApproval.id == approval_id)
                .values(**values)
            )
        return

    if entity_type == ENTITY_TYPE_AUTHORITY_MATCH:
        try:
            match_id = uuid.UUID(entity_id)
        except ValueError:
            return
        values = {}
        for field in (
            "matched_name", "mazal_id", "viaf_id", "wikidata_qid",
            "confidence", "source", "approved",
        ):
            if field in target_state:
                values[field] = target_state[field]
        if "payload" in target_state:
            values["payload"] = target_state["payload"]
        if values:
            await db.execute(
                update(AuthorityMatch)
                .where(AuthorityMatch.id == match_id)
                .values(**values)
            )
        return

    if entity_type == ENTITY_TYPE_WIKIDATA_OVERRIDE:
        try:
            override_id = uuid.UUID(entity_id)
        except ValueError:
            return
        values = {}
        for field in (
            "labels", "descriptions", "aliases",
            "add_statements", "remove_statements", "statement_edits",
        ):
            if field in target_state:
                values[field] = target_state[field]
        if values:
            await db.execute(
                update(WikidataItemOverride)
                .where(WikidataItemOverride.id == override_id)
                .values(**values)
            )
        return

    if entity_type == ENTITY_TYPE_MARC_RECORD:
        # Composite key: "<run_id>:<control_number>".
        if ":" not in entity_id:
            return
        run_part, _, control_number = entity_id.partition(":")
        try:
            run_uuid = uuid.UUID(run_part)
        except ValueError:
            return
        marc_payload = target_state.get("marc", target_state)
        await db.execute(
            update(RunRecord)
            .where(
                RunRecord.run_id == run_uuid,
                RunRecord.control_number == control_number,
            )
            .values(marc=marc_payload)
        )
        return

    # wikibase_item — no read-model table; the event log itself is the
    # record of what we wrote out. No projection update needed.
    return
