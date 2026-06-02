"""Daily 03:05 UTC prune of the rolling 1000-event window.

For every ``(entity_type, entity_id)`` group with more than ``HARD_CAP``
events, this job deletes the oldest non-anchor events so the group falls
back to ``HARD_CAP`` rows. It NEVER deletes:

* any ``op="create"`` event — the entity's anchor, needed to replay
  from scratch.
* any ``op="snapshot"`` event — keepers for ``state_at_rev`` queries on
  any past revision. Long-lived entities may carry multiple snapshots;
  we preserve all of them so any historical replay stays possible.

The ``entity_snapshot`` table (referenced from ``ProjectSnapshot``) is
the cold archive and is NEVER pruned by this job — it grows at
3 rows/day/entity forever (3x/day snapshot job).
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import OP_CREATE, OP_SNAPSHOT, ProjectEvent

logger = logging.getLogger(__name__)

HARD_CAP = 1000
WARN_THRESHOLD = 5000  # log a warning above this (suggests a hot loop)


async def prune_events(db: AsyncSession) -> dict[str, int]:
    """Apply the rolling-window retention rule. Commits once at the end.

    Returns a counter dict for the caller / scheduler log.
    """

    # Identify (entity_type, entity_id) groups with > HARD_CAP events.
    group_stmt = (
        select(
            ProjectEvent.entity_type,
            ProjectEvent.entity_id,
            func.count(ProjectEvent.id).label("n"),
        )
        .where(ProjectEvent.entity_type.is_not(None))
        .where(ProjectEvent.entity_id.is_not(None))
        .group_by(ProjectEvent.entity_type, ProjectEvent.entity_id)
        .having(func.count(ProjectEvent.id) > HARD_CAP)
    )
    groups = (await db.execute(group_stmt)).all()

    total_deleted = 0
    entities_pruned = 0

    for entity_type, entity_id, n in groups:
        if n > WARN_THRESHOLD:
            logger.warning(
                "entity has %d events (>%d cap): type=%s id=%s",
                n,
                WARN_THRESHOLD,
                entity_type,
                entity_id,
            )

        # Gather all rows in this group in rev_no order. We will preserve
        # every create/snapshot row (anchors + replay keepers) and delete
        # the oldest non-anchor rows until total <= HARD_CAP.
        all_rows = (
            await db.execute(
                select(
                    ProjectEvent.id,
                    ProjectEvent.op,
                    ProjectEvent.rev_no,
                )
                .where(ProjectEvent.entity_type == entity_type)
                .where(ProjectEvent.entity_id == entity_id)
                .order_by(ProjectEvent.rev_no.asc())
            )
        ).all()

        if len(all_rows) <= HARD_CAP:
            continue

        non_anchor = [r for r in all_rows if r.op not in (OP_CREATE, OP_SNAPSHOT)]
        excess = len(all_rows) - HARD_CAP
        delete_ids = [r.id for r in non_anchor[:excess]]
        if not delete_ids:
            # Group is over cap but every row is an anchor — refuse to
            # prune. Log a warning so a human can inspect.
            logger.warning(
                "prune_events: entity over cap but all rows are anchors; "
                "skipping: type=%s id=%s total=%d",
                entity_type,
                entity_id,
                len(all_rows),
            )
            continue

        result = await db.execute(
            delete(ProjectEvent).where(ProjectEvent.id.in_(delete_ids))
        )
        deleted = result.rowcount or 0
        total_deleted += deleted
        if deleted > 0:
            entities_pruned += 1

    await db.commit()

    summary: dict[str, int] = {
        "entities_pruned": entities_pruned,
        "events_deleted": total_deleted,
    }
    logger.info("prune_events: %s", summary)
    return summary
