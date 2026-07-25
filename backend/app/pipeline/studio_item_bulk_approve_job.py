"""Background job: bulk-approve filtered Studio items (HMO or Wikidata)."""

from __future__ import annotations

import uuid

from app.db import session_scope
from app.models.run_job import (
    JOB_KIND_HMO_ITEM_BULK_APPROVE,
    JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.pipeline.studio_item_bulk_approve import Channel, bulk_approve_items


def _channel_for_kind(kind: str) -> Channel:
    if kind == JOB_KIND_HMO_ITEM_BULK_APPROVE:
        return "hmo"
    if kind == JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE:
        return "wikidata"
    raise ValueError(f"unsupported bulk-approve kind {kind!r}")


async def run_studio_item_bulk_approve_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        kind = job.kind
        run_id = job.run_id
        actor_user_id = job.created_by
        params = dict(job.params or {})
        local_ids = list(params.get("local_ids") or [])

    try:
        channel = _channel_for_kind(kind)
    except ValueError as exc:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    if not local_ids:
        await finish_job(
            job_id,
            status=JOB_STATUS_FAILED,
            error="local_ids is required and must be non-empty",
        )
        return

    await update_job_progress(job_id, {
        "phase": "running",
        "processed": 0,
        "total": len(local_ids),
        "message": f"Starting bulk approve ({len(local_ids)} items)…",
    })

    async def on_progress(processed: int, total: int, message: str) -> None:
        await update_job_progress(job_id, {
            "phase": "running",
            "processed": processed,
            "total": total,
            "message": message,
        })

    async def should_cancel() -> bool:
        return await is_cancel_requested(job_id)

    try:
        async with session_scope() as db:
            result = await bulk_approve_items(
                db,
                run_id=run_id,
                channel=channel,
                local_ids=[str(x) for x in local_ids],
                actor_id=actor_user_id,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
    except Exception as exc:  # noqa: BLE001
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    status = JOB_STATUS_CANCELLED if result.get("cancelled") else JOB_STATUS_SUCCEEDED
    processed = (
        int(result.get("approved") or 0)
        + int(result.get("unchanged") or 0)
        + int(result.get("failed") or 0)
    )
    total = int(result.get("total") or processed)
    await finish_job(
        job_id,
        status=status,
        result=result,
        progress={
            "phase": "cancelled" if status == JOB_STATUS_CANCELLED else "done",
            "processed": processed,
            "total": total,
            "message": (
                "Cancelled by user"
                if status == JOB_STATUS_CANCELLED
                else (
                    f"Done: approved {result.get('approved', 0)}, "
                    f"already approved {result.get('unchanged', 0)}, "
                    f"failed {result.get('failed', 0)}"
                )
            ),
        },
    )
