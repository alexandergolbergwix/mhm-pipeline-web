"""Background Wikidata Studio build job."""

from __future__ import annotations

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
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata studio build job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

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
            "message": f"Built {total} items",
        },
    )
