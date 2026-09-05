"""Build a durable Publication Release outside the HTTP request."""

from __future__ import annotations

import uuid

from app.db import session_scope
from app.models.run_job import JOB_STATUS_FAILED, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline.run_job_service import finish_job, update_job_progress
from app.publication.credentials import configured_publication_gateway_factory
from app.publication.runtime import PublicationRuntime
from app.schemas.publication import PreparePublicationRequest


async def run_wikidata_publication_prepare_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        params = job.params or {}
        actor_id = str(params.get("actor_id") or "").strip()
        payload = params.get("request")
        if not actor_id or not isinstance(payload, dict):
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="Invalid Publication prepare job parameters")
            return
        await update_job_progress(job_id, {"phase": "preparing", "message": "Preparing the immutable Publication Release."})
        runtime = PublicationRuntime(session=db, gateway_factory=configured_publication_gateway_factory)
        response = await runtime.prepare(
            run_id=job.run_id,
            request=PreparePublicationRequest.model_validate(payload),
            actor_id=actor_id,
        )
    publication = response.publication
    if publication is None:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error="Publication preparation returned no Release")
        return
    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={"publication_id": publication.publication_id},
        progress={
            "phase": "succeeded",
            "processed": publication.current_release.entity_count,
            "total": publication.current_release.entity_count,
            "unit": "entities",
            "message": "The immutable Publication Release is ready for review.",
        },
    )
