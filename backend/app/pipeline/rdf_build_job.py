"""Background RDF build job."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.rdf_build import (
    build_rdf_graph,
    rdf_build_signature,
    rdf_output_path_for_run,
)
from app.pipeline.rdf_build_batches import (
    iter_rdf_build_batches,
    load_rdf_triple_overrides,
    run_record_bounds,
)
from app.pipeline.rdf_build_shard import (
    persist_rdf_artifact_and_bust_caches,
    rdf_build_options_from_params,
)
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)

logger = logging.getLogger(__name__)


async def run_rdf_build_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        if await is_cancel_requested(job_id):
            await finish_job(job_id, status=JOB_STATUS_CANCELLED)
            return

        run_id = job.run_id
        params = job.params or {}
        # Batch-build (R23): the corpus is never materialised — the loader
        # pages it by the (run_id, control_number) keyset while the mapper
        # streams record subgraphs. Here we only need the shape of the
        # corpus (one server-side aggregate) + the small override set.
        total, first_cn, last_cn = await run_record_bounds(db, run_id)
        if total == 0:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="run has no records")
            return

        overrides = await load_rdf_triple_overrides(db, run_id)
        opts = rdf_build_options_from_params(params)

        # Streaming resume (job-service R26): a crashed run's checkpoint in
        # job.progress lets the retry skip already-mapped records. The
        # signature pins the corpus + options; any change starts fresh.
        signature = rdf_build_signature(run_id, total, first_cn, last_cn, opts)
        prev_checkpoint = (
            job.progress.get("checkpoint")
            if isinstance(job.progress, dict) else None
        ) or {}
        resume: dict[str, Any] | None = None
        if (
            prev_checkpoint.get("signature") == signature
            and int(prev_checkpoint.get("record_index") or 0) > 0
            and not bool(params.get("force_rebuild"))
        ):
            resume = {k: prev_checkpoint[k] for k in ("record_index", "file_bytes", "manuscripts")}
        checkpoint: dict[str, Any] = {"signature": signature}

    await update_job_progress(job_id, {
        "phase": "building",
        "processed": 0,
        "total": total,
        "message": f"Building RDF for {total} records…",
    })

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    out_path = rdf_output_path_for_run(str(run_id))
    try:
        async def _report_progress(payload: dict[str, Any]) -> None:
            await update_job_progress(job_id, payload)

        if resume is not None:
            await update_job_progress(job_id, {
                "phase": "building",
                "processed": int(resume.get("record_index") or 0),
                "total": total,
                "message": f"Resuming RDF build at record {resume.get('record_index')}…",
            })

        result = await build_rdf_graph(
            output_path=out_path,
            batch_source=iter_rdf_build_batches(run_id),
            overrides=overrides,
            build_options=opts,
            total_records=total,
            on_progress=_report_progress,
            resume=resume,
            checkpoint=checkpoint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("RDF build job failed for run %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    await persist_rdf_artifact_and_bust_caches(run_id, out_path, result)

    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result=result.to_dict(),
        progress={
            "phase": "done",
            "processed": total,
            "total": total,
            "message": "RDF build complete",
        },
    )
