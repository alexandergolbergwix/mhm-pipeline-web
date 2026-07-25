"""Background job: HMO Wikibase item build / rebuild."""

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
from app.pipeline.hmo_item_build_exec import HmoItemBuildError, execute_hmo_item_build
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress

logger = logging.getLogger(__name__)


async def run_hmo_item_build_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        params = dict(job.params or {})
        force_rebuild = bool(params.get("force_rebuild", False))
        refresh_authority = bool(params.get("refresh_authority", True))

    await update_job_progress(job_id, {
        "phase": "starting",
        "processed": 0,
        "total": 3,
        "unit": "steps",
        "message": "Starting HMO item build (3 steps)…",
    })

    async def on_progress(
        phase: str,
        processed: int,
        total: int,
        message: str,
        *,
        sub_processed: int | None = None,
        sub_total: int | None = None,
        sub_unit: str | None = None,
        sub_message: str | None = None,
    ) -> None:
        progress: dict = {
            "phase": phase,
            "processed": processed,
            "total": total,
            "unit": "steps",
            "message": message,
        }
        if sub_processed is not None:
            progress["sub_processed"] = sub_processed
        if sub_total is not None:
            progress["sub_total"] = sub_total
        if sub_unit:
            progress["sub_unit"] = sub_unit
        if sub_message:
            progress["sub_message"] = sub_message
        await update_job_progress(job_id, progress)

    async def should_cancel() -> bool:
        return await is_cancel_requested(job_id)

    try:
        async with session_scope() as db:
            result = await execute_hmo_item_build(
                db,
                run_id,
                force_rebuild=force_rebuild,
                refresh_authority=refresh_authority,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
    except HmoItemBuildError as exc:
        if str(exc) == "cancelled" or await is_cancel_requested(job_id):
            await finish_job(job_id, status=JOB_STATUS_CANCELLED)
            return
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("hmo item build job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={
            "from_cache": result.from_cache,
            "entity_count": result.entity_count,
            "deferred_link_count": result.deferred_link_count,
            "skipped_statement_count": result.skipped_statement_count,
            "refreshed_authority": result.refreshed_authority,
            "rebuilt_rdf": result.rebuilt_rdf,
        },
        progress={
            "phase": "done",
            "processed": 3,
            "total": 3,
            "unit": "steps",
            "message": (
                f"Built {result.entity_count} entities"
                + (" (cached)" if result.from_cache else "")
            ),
        },
    )
