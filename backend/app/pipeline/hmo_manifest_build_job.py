"""Background job: build IIIF manifests from a run's RDF TTL."""

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
from app.pipeline import hmo_studio as hmo_pipeline
from app.pipeline.rdf_build import ensure_ttl_on_disk, rdf_output_path_for_run
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress

logger = logging.getLogger(__name__)


async def run_hmo_manifest_build_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id

    await update_job_progress(job_id, {
        "phase": "starting",
        "processed": 0,
        "total": 1,
        "message": "Loading RDF graph…",
    })

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    ttl_path = rdf_output_path_for_run(str(run_id))
    async with session_scope() as db:
        await ensure_ttl_on_disk(ttl_path, run_id, db)

    if not ttl_path.exists():
        await finish_job(
            job_id,
            status=JOB_STATUS_FAILED,
            error=(
                "No RDF graph for this run yet. Build the RDF (RDF Graph) "
                "before generating IIIF manifests."
            ),
        )
        return

    await update_job_progress(job_id, {
        "phase": "building",
        "processed": 0,
        "total": 1,
        "message": "Generating IIIF manifests…",
    })

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    manifest_dir = hmo_pipeline.manifest_dir_for_run(str(run_id))
    try:
        result = await hmo_pipeline.build_manifests_for_run(
            ttl_path=ttl_path, manifest_dir=manifest_dir,
        )
    except FileNotFoundError as exc:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("hmo manifest build job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    count = int(getattr(result, "manifest_count", 0) or 0)
    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result=dict(result.__dict__),
        progress={
            "phase": "done",
            "processed": max(count, 1),
            "total": max(count, 1),
            "message": f"Built {count} manifests",
        },
    )
