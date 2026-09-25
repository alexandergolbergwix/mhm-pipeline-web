"""Background job wrapper for the HMO item upload (dry-run or live).

Live uploads make one sequential Wikibase Cloud write per item plus one
per deferred link — far over Heroku's 30s HTTP timeout. Dry-run walks the
same corpus with SHACL gates and must also stay off the request path for
large runs (Rule W-107).
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
from app.pipeline.hmo_item_upload import STEP_ADD_LINKS, STEP_UPLOAD_ITEMS
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.services.wikibase_audit import WikibaseAuditContext
from app.services.wikibase_credentials import build_server_wikibase_writer


def _upload_steps(
    *,
    current_step: str | None,
    items_done: int,
    items_total: int,
    links_done: int,
    links_total: int,
    terminal: str | None = None,
) -> list[dict]:
    """``steps[]`` strip for the upload UI (same shape as the Wikidata
    upload modal's payload): pass 1 uploads items, pass 2 adds the
    deferred item-to-item links. The outer ``processed/total`` counter
    stays write-proportional (items + links); this strip is the per-step
    view so the two scales never read as one number.
    """
    step_no = 0
    if current_step == STEP_UPLOAD_ITEMS:
        step_no = 1
    elif current_step == STEP_ADD_LINKS:
        step_no = 2
    if terminal == "succeeded":
        step1_status = step2_status = "done"
    elif terminal is not None:  # cancelled/failed: incomplete steps are skipped
        step1_status = "done" if (items_total > 0 and items_done >= items_total) else "skipped"
        step2_status = "done" if (links_total > 0 and links_done >= links_total) else "skipped"
    else:
        step1_status = "running" if step_no == 1 else ("done" if step_no == 2 else "pending")
        step2_status = "running" if step_no == 2 else "pending"
    return [
        {
            "id": STEP_UPLOAD_ITEMS,
            "label": "Upload items",
            "status": step1_status,
            "processed": items_done,
            "total": items_total,
            "unit": "items",
        },
        {
            "id": STEP_ADD_LINKS,
            "label": "Add item links",
            "status": step2_status,
            "processed": links_done,
            "total": links_total,
            "unit": "links",
        },
    ]


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
        params = job.params or {}
        dry_run = bool(params.get("dry_run", True))
        update_existing = bool(params.get("update_existing", False))
        allow_shacl_errors = bool(params.get("allow_shacl_errors", False))
        raw_ids = params.get("local_ids")
        local_ids = [str(x) for x in raw_ids] if isinstance(raw_ids, list) and raw_ids else None

    writer = None
    if not dry_run:
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
        "message": "Loading item build…" if not dry_run else "Previewing item upload…",
    })

    last_seen_total = 0
    recent_item_outcomes: list[dict] = []
    current_step: str | None = None
    items_done = items_total = links_done = links_total = 0
    audit_ctx = None
    if not dry_run:
        audit_ctx = WikibaseAuditContext(
            actor_user_id=actor_user_id,
            project_id=project_id,
            run_id=run_id,
            job_id=job_id,
            channel=CHANNEL_ITEM_UPLOAD,
        )

    async def on_progress(
        processed: int,
        total: int,
        message: str,
        *,
        item_outcome: dict | None = None,
        step_id: str | None = None,
        step_processed: int | None = None,
        step_total: int | None = None,
    ) -> None:
        nonlocal last_seen_total, current_step, items_done, items_total, links_done, links_total
        last_seen_total = total
        if step_id:
            current_step = step_id
            if step_id == STEP_UPLOAD_ITEMS:
                items_done, items_total = step_processed or 0, step_total or 0
            elif step_id == STEP_ADD_LINKS:
                links_done, links_total = step_processed or 0, step_total or 0
        progress: dict = {
            "phase": "running",
            "processed": processed,
            "total": total,
            "message": message,
            "steps": _upload_steps(
                current_step=current_step,
                items_done=items_done,
                items_total=items_total,
                links_done=links_done,
                links_total=links_total,
            ),
        }
        if item_outcome is not None:
            recent_item_outcomes.append(item_outcome)
            progress["item_outcome"] = item_outcome
            # Rolling window so a missed poll can still catch up without
            # rewriting thousands of outcomes into every progress row.
            progress["recent_item_outcomes"] = recent_item_outcomes[-200:]
        await update_job_progress(job_id, progress)

    async def should_cancel() -> bool:
        return await is_cancel_requested(job_id)

    build_missing: str | None = None
    async with session_scope() as db:
        try:
            result = await pipeline.upload_items_for_run(
                db, run_id, writer=writer, dry_run=dry_run,
                update_existing=update_existing,
                allow_shacl_errors=allow_shacl_errors,
                local_ids=local_ids,
                audit_ctx=audit_ctx,
                on_progress=on_progress, should_cancel=should_cancel,
            )
        except pipeline.ItemBuildMissingError as exc:
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
            "message": (
                "Cancelled by user" if result.cancelled
                else ("Preview complete" if dry_run else "Upload complete")
            ),
            "steps": _upload_steps(
                current_step=current_step,
                items_done=items_done,
                items_total=items_total,
                links_done=links_done,
                links_total=links_total,
                terminal="cancelled" if result.cancelled else "succeeded",
            ),
        },
    )
