"""Background Wikidata Studio build job."""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

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
        auth = SimpleNamespace(user=SimpleNamespace(id=job.created_by))

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
        from app.routers.wikidata_studio import build_studio  # noqa: PLC0415

        async with session_scope() as db:
            result = await build_studio(
                run_id=run_id,
                approved_only=approved_only,
                force_rebuild=force_rebuild,
                entity_type=None,
                q=None,
                sort="label",
                sort_dir="asc",
                page=1,
                page_size=500,
                auth=auth,
                db=db,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata studio build job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    summary = result.summary.model_dump() if hasattr(result.summary, "model_dump") else dict(result.summary)
    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={
            "total": result.total,
            "record_count": result.record_count,
            "approved_match_count": result.approved_match_count,
            "summary": summary,
        },
        progress={
            "phase": "done",
            "processed": result.total,
            "total": result.total,
            "message": f"Built {result.total} items",
        },
    )
