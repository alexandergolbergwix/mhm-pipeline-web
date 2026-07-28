"""Background run job API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.run_job import (
    ACTIVE_JOB_STATUSES,
    SUPPORTED_JOB_KINDS,
    RunJob,
)
from app.pipeline.run_job_params import prepare_job_params
from app.pipeline.run_job_service import (
    ActiveJobError,
    create_job,
    request_cancel,
    serialise_job,
)
from app.routers.runs import _lookup_run_with_access

router = APIRouter(tags=["run-jobs"])


class StartRunJobRequest(BaseModel):
    kind: str = Field(..., min_length=1, max_length=48)
    params: dict[str, Any] = Field(default_factory=dict)


@router.get("/jobs/mine")
async def list_my_jobs(
    active: bool = Query(False),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    """List jobs started by the current user."""
    q = select(RunJob).where(RunJob.created_by == auth.user.id)
    if active:
        q = q.where(RunJob.status.in_(tuple(ACTIVE_JOB_STATUSES)))
    q = q.order_by(RunJob.created_at.desc())
    rows = (await db.execute(q)).scalars().all()
    return {"jobs": [serialise_job(j) for j in rows]}


@router.get("/runs/{run_id}/jobs")
async def list_run_jobs(
    run_id: uuid.UUID,
    active: bool = Query(False),
    kind: str | None = Query(None, max_length=48),
    limit: int | None = Query(None, ge=1, le=100),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    q = select(RunJob).where(RunJob.run_id == run_id)
    if active:
        q = q.where(RunJob.status.in_(tuple(ACTIVE_JOB_STATUSES)))
    if kind:
        q = q.where(RunJob.kind == kind)
    q = q.order_by(RunJob.created_at.desc())
    if limit is not None:
        q = q.limit(limit)
    rows = (await db.execute(q)).scalars().all()
    # List payloads omit session_snapshot by default (Rule W-130).
    return {"jobs": [serialise_job(j) for j in rows]}


@router.post("/runs/{run_id}/jobs", status_code=status.HTTP_201_CREATED)
async def start_run_job(
    run_id: uuid.UUID,
    body: StartRunJobRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    params = await prepare_job_params(
        db, auth, run_id=run_id, kind=body.kind, params=body.params,
    )
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run_id,
            kind=body.kind,
            params=params,
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "job already running", "job_id": str(exc.job_id)},
        ) from exc
    return serialise_job(job)


@router.get("/runs/{run_id}/jobs/{job_id}")
async def get_run_job(
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    include_session_snapshot: bool = Query(False),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    job = (
        await db.execute(
            select(RunJob).where(RunJob.id == job_id, RunJob.run_id == run_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return serialise_job(job, include_session_snapshot=include_session_snapshot)


@router.post("/runs/{run_id}/jobs/{job_id}/cancel")
async def cancel_run_job(
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _lookup_run_with_access(db, run_id, auth, write=True)
    job = (
        await db.execute(
            select(RunJob).where(RunJob.id == job_id, RunJob.run_id == run_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    updated = await request_cancel(db, job_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return serialise_job(updated)
