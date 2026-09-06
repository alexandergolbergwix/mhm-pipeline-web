"""Create a Publication plan and receipt outside the HTTP request."""

from __future__ import annotations

import uuid

from app.db import session_scope
from app.models.run_job import JOB_STATUS_CANCELLED, JOB_STATUS_FAILED, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.publication.credentials import ExecutionCredentialResolver
from app.publication.gateway import WikidataGateway
from app.publication.runtime import PublicationRuntime
from app.publication.types import TargetRef
from app.publication.wikidata_gateway import CurrentWikidataBoundaryFactory, WikidataGatewayAdapter
from app.schemas.publication import AdvancePublicationRequest, DryRunPublicationCommand


class DryRunCancelled(Exception):
    """The curator cancelled the read-only job."""


async def run_wikidata_publication_dry_run_job(job_id: uuid.UUID) -> None:
    async def progress(processed: int, total: int) -> None:
        if await is_cancel_requested(job_id):
            raise DryRunCancelled()
        await update_job_progress(
            job_id,
            {
                "phase": "dry_run",
                "processed": processed,
                "total": total,
                "unit": "entities",
                "message": "Check Wikidata entities before publication.",
            },
        )

    try:
        async with session_scope() as db:
            job = await db.get(RunJob, job_id)
            if job is None:
                return
            params = job.params or {}
            publication_id = str(params.get("publication_id") or "")
            actor_id = str(params.get("actor_id") or "")
            scope_id = str(params.get("credential_scope_id") or "")
            envelope = params.get("_publication_credential")
            if (
                not isinstance(envelope, str)
                or not scope_id.startswith("dry-run:")
                or job.created_by != uuid.UUID(actor_id)
            ):
                raise ValueError("The Publication dry-run credential account or scope is invalid")
            request = AdvancePublicationRequest.model_validate(params.get("request"))
            if not isinstance(request.command, DryRunPublicationCommand):
                raise ValueError("The Publication dry-run job accepts only a dry-run command")
            resolver = ExecutionCredentialResolver(
                envelope, publication_id=publication_id, execution_id=scope_id, actor_id=actor_id
            )

            def gateway_factory(*, target: TargetRef, actor_id: str) -> WikidataGateway:
                return WikidataGatewayAdapter(
                    credential_resolver=resolver, boundary_factory=CurrentWikidataBoundaryFactory()
                )

            await progress(0, 0)
            response = await PublicationRuntime(
                session=db, gateway_factory=gateway_factory, dry_run_progress=progress
            ).advance(
                run_id=job.run_id, publication_id=publication_id, request=request, actor_id=actor_id
            )
        publication = response.publication
        receipt = publication.dry_run_receipt if publication else None
        passed = receipt is not None and receipt.status == "valid"
        await finish_job(
            job_id,
            status=JOB_STATUS_SUCCEEDED if passed else JOB_STATUS_FAILED,
            result={"publication_id": publication_id, "plan_id": publication.plan.plan_id if publication and publication.plan else None},
            error=None
            if passed
            else "The dry-run has blocked actions. Review the Publication plan before a retry.",
            progress={
                "phase": "succeeded" if passed else "failed",
                "processed": publication.current_release.entity_count if publication else 0,
                "total": publication.current_release.entity_count if publication else 0,
                "unit": "entities",
                "message": "The Dry-run Receipt is valid."
                if passed
                else "The dry-run did not pass.",
            },
        )
    except DryRunCancelled:
        await finish_job(
            job_id,
            status=JOB_STATUS_CANCELLED,
            progress={"phase": "cancelled", "message": "The dry-run was cancelled."},
        )
