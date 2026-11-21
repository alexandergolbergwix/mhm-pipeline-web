"""Build a durable Publication Release outside the HTTP request."""

from __future__ import annotations

import asyncio
import uuid

from app.db import session_scope
from app.models.run_job import JOB_STATUS_FAILED, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline.run_job_service import finish_job, update_job_progress
from app.pipeline.wikidata_item_views import StudioBuildMissingError, fetch_merged_wikidata_item
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.publication.credentials import configured_publication_gateway_factory
from app.publication.runtime import PublicationRuntime
from app.schemas.publication import PreparePublicationRequest
from converter.wikidata import troki_author_repair as troki


async def run_wikidata_publication_prepare_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        params = job.params or {}
        actor_id = str(params.get("actor_id") or "").strip()
        payload = params.get("request")
        if not actor_id or not isinstance(payload, dict):
            await finish_job(
                job_id,
                status=JOB_STATUS_FAILED,
                error="Invalid Publication prepare job parameters",
            )
            return
        await update_job_progress(job_id, {
            "phase": "preparing",
            "message": "Verify the audited source and prepare the Publication release."
            if "_selected_existing" in params else "Prepare the immutable Publication Release.",
        })
        freshness_timeout = None
        troki_author_only = params.get("_gila_troki_author") is True
        if "_selected_existing" in params:
            from app.pipeline.marc_verify_context import load_run_marc_records
            from app.pipeline.wikidata_gila_audit_job import gila_record_fingerprint
            from app.routers.wikidata_studio import STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS

            expected_fingerprint = params.get("_gila_source_fingerprint")
            if expected_fingerprint is not None:
                records = await load_run_marc_records(db, job.run_id)
                if gila_record_fingerprint(records) != expected_fingerprint:
                    await finish_job(
                        job_id, status=JOB_STATUS_FAILED,
                        error="The source changed. Run a new audit before preparation.",
                    )
                    return
            for local_id, qid in params["_selected_existing"].items():
                try:
                    item = await fetch_merged_wikidata_item(
                        db, job.run_id, local_id, source="canonical",
                    )
                except StudioBuildMissingError:
                    item = None
                mapped = (
                    item and not item.get("rebuild_required") and item.get("existing_qid") == qid
                )
                if troki_author_only:
                    mapped = (
                        qid == troki.WORK_QID and item and troki.author_statement(item) is not None
                    )
                if not mapped:
                    await finish_job(
                        job_id, status=JOB_STATUS_FAILED,
                        error="The selected item changed. Rebuild Studio and run a new audit.",
                    )
                    return
            freshness_timeout = STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS
        if troki_author_only:
            reader = ReferenceItemReader()
            work, manuscript, author = await asyncio.gather(
                reader.fetch(troki.WORK_QID), reader.fetch(troki.MANUSCRIPT_QID),
                reader.fetch(troki.AUTHOR_QID),
            )
            if not (
                troki.work_matches(work, require_missing=True)
                and troki.manuscript_links_work(manuscript)
                and troki.author_matches(author)
            ):
                await finish_job(
                    job_id, status=JOB_STATUS_FAILED,
                    error="The repair evidence changed. Run a new audit before preparation.",
                )
                return
        runtime = PublicationRuntime(
            session=db, gateway_factory=configured_publication_gateway_factory,
            freshness_timeout_seconds=freshness_timeout,
        )
        response = await runtime.prepare(
            run_id=job.run_id,
            request=PreparePublicationRequest.model_validate(payload),
            actor_id=actor_id,
            **({
                "selected_existing": params["_selected_existing"],
                "expected_snapshot": tuple(params["_gila_build_snapshot"]),
                "troki_author_only": troki_author_only,
            } if "_selected_existing" in params else {}),
        )
    publication = response.publication
    if publication is None:
        await finish_job(
            job_id, status=JOB_STATUS_FAILED, error="Publication preparation returned no Release",
        )
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
