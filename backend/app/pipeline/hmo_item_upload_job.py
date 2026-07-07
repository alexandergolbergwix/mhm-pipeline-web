"""Background job wrapper for the live HMO item upload.

A live upload makes one sequential Wikibase Cloud write per item plus one
per deferred link (thousands for a full run) — far over Heroku's 30s HTTP
request timeout, so it must run as a ``run_jobs`` background task
(mirrors ``hmo_schema_bootstrap_job.py``'s shape). Dry-run previews stay
synchronous in the router: they make no network calls.
"""

from __future__ import annotations

import uuid

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.models.wikibase_cloud_write import CHANNEL_ITEM_UPLOAD
from app.pipeline import hmo_item_upload as pipeline
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.services.wikibase_audit import WikibaseAuditContext
from app.services.wikibase_credentials import build_server_wikibase_writer


def serialise_upload_result(result: pipeline.HmoItemUploadResult) -> dict:
    return {
        "dry_run": result.dry_run,
        "created": result.created,
        "updated": result.updated,
        "skipped": result.skipped,
        "failed": result.failed,
        "blocked": result.blocked,
        "linked": result.linked,
        "unresolved_links": result.unresolved_links,
        "outcomes": [o.__dict__ for o in result.outcomes],
        "link_outcomes": [o.__dict__ for o in result.link_outcomes],
    }


async def run_hmo_item_upload_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        actor_user_id = job.created_by
        project_id = job.project_id
        run_id = job.run_id
        update_existing = bool((job.params or {}).get("update_existing", False))
        allow_shacl_errors = bool((job.params or {}).get("allow_shacl_errors", False))

    try:
        writer = build_server_wikibase_writer()
    except Exception as exc:  # noqa: BLE001
        await finish_job(
            job_id, status=JOB_STATUS_FAILED,
            error=str(getattr(exc, "detail", exc)),
        )
        return

    await update_job_progress(job_id, {
        "phase": "running", "processed": 0, "total": 0,
        "message": "Loading item build…",
    })

    last_seen_total = 0
    audit_ctx = WikibaseAuditContext(
        actor_user_id=actor_user_id,
        project_id=project_id,
        run_id=run_id,
        job_id=job_id,
        channel=CHANNEL_ITEM_UPLOAD,
    )

    async def on_progress(processed: int, total: int, message: str) -> None:
        nonlocal last_seen_total
        last_seen_total = total
        await update_job_progress(job_id, {
            "phase": "running", "processed": processed, "total": total,
            "message": message,
        })

    async def should_cancel() -> bool:
        return await is_cancel_requested(job_id)

    build_missing: str | None = None
    async with session_scope() as db:
        try:
            result = await pipeline.upload_items_for_run(
                db, run_id, writer=writer, dry_run=False,
                update_existing=update_existing,
                allow_shacl_errors=allow_shacl_errors,
                audit_ctx=audit_ctx,
                on_progress=on_progress, should_cancel=should_cancel,
            )
        except pipeline.ItemBuildMissingError as exc:
            # Defer finish_job until the session closes — nesting a second
            # session_scope inside this one deadlocks the single shared
            # SQLite test connection.
            build_missing = str(exc)
    if build_missing is not None:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=build_missing)
        return

    processed_count = (
        result.created + result.updated + result.skipped + result.failed
        + result.blocked + result.linked
    )
    await finish_job(
        job_id,
        status=JOB_STATUS_CANCELLED if result.cancelled else JOB_STATUS_SUCCEEDED,
        result=serialise_upload_result(result),
        progress={
            "phase": "cancelled" if result.cancelled else "done",
            "processed": processed_count,
            "total": last_seen_total or processed_count,
            "message": "Cancelled by user" if result.cancelled else "Upload complete",
        },
    )
