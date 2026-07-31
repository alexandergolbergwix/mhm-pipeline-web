"""Background Wikidata Studio build job."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL_SECONDS = 1.5


async def _publish_build_progress(
    job_id: uuid.UUID,
    counter: dict[str, int],
) -> None:
    """Publish `x/n records` while the CPU build runs in the threadpool.

    ``builder.build_all`` reports progress from a worker thread, which cannot
    touch the async session — so the callback only bumps *counter* and this
    task owns every DB write (Rule W-112: 1-based steps with a unit label,
    Rule W-128: keep job polls light on the web dyno).
    """
    last = -1
    while True:
        await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)
        done, total = counter["done"], counter["total"]
        if done == last or not total:
            continue
        last = done
        await update_job_progress(job_id, {
            "phase": "building",
            "processed": done,
            "total": total,
            "unit": "records",
            "message": f"Building items — record {min(done + 1, total)}/{total}",
        })


async def run_wikidata_studio_build_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        params = job.params or {}
        approved_only = bool(params.get("approved_only", True))
        force_rebuild = bool(params.get("force_rebuild", False))
        run_user_id = job.created_by

    await update_job_progress(job_id, {
        "phase": "building",
        "processed": 0,
        "total": 1,
        "message": "Building Wikidata Studio items…",
    })

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    counter = {"done": 0, "total": 0}

    def on_record(done: int, total: int) -> None:
        counter["done"], counter["total"] = done, total

    publisher = asyncio.create_task(_publish_build_progress(job_id, counter))
    try:
        from app.routers.wikidata_studio import execute_studio_build  # noqa: PLC0415

        async with session_scope() as db:
            cached = await execute_studio_build(
                db,
                run_id=run_id,
                approved_only=approved_only,
                force_rebuild=force_rebuild,
                run_user_id=run_user_id,
                source=str(params.get("source") or "legacy"),
                # Never WDQS-reconcile the full corpus on the build path (Rule W-119).
                # Reconcile runs on upload / gated QS / the preview endpoint only.
                reconcile=False,
                progress_cb=on_record,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata studio build job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return
    finally:
        publisher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publisher

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    total = len(cached.result_items or [])
    summary = cached.summary or {}
    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={
            "total": total,
            "record_count": cached.record_count,
            "approved_match_count": cached.approved_match_count,
            "summary": summary,
        },
        progress={
            "phase": "done",
            "processed": total,
            "total": max(total, 1),
            "unit": "items",
            "message": f"Built {total} items from {cached.record_count} records",
        },
    )
