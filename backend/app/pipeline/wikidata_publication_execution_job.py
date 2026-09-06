"""Run a queued, durable Wikidata Publication Execution."""

from __future__ import annotations

import uuid

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.publication.credentials import ExecutionCredentialResolver, configured_publication_gateway_factory
from app.publication.wikidata_gateway import CurrentWikidataBoundaryFactory, WikidataGatewayAdapter
from app.publication.runtime import PublicationRuntime
from app.publication.types import TargetRef
from app.publication.gateway import WikidataGateway


def _required_param(job: RunJob, name: str) -> str:
    value = str((job.params or {}).get(name) or "").strip()
    if not value:
        raise ValueError(f"The Publication execution job lacks {name}")
    return value


def _progress(summary) -> dict[str, object]:
    execution = summary.execution
    if execution is None:
        return {"phase": "failed", "message": "The Publication Execution is absent."}
    return {
        "phase": execution.status,
        "processed": execution.processed,
        "total": execution.total,
        "unit": "entities",
        "current_label": execution.current_entity_label,
        "message": f"Publication Execution is {execution.status}.",
    }


async def run_wikidata_publication_execution_job(job_id: uuid.UUID) -> None:
    """Resume a queued Execution and write only through the gateway seam."""
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        publication_id = _required_param(job, "publication_id")
        execution_id = _required_param(job, "execution_id")
        actor_id = _required_param(job, "actor_id")
        run_id = job.run_id
        if await is_cancel_requested(job_id):
            await finish_job(
                job_id,
                status=JOB_STATUS_CANCELLED,
                result={"publication_id": publication_id, "execution_id": execution_id},
                progress={"phase": "cancelled", "message": "Publication Execution cancelled."},
            )
            return
        envelope = (job.params or {}).get("_publication_credential")
        gateway_factory = configured_publication_gateway_factory
        if envelope is not None:
            if not isinstance(envelope, str) or job.created_by != uuid.UUID(actor_id):
                raise ValueError("The Publication execution credential account is invalid")
            resolver = ExecutionCredentialResolver(envelope,
                publication_id=publication_id, execution_id=execution_id, actor_id=actor_id)

            def credential_gateway_factory(*, target: TargetRef, actor_id: str) -> WikidataGateway:
                return WikidataGatewayAdapter(credential_resolver=resolver,
                    boundary_factory=CurrentWikidataBoundaryFactory())

            gateway_factory = credential_gateway_factory

        runtime = PublicationRuntime(session=db, gateway_factory=gateway_factory)
        await update_job_progress(
            job_id,
            {
                "phase": "running",
                "processed": 0,
                "total": 0,
                "unit": "entities",
                "message": "Publication Execution is starting.",
            },
        )
        summary = await runtime.execute(
            run_id=run_id,
            publication_id=publication_id,
            execution_id=execution_id,
            actor_id=actor_id,
            worker_id=f"publication-job:{job_id}",
        )
    execution = summary.execution
    if execution is None:
        await finish_job(
            job_id,
            status=JOB_STATUS_FAILED,
            error="The Publication Execution is absent.",
            result={"publication_id": publication_id, "execution_id": execution_id},
        )
        return
    terminal = {
        "succeeded": JOB_STATUS_SUCCEEDED,
        "cancelled": JOB_STATUS_CANCELLED,
        "failed": JOB_STATUS_FAILED,
    }.get(execution.status, JOB_STATUS_FAILED)
    error = None if terminal != JOB_STATUS_FAILED else (
        "The Publication Execution paused. Resolve the audit Finding, then resume it."
    )
    await finish_job(
        job_id,
        status=terminal,
        result={"publication_id": publication_id, "execution_id": execution_id},
        error=error,
        progress=_progress(summary),
    )
