"""Background HMO -> Wikidata projection-coverage build job.

The coverage report parses the run's TTL twice (once to build real
Wikidata item drafts, once to count RDF classes against them — see
``hmo_studio.coverage_report_for_run``). On a large manuscript corpus
that comfortably exceeds Heroku's 30s router timeout, which used to
leave the request's ``Depends(get_session)`` connection pinned for the
full 30s and the frontend blindly retrying every 30s forever (the same
connection-pool-exhaustion failure mode as the AI-verify SSE streams).
Routing it through the existing run-job system fixes both: the router
returns immediately, and the frontend polls a job instead of hammering
the slow endpoint.
"""

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
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress

logger = logging.getLogger(__name__)


async def run_hmo_coverage_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    await update_job_progress(job_id, {
        "phase": "building",
        "processed": 0,
        "total": 1,
        "message": "Building HMO -> Wikidata projection coverage…",
    })

    from app.pipeline import hmo_studio as hmo_pipeline  # noqa: PLC0415
    from app.pipeline.rdf_build import rdf_output_path_for_run  # noqa: PLC0415

    try:
        ttl_path = rdf_output_path_for_run(str(run_id))
        report = await hmo_pipeline.coverage_report_for_run(ttl_path=ttl_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("hmo coverage build job failed for run %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    hmo_pipeline.cache_coverage_report(str(run_id), report)

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result=report,
        progress={
            "phase": "done",
            "processed": 1,
            "total": 1,
            "message": "Coverage report ready",
        },
    )
