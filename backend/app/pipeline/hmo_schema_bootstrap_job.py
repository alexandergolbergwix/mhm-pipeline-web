"""Background job wrapper for the live HMO Wikibase schema bootstrap.

A live bootstrap makes one sequential ``wbeditentity`` call per missing
ontology class/property (~380 today) against ``mhm-hmo.wikibase.cloud`` —
comfortably over Heroku's 30s HTTP request timeout, so it must run as a
``run_jobs`` background task (mirrors ``authority_re_enrich_job.py``'s
shape) instead of inline in the request/response cycle.
"""

from __future__ import annotations

import uuid

from app.db import session_scope
from app.models.run_job import JOB_STATUS_CANCELLED, JOB_STATUS_FAILED, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline import hmo_schema_bootstrap as pipeline
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.models.wikibase_cloud_write import CHANNEL_SCHEMA_BOOTSTRAP
from app.services.wikibase_audit import WikibaseAuditContext
from app.services.wikibase_credentials import build_server_wikibase_writer


async def run_hmo_schema_bootstrap_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        actor_user_id = job.created_by
        project_id = job.project_id
        run_id = job.run_id

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
        "message": "Reading ontology…",
    })

    last_seen_total = 0
    audit_ctx = WikibaseAuditContext(
        actor_user_id=actor_user_id,
        project_id=project_id,
        run_id=run_id,
        job_id=job_id,
        channel=CHANNEL_SCHEMA_BOOTSTRAP,
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

    async with session_scope() as db:
        result = await pipeline.bootstrap_schema(
            db, writer=writer, dry_run=False,
            on_progress=on_progress, should_cancel=should_cancel,
            audit_ctx=audit_ctx,
        )

    pipeline.cache_schema_bootstrap_report(result)

    cancelled = await is_cancel_requested(job_id)
    processed_count = result.created + result.skipped + result.failed
    await finish_job(
        job_id,
        status=JOB_STATUS_CANCELLED if cancelled else JOB_STATUS_SUCCEEDED,
        result={
            "dry_run": False,
            "created": result.created,
            "skipped": result.skipped,
            "failed": result.failed,
            "would_create": 0,
            "entries": [pipeline.serialise_bootstrap_entry(e) for e in result.entries],
        },
        progress={
            "phase": "cancelled" if cancelled else "done",
            "processed": processed_count,
            "total": last_seen_total or processed_count,
            "message": "Cancelled by user" if cancelled else "Complete",
        },
    )
