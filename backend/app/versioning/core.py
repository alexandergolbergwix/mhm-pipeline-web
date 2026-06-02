"""Versioning core — canonical mutation entry point for entity-scoped events.

Every state-changing curator decision routes through ``apply_event()`` BEFORE
the caller updates the read-model. The caller commits both writes in the same
SQLAlchemy transaction.

The append-only event log on ``project_events`` is the source of truth; the
current state of an entity is the fold of its events (snapshot + replayed
patches). See ``app.models.event`` for the row schema and the closed sets of
``entity_type`` / ``op`` values this module validates against.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import jsonpatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import (
    ALL_ENTITY_TYPES,
    ALL_OPS,
    OP_CREATE,
    OP_PATCH,
    OP_REVERT,
    OP_SNAPSHOT,
    ProjectEvent,
)

logger = logging.getLogger(__name__)


# How often (in rev_no) to auto-emit a snapshot after a non-snapshot event.
_SNAPSHOT_EVERY = 50


async def _latest_event(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    max_rev: int | None = None,
) -> ProjectEvent | None:
    """Return the highest-rev event for this entity, optionally bounded."""

    stmt = select(ProjectEvent).where(
        ProjectEvent.entity_type == entity_type,
        ProjectEvent.entity_id == entity_id,
    )
    if max_rev is not None:
        stmt = stmt.where(ProjectEvent.rev_no <= max_rev)
    stmt = stmt.order_by(ProjectEvent.rev_no.desc()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _latest_state_event(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    max_rev: int | None = None,
) -> ProjectEvent | None:
    """Return the highest-rev event that carries a full ``state`` snapshot.

    This is any event with ``state IS NOT NULL`` — typically ``create`` or
    ``snapshot`` ops, but ``revert`` events also carry the full target state.
    """

    stmt = select(ProjectEvent).where(
        ProjectEvent.entity_type == entity_type,
        ProjectEvent.entity_id == entity_id,
        ProjectEvent.state.isnot(None),
    )
    if max_rev is not None:
        stmt = stmt.where(ProjectEvent.rev_no <= max_rev)
    stmt = stmt.order_by(ProjectEvent.rev_no.desc()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _patches_between(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    after_rev: int,
    upto_rev: int | None = None,
) -> list[ProjectEvent]:
    """Return patch-bearing events with rev_no in (after_rev, upto_rev], ascending."""

    stmt = select(ProjectEvent).where(
        ProjectEvent.entity_type == entity_type,
        ProjectEvent.entity_id == entity_id,
        ProjectEvent.rev_no > after_rev,
        ProjectEvent.patch.isnot(None),
    )
    if upto_rev is not None:
        stmt = stmt.where(ProjectEvent.rev_no <= upto_rev)
    stmt = stmt.order_by(ProjectEvent.rev_no.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _replay(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    max_rev: int | None = None,
) -> dict[str, Any] | None:
    """Fold the event stream for an entity into its current state."""

    snapshot = await _latest_state_event(
        db, entity_type=entity_type, entity_id=entity_id, max_rev=max_rev,
    )
    if snapshot is None:
        return None
    state: dict[str, Any] = dict(snapshot.state or {})
    snap_rev = snapshot.rev_no or 0
    patches = await _patches_between(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        after_rev=snap_rev,
        upto_rev=max_rev,
    )
    for ev in patches:
        if ev.patch is None:
            continue
        state = jsonpatch.JsonPatch(ev.patch).apply(state)
    return state


async def apply_event(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: str,
    op: str,
    actor_id: uuid.UUID | None = None,
    new_state: dict[str, Any] | None = None,
    patch: list[Any] | None = None,
    message: str = "",
) -> ProjectEvent:
    """Append a versioned event for an entity and return the inserted row.

    Does NOT commit — the surrounding handler owns the transaction. The
    caller is expected to update its read-model and commit both writes
    together.
    """

    if entity_type not in ALL_ENTITY_TYPES:
        raise ValueError(
            f"entity_type {entity_type!r} not in {sorted(ALL_ENTITY_TYPES)}"
        )
    if op not in ALL_OPS:
        raise ValueError(f"op {op!r} not in {sorted(ALL_OPS)}")

    latest = await _latest_event(
        db, entity_type=entity_type, entity_id=entity_id,
    )
    next_rev = ((latest.rev_no or 0) + 1) if latest is not None else 1
    parent_event_id = latest.id if next_rev > 1 and latest is not None else None

    stored_state: dict[str, Any] | None
    stored_patch: list[Any] | None

    if op == OP_CREATE:
        if new_state is None:
            raise ValueError("op=create requires new_state")
        stored_state = new_state
        stored_patch = None
    elif op == OP_PATCH:
        if new_state is None:
            raise ValueError("op=patch requires new_state")
        prev_state = await current_state(db, entity_type, entity_id) or {}
        patch_obj = jsonpatch.make_patch(prev_state, new_state)
        stored_state = None
        stored_patch = list(patch_obj.patch)
    elif op == OP_REVERT:
        if new_state is None or patch is None:
            raise ValueError("op=revert requires both new_state and patch")
        stored_state = new_state
        stored_patch = patch
    elif op == OP_SNAPSHOT:
        if new_state is None:
            raise ValueError("op=snapshot requires new_state")
        stored_state = new_state
        stored_patch = None
    else:  # pragma: no cover — ALL_OPS check above is exhaustive.
        raise ValueError(f"unhandled op {op!r}")

    event = ProjectEvent(
        project_id=project_id,
        actor_id=actor_id,
        type=f"{entity_type}.{op}",
        payload={},
        entity_type=entity_type,
        entity_id=entity_id,
        rev_no=next_rev,
        parent_event_id=parent_event_id,
        op=op,
        patch=stored_patch,
        state=stored_state,
        message=message or None,
    )
    db.add(event)
    await db.flush()

    if op != OP_SNAPSHOT and next_rev % _SNAPSHOT_EVERY == 0:
        snap_state = stored_state
        if snap_state is None:
            snap_state = await current_state(db, entity_type, entity_id) or {}
        snapshot_event = ProjectEvent(
            project_id=project_id,
            actor_id=actor_id,
            type=f"{entity_type}.{OP_SNAPSHOT}",
            payload={},
            entity_type=entity_type,
            entity_id=entity_id,
            rev_no=next_rev + 1,
            parent_event_id=event.id,
            op=OP_SNAPSHOT,
            patch=None,
            state=snap_state,
            message=f"auto-snapshot @ rev {next_rev + 1}",
        )
        db.add(snapshot_event)
        await db.flush()

    return event


async def current_state(
    db: AsyncSession,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any] | None:
    """Return the folded current state for an entity, or None if no events."""

    latest = await _latest_event(
        db, entity_type=entity_type, entity_id=entity_id,
    )
    if latest is None:
        return None
    if latest.op in (OP_SNAPSHOT, OP_CREATE) and latest.state is not None:
        return dict(latest.state)
    return await _replay(db, entity_type=entity_type, entity_id=entity_id)


async def state_at_rev(
    db: AsyncSession,
    entity_type: str,
    entity_id: str,
    rev_no: int,
) -> dict[str, Any] | None:
    """Return the folded state of an entity at exactly ``rev_no``."""

    return await _replay(
        db, entity_type=entity_type, entity_id=entity_id, max_rev=rev_no,
    )


async def diff_revs(
    db: AsyncSession,
    entity_type: str,
    entity_id: str,
    from_rev: int,
    to_rev: int,
) -> dict[str, Any]:
    """Return the RFC 6902 patch and folded states between two revisions."""

    before = await state_at_rev(db, entity_type, entity_id, from_rev)
    after = await state_at_rev(db, entity_type, entity_id, to_rev)
    patch_obj = jsonpatch.make_patch(before or {}, after or {})
    return {"patch": list(patch_obj.patch), "before": before, "after": after}


async def revert_to_rev(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: str,
    target_rev: int,
    actor_id: uuid.UUID | None = None,
    message: str = "",
) -> ProjectEvent:
    """Append a revert event that re-applies the entity state at ``target_rev``."""

    target_state = await state_at_rev(db, entity_type, entity_id, target_rev)
    if target_state is None:
        raise ValueError("rev not found")
    current = await current_state(db, entity_type, entity_id)
    inverse_patch = list(
        jsonpatch.make_patch(current or {}, target_state).patch
    )
    return await apply_event(
        db,
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        op=OP_REVERT,
        new_state=target_state,
        patch=inverse_patch,
        actor_id=actor_id,
        message=message or f"revert to rev {target_rev}",
    )


async def event_timeline(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    limit: int = 200,
    before_rev: int | None = None,
) -> list[ProjectEvent]:
    """Return events for this entity, newest first."""

    stmt = select(ProjectEvent).where(
        ProjectEvent.entity_type == entity_type,
        ProjectEvent.entity_id == entity_id,
    )
    if before_rev is not None:
        stmt = stmt.where(ProjectEvent.rev_no < before_rev)
    stmt = stmt.order_by(ProjectEvent.rev_no.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
