"""Administrator-only controls for the reviewed backend reference job."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.auth.session import AuthContext
from app.db import get_session
from app.models.run_job import RunJob
from app.pipeline.run_job_service import ActiveJobError, create_job, serialise_job
from app.pipeline.wikidata_reference_repair_job import KIND, load_seed
from app.routers.runs import _lookup_run_with_access
from app.settings import get_settings

router = APIRouter(prefix="/admin/wikidata-reference-repairs", tags=["admin"])
Admin = Annotated[AuthContext, Depends(require_admin)]
Database = Annotated[AsyncSession, Depends(get_session)]


class StartReferenceRepairs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool
    scope_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


async def latest(db, run_id):
    return (
        await db.execute(
            select(RunJob)
            .where(
                RunJob.run_id == run_id,
                RunJob.kind == KIND,
            )
            .order_by(RunJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("")
async def status(auth: Admin, db: Database):
    seed, digest = load_seed()
    run_id = uuid.UUID(seed["manifest"]["run_id"])
    run = await _lookup_run_with_access(db, run_id, auth, write=False)
    job = await latest(db, run_id)
    checkpoint = (
        job.params.get("_reference_checkpoint", seed["checkpoint"]) if job else seed["checkpoint"]
    )
    return {
        "enabled": get_settings().gila_reference_cloud_enabled,
        "scope_digest": digest,
        "run_id": str(run_id),
        "project_id": str(run.project_id),
        "observed_at": datetime.now(UTC).isoformat(),
        "total": len(seed["manifest"]["qids"]),
        "verified": sum(row.get("corrected") is True for row in checkpoint.get("results", [])),
        "already_clean": sum(
            row.get("status") == "already_clean" for row in checkpoint.get("results", [])
        ),
        "completed": len(checkpoint.get("results", [])),
        "recent_results": [
            {key: row.get(key) for key in ("qid", "status", "after_revision")}
            for row in checkpoint.get("results", [])[-5:]
        ],
        "worker_heartbeat_at": (
            job.executor_heartbeat_at.isoformat() if job and job.executor_heartbeat_at else None
        ),
        "job": serialise_job(job) if job else None,
        "halted_status": checkpoint.get("halted_status"),
        "creator_uid": 6935067,
        "maxlag": 5,
    }


@router.post("/start")
async def start(body: StartReferenceRepairs, auth: Admin, db: Database):
    seed, digest = load_seed()
    run_id = uuid.UUID(seed["manifest"]["run_id"])
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    if not get_settings().gila_reference_cloud_enabled:
        raise HTTPException(409, "The backend handoff is not enabled.")
    if body.confirmed is not True or body.scope_digest != digest:
        raise HTTPException(409, "Confirm the current reviewed reference scope.")
    if str(auth.user.id) != seed["manifest"]["actor_user_id"]:
        raise HTTPException(403, "The original administrator must resume this reviewed scope.")
    prior = await latest(db, run_id)
    if prior and prior.status in {"queued", "running", "succeeded"}:
        return serialise_job(prior)
    checkpoint = (
        prior.params.get("_reference_checkpoint", seed["checkpoint"])
        if prior
        else seed["checkpoint"]
    )
    if prior and (prior.created_by != auth.user.id or prior.params.get("scope_digest") != digest):
        raise HTTPException(409, "The previous job has another actor or scope.")
    if checkpoint.get("halted_status") not in {None, "pre_send_retryable"}:
        raise HTTPException(409, "The previous outcome requires inspection before another start.")
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run_id,
            kind=KIND,
            params={
                "scope_digest": digest,
                "total": len(seed["manifest"]["qids"]),
                "_reference_checkpoint": checkpoint,
            },
            created_by=auth.user.id,
        )
    except ActiveJobError as error:
        job = await db.get(RunJob, error.job_id)
    return serialise_job(job)
