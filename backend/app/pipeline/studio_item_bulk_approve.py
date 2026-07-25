"""Approve many Studio item overrides in one background job.

Used by ``hmo_item_bulk_approve`` and ``wikidata_item_bulk_approve`` so the
curator UI never fires thousands of synchronous PATCH requests (Heroku H12 /
browser hang).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import (
    ENTITY_TYPE_HMO_ITEM_OVERRIDE,
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    OP_CREATE,
    OP_PATCH,
    ProjectEvent,
)
from app.models.hmo_studio_item_override import HmoStudioItemOverride
from app.models.item_override import WikidataItemOverride
from app.models.run import Run
from app.pipeline.hmo_item_merge import override_row_to_dict as hmo_override_row_to_dict
from app.pipeline.wikidata_item_merge import override_row_to_dict as wd_override_row_to_dict
from app.versioning import apply_event

logger = logging.getLogger(__name__)

Channel = Literal["hmo", "wikidata"]
ProgressCb = Callable[[int, int, str], Awaitable[None]]
CancelCb = Callable[[], Awaitable[bool]]

MAX_BULK_APPROVE_IDS = 5000
_BATCH_COMMIT = 25


async def _emit_override_event(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    entity_type: str,
    row_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    new_state: dict[str, Any],
    message: str,
) -> None:
    entity_id_str = str(row_id)
    try:
        has_history = (
            await db.execute(
                select(ProjectEvent.id)
                .where(
                    ProjectEvent.entity_type == entity_type,
                    ProjectEvent.entity_id == entity_id_str,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        await apply_event(
            db,
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id_str,
            op=OP_PATCH if has_history else OP_CREATE,
            new_state=new_state,
            actor_id=actor_id,
            message=message,
        )
    except Exception as exc:  # noqa: BLE001 — versioning must never abort the batch
        logger.warning("apply_event failed for %s %s: %s", entity_type, entity_id_str, exc)


async def _approve_hmo_one(
    db: AsyncSession,
    *,
    run: Run,
    local_id: str,
    actor_id: uuid.UUID | None,
) -> str:
    """Return ``approved`` | ``unchanged``."""
    row = (
        await db.execute(
            select(HmoStudioItemOverride).where(
                HmoStudioItemOverride.run_id == run.id,
                HmoStudioItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = HmoStudioItemOverride(
            run_id=run.id,
            local_id=local_id,
            updated_by=actor_id,
            approved=True,
        )
        db.add(row)
        await db.flush()
    elif row.approved is True:
        return "unchanged"
    else:
        row.approved = True
        row.updated_by = actor_id

    await _emit_override_event(
        db,
        project_id=run.project_id,
        entity_type=ENTITY_TYPE_HMO_ITEM_OVERRIDE,
        row_id=row.id,
        actor_id=actor_id,
        new_state=hmo_override_row_to_dict(row),
        message=f"hmo item bulk approve ({local_id})",
    )
    return "approved"


async def _approve_wikidata_one(
    db: AsyncSession,
    *,
    run: Run,
    local_id: str,
    actor_id: uuid.UUID | None,
) -> str:
    row = (
        await db.execute(
            select(WikidataItemOverride).where(
                WikidataItemOverride.run_id == run.id,
                WikidataItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = WikidataItemOverride(
            run_id=run.id,
            local_id=local_id,
            updated_by=actor_id,
            approved=True,
        )
        db.add(row)
        await db.flush()
    elif row.approved is True:
        return "unchanged"
    else:
        row.approved = True
        row.updated_by = actor_id

    await _emit_override_event(
        db,
        project_id=run.project_id,
        entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
        row_id=row.id,
        actor_id=actor_id,
        new_state=wd_override_row_to_dict(row),
        message=f"wikidata override bulk approve ({local_id})",
    )
    return "approved"


async def bulk_approve_items(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    channel: Channel,
    local_ids: list[str],
    actor_id: uuid.UUID | None,
    on_progress: ProgressCb | None = None,
    should_cancel: CancelCb | None = None,
) -> dict[str, Any]:
    """Approve each local_id. Caller owns the session; we commit in batches."""
    run = await db.get(Run, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")

    ids = [str(x).strip() for x in local_ids if str(x).strip()]
    # Preserve order, drop dupes
    seen: set[str] = set()
    unique_ids: list[str] = []
    for lid in ids:
        if lid in seen:
            continue
        seen.add(lid)
        unique_ids.append(lid)

    total = len(unique_ids)
    approved = 0
    unchanged = 0
    failed = 0
    cancelled = False

    if on_progress:
        await on_progress(0, total, f"Approving 0/{total}…")

    approve_one = _approve_hmo_one if channel == "hmo" else _approve_wikidata_one

    for i, local_id in enumerate(unique_ids):
        if should_cancel and await should_cancel():
            cancelled = True
            break
        try:
            async with db.begin_nested():
                outcome = await approve_one(
                    db, run=run, local_id=local_id, actor_id=actor_id,
                )
            if outcome == "approved":
                approved += 1
            else:
                unchanged += 1
        except Exception as exc:  # noqa: BLE001 — keep going
            failed += 1
            logger.warning(
                "bulk approve failed channel=%s local_id=%s: %s",
                channel, local_id, exc,
            )

        processed = i + 1
        if processed % _BATCH_COMMIT == 0 or processed == total or cancelled:
            await db.commit()
            if on_progress:
                await on_progress(
                    processed,
                    total,
                    f"Approved {approved}, skipped {unchanged}, failed {failed} "
                    f"({processed}/{total})",
                )

    return {
        "channel": channel,
        "total": total,
        "approved": approved,
        "unchanged": unchanged,
        "failed": failed,
        "cancelled": cancelled,
    }
