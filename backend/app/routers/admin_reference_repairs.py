"""Administrator-only controls for the reviewed backend reference job."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.auth.session import AuthContext
from app.db import get_session
from app.models.run_job import JOB_KIND_WIKIDATA_GILA_AUDIT, RunJob
from app.pipeline.run_job_service import ActiveJobError, create_job, serialise_job
from app.pipeline.wikidata_reference_repair_job import (
    KIND,
    REFERENCE_JOB_MAXLAG,
    load_remaining_seed,
    load_seed,
    reference_eta_seconds,
    reference_resume_action,
)
from app.routers.runs import _lookup_run_with_access
from app.settings import get_settings

router = APIRouter(prefix="/admin/wikidata-reference-repairs", tags=["admin"])
Admin = Annotated[AuthContext, Depends(require_admin)]
Database = Annotated[AsyncSession, Depends(get_session)]
ReferenceScope = Literal["original", "remaining"]


class StartReferenceRepairs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool
    scope_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: ReferenceScope = "original"


async def latest(db, run_id, scope: ReferenceScope):
    stored_scope = RunJob.params["reference_scope"].as_string()
    matches_scope = stored_scope == scope
    if scope == "original":
        matches_scope = or_(matches_scope, stored_scope.is_(None))
    return (
        await db.execute(
            select(RunJob)
            .where(
                RunJob.run_id == run_id,
                RunJob.kind == KIND,
                matches_scope,
            )
            .order_by(RunJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("")
async def status(auth: Admin, db: Database, scope: ReferenceScope = "original"):
    seed, digest = load_remaining_seed() if scope == "remaining" else load_seed()
    run_id = uuid.UUID(seed["manifest"]["run_id"])
    run = await _lookup_run_with_access(db, run_id, auth, write=False)
    job = await latest(db, run_id, scope)
    other_scope = "original" if scope == "remaining" else "remaining"
    other_job = await latest(db, run_id, other_scope)
    checkpoint = (
        job.params.get("_reference_checkpoint", seed["checkpoint"]) if job else seed["checkpoint"]
    )
    now = datetime.now(UTC)
    results = checkpoint.get("results", [])
    qids = seed["manifest"]["qids"]
    total = len(qids)
    inventory = None
    if scope == "remaining":
        audit = (await db.execute(
            select(RunJob.result, RunJob.finished_at)
            .where(RunJob.run_id == run_id, RunJob.kind == JOB_KIND_WIKIDATA_GILA_AUDIT,
                   RunJob.status == "succeeded")
            .order_by(RunJob.created_at.desc())
            .limit(1)
        )).one_or_none()
        checks = (audit.result or {}).get("reviewed_reference_checks", {}) if audit else {}
        outcomes = {row["qid"]: row for row in results}
        inventory = {
            "audited_at": seed["reference_inventory"]["audited_at"],
            "live_checked_at": audit.finished_at if audit else None,
            "items": [
                {
                    **{key: value for key, value in row.items() if key != "claims"},
                    "observed_blocks": checks.get(row["qid"]),
                    "queue_result": outcomes.get(row["qid"], {}).get("status"),
                }
                for row in seed["reference_inventory"]["items"]
            ],
        }
    return {
        "enabled": get_settings().gila_reference_cloud_enabled,
        "scope_digest": digest,
        "run_id": str(run_id),
        "project_id": str(run.project_id),
        "observed_at": now.isoformat(),
        "total": total,
        "eta_seconds": reference_eta_seconds(job, results, total, now),
        "verified": sum(row.get("corrected") is True for row in results),
        "already_clean": sum(
            row.get("status") == "already_clean" for row in results
        ),
        "completed": len(results),
        "recent_results": [
            {key: row.get(key) for key in ("qid", "status", "after_revision")}
            for row in results[-5:]
        ],
        "worker_heartbeat_at": (
            job.executor_heartbeat_at.isoformat() if job and job.executor_heartbeat_at else None
        ),
        "job": serialise_job(job) if job else None,
        "halted_status": checkpoint.get("halted_status"),
        "halted_qid": (
            qids[len(results)] if checkpoint.get("halted_status") and len(results) < total else None
        ),
        "resume_action": (
            reference_resume_action(checkpoint)
            if job and job.status in {"failed", "cancelled"} else None
        ),
        "creator_uid": 6935067,
        "maxlag": REFERENCE_JOB_MAXLAG,
        "reference_inventory": inventory,
        "other_scope_active": bool(other_job and other_job.status in {"queued", "running"}),
    }


@router.post("/start")
async def start(body: StartReferenceRepairs, auth: Admin, db: Database):
    seed, digest = load_remaining_seed() if body.scope == "remaining" else load_seed()
    run_id = uuid.UUID(seed["manifest"]["run_id"])
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    if not get_settings().gila_reference_cloud_enabled:
        raise HTTPException(409, "The backend handoff is not enabled.")
    if body.confirmed is not True or body.scope_digest != digest:
        raise HTTPException(409, "Confirm the current reviewed reference scope.")
    if str(auth.user.id) != seed["manifest"]["actor_user_id"]:
        raise HTTPException(403, "The original administrator must resume this reviewed scope.")
    prior = await latest(db, run_id, body.scope)
    if prior and prior.status in {"queued", "running", "succeeded"}:
        return serialise_job(prior)
    checkpoint = (
        prior.params.get("_reference_checkpoint", seed["checkpoint"])
        if prior
        else seed["checkpoint"]
    )
    if prior and (prior.created_by != auth.user.id or prior.params.get("scope_digest") != digest):
        raise HTTPException(409, "The previous job has another actor or scope.")
    if reference_resume_action(checkpoint) == "review":
        raise HTTPException(409, "The previous outcome requires inspection before another start.")
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run_id,
            kind=KIND,
            params={
                "scope_digest": digest,
                "reference_scope": body.scope,
                "total": len(seed["manifest"]["qids"]),
                "_reference_checkpoint": checkpoint,
            },
            created_by=auth.user.id,
        )
    except ActiveJobError as error:
        job = await db.get(RunJob, error.job_id)
        if not job or job.params.get("scope_digest") != digest:
            raise HTTPException(409, "Another reference scope is active.") from error
    return serialise_job(job)
