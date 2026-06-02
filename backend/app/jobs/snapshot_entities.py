"""3x/day archive snapshot for every entity touched since the previous slot.

Slot windows (UTC):
  0  ->  00:00-07:59  (run at 00:05)
  1  ->  08:00-15:59  (run at 08:05)
  2  ->  16:00-23:59  (run at 16:05)

For every (project_id, entity_type, entity_id) with at least one event
whose created_at is in the just-finished slot window, write a full-state
row into entity_snapshot. Idempotent on (entity_type, entity_id, bucket,
slot) via ON CONFLICT DO UPDATE -- re-running the same slot is safe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity_snapshot import EntitySnapshot
from app.models.event import ProjectEvent
from app.versioning import current_state

logger = logging.getLogger(__name__)

SLOT_HOURS = 8


async def snapshot_touched_entities(db: AsyncSession) -> dict[str, int]:
    """Write a snapshot row for every entity mutated in the current slot.

    Idempotent: re-running the same slot upserts the existing row rather
    than creating a duplicate, so Heroku Scheduler retries are safe.
    """

    now = datetime.now(timezone.utc)
    bucket = now.date()
    slot = now.hour // SLOT_HOURS  # 0, 1, or 2
    # Touched-since cutoff: the start of the CURRENT slot window. If we
    # run at 00:05 UTC, snapshot anything touched since 00:00 UTC today.
    slot_start = now.replace(
        hour=slot * SLOT_HOURS, minute=0, second=0, microsecond=0,
    )

    # Distinct (project_id, entity_type, entity_id) tuples with at least
    # one event in [slot_start, now].
    touched_stmt = (
        select(
            ProjectEvent.project_id,
            ProjectEvent.entity_type,
            ProjectEvent.entity_id,
        )
        .where(ProjectEvent.created_at >= slot_start)
        .where(ProjectEvent.entity_type.is_not(None))
        .where(ProjectEvent.entity_id.is_not(None))
        .group_by(
            ProjectEvent.project_id,
            ProjectEvent.entity_type,
            ProjectEvent.entity_id,
        )
    )
    touched = (await db.execute(touched_stmt)).all()

    snapshots_written = 0
    for project_id, entity_type, entity_id in touched:
        state = await current_state(db, entity_type, entity_id)
        if state is None:
            # No fold available (entity was hard-deleted before we got
            # here, or the only events were non-state-bearing). Skip.
            continue

        # Latest rev_no for this entity across the whole event log.
        rev_stmt = (
            select(func.max(ProjectEvent.rev_no))
            .where(ProjectEvent.entity_type == entity_type)
            .where(ProjectEvent.entity_id == entity_id)
        )
        rev_no = (await db.execute(rev_stmt)).scalar() or 1

        upsert_stmt = (
            pg_insert(EntitySnapshot)
            .values(
                project_id=project_id,
                entity_type=entity_type,
                entity_id=entity_id,
                bucket=bucket,
                slot=slot,
                rev_no=rev_no,
                state=state,
            )
            .on_conflict_do_update(
                index_elements=["entity_type", "entity_id", "bucket", "slot"],
                set_={
                    "state": state,
                    "rev_no": rev_no,
                    "created_at": now,
                },
            )
            .returning(EntitySnapshot.created_at)
        )
        await db.execute(upsert_stmt)
        snapshots_written += 1

    await db.commit()

    summary: dict[str, int] = {
        "snapshots_written": snapshots_written,
        "entities_touched": len(touched),
    }
    logger.info(
        "snapshot_touched_entities: bucket=%s slot=%d %s",
        bucket, slot, summary,
    )
    return summary
