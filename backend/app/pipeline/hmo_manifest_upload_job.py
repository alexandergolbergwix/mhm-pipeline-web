"""Background job: upload IIIF manifests to HMO Wikibase Cloud."""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.models.wikibase_cloud_write import CHANNEL_MANIFEST_UPLOAD
from app.pipeline import hmo_studio as hmo_pipeline
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.services.wikibase_audit import WikibaseAuditContext
from app.services.wikibase_credentials import build_server_wikibase_writer

logger = logging.getLogger(__name__)


async def run_hmo_manifest_upload_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        project_id = job.project_id
        actor_id = job.created_by
        dry_run = bool((job.params or {}).get("dry_run", True))

    manifest_dir = hmo_pipeline.manifest_dir_for_run(str(run_id))
    if not manifest_dir.exists():
        await finish_job(
            job_id,
            status=JOB_STATUS_FAILED,
            error=(
                "No IIIF manifests for this run yet. Click "
                "“Build manifests” first."
            ),
        )
        return

    writer = None
    if not dry_run:
        try:
            writer = build_server_wikibase_writer()
        except Exception as exc:  # noqa: BLE001
            await finish_job(
                job_id,
                status=JOB_STATUS_FAILED,
                error=str(getattr(exc, "detail", exc)),
            )
            return

    files = sorted(manifest_dir.glob("MS_*.json"))
    total = len(files)
    await update_job_progress(job_id, {
        "phase": "starting",
        "processed": 0,
        "total": total,
        "message": (
            f"{'Previewing' if dry_run else 'Uploading'} {total} manifests…"
        ),
    })

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    # Audit intent before network (mirrors the sync router path).
    async with session_scope() as db:
        from app.routers.hmo_studio import _audit_manifest_upload_intent  # noqa: PLC0415

        await _audit_manifest_upload_intent(
            db,
            project_id=project_id,
            actor_id=actor_id,
            manifest_dir=manifest_dir,
            dry_run=dry_run,
        )

    audit_ctx = None
    if not dry_run:
        audit_ctx = WikibaseAuditContext(
            actor_user_id=actor_id,
            project_id=project_id,
            run_id=run_id,
            job_id=job_id,
            channel=CHANNEL_MANIFEST_UPLOAD,
        )

    async def on_progress(processed: int, total_n: int, message: str) -> None:
        await update_job_progress(job_id, {
            "phase": "uploading",
            "processed": processed,
            "total": total_n,
            "message": message,
        })

    async def should_cancel() -> bool:
        return await is_cancel_requested(job_id)

    try:
        async with session_scope() as db:
            result = await hmo_pipeline.upload_manifests_for_run(
                manifest_dir=manifest_dir,
                writer=writer,
                dry_run=dry_run,
                db=db,
                audit_ctx=audit_ctx,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
    except FileNotFoundError as exc:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("hmo manifest upload job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    if getattr(result, "cancelled", False) or await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    hmo_pipeline.cache_upload_report(str(run_id), result)
    processed = result.uploaded + result.unchanged + result.failed
    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result=asdict(result),
        progress={
            "phase": "done",
            "processed": processed,
            "total": max(total, processed),
            "message": (
                f"{'Preview' if dry_run else 'Upload'} complete: "
                f"{result.uploaded} ok, {result.unchanged} unchanged, "
                f"{result.failed} failed"
            ),
        },
    )
