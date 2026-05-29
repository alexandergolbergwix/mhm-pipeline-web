"""Run lifecycle + approvals.

Endpoints (RBAC notes):

* ``POST   /projects/{id}/runs``            — editor+
* ``GET    /projects/{id}/runs``            — viewer+
* ``GET    /runs/{id}``                     — viewer+ (project-scoped via lookup)
* ``GET    /runs/{id}/matches``             — viewer+
* ``GET    /runs/{id}/records/{cn}``        — viewer+ (popup with full MARC)
* ``PATCH  /runs/{id}/matches/{mid}``       — editor+ (toggle approval)
* ``POST   /runs/{id}/matches/bulk-approve``— editor+
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_perms import (
    ProjectContext,
    require_editor,
    require_viewer,
)
from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.project import (
    PROJECT_ROLE_EDITOR,
    PROJECT_ROLE_OWNER,
    PROJECT_ROLE_VIEWER,
    Membership,
    Project,
)
from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline.run import execute_run, serialise_match
from app.schemas.runs import (
    ApprovalBatch,
    ApprovalUpdate,
    AuthorityMatchResponse,
    RunDetail,
    RunListItem,
    RunMarcRecord,
)


router = APIRouter(tags=["runs"])


# ── Per-project: list + create ─────────────────────────────────────────


@router.get("/projects/{project_id}/runs", response_model=list[RunListItem])
async def list_runs(
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> list[RunListItem]:
    rows = (
        await db.execute(
            select(Run).where(Run.project_id == ctx.project.id).order_by(desc(Run.created_at))
        )
    ).scalars().all()
    return [_to_list_item(r) for r in rows]


@router.post(
    "/projects/{project_id}/runs", response_model=RunListItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    file: UploadFile = File(..., description="MARC JSON or JSONL upload"),
    ctx: ProjectContext = Depends(require_editor),
    db: AsyncSession = Depends(get_session),
) -> RunListItem:
    raw = await file.read()
    if len(raw) > 25 * 1024 * 1024:  # 25 MB hard ceiling
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Upload exceeds 25 MB",
        )
    run = Run(
        project_id=ctx.project.id,
        created_by=ctx.user_id,
        name=(file.filename or "Untitled run").rsplit(".", 1)[0][:200],
    )
    db.add(run)
    await db.flush()
    await execute_run(db, run=run, upload=raw)
    return _to_list_item(run)


# ── Per-run: detail + matches + per-record MARC popup ───────────────────


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RunDetail:
    run = await _lookup_run_with_access(db, run_id, auth)
    matches = (
        await db.execute(
            select(AuthorityMatch)
            .where(AuthorityMatch.run_id == run.id)
            .order_by(AuthorityMatch.control_number.asc(), AuthorityMatch.entity_text.asc())
        )
    ).scalars().all()
    payload = _to_list_item(run).model_dump()
    payload["matches"] = [serialise_match(m) for m in matches]
    return RunDetail(**payload)


@router.get("/runs/{run_id}/matches", response_model=list[AuthorityMatchResponse])
async def list_matches(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[AuthorityMatchResponse]:
    await _lookup_run_with_access(db, run_id, auth)
    matches = (
        await db.execute(
            select(AuthorityMatch)
            .where(AuthorityMatch.run_id == run_id)
            .order_by(AuthorityMatch.control_number.asc(), AuthorityMatch.entity_text.asc())
        )
    ).scalars().all()
    return [AuthorityMatchResponse(**serialise_match(m)) for m in matches]


@router.get(
    "/runs/{run_id}/records/{control_number}",
    response_model=RunMarcRecord,
)
async def get_record(
    run_id: uuid.UUID, control_number: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RunMarcRecord:
    await _lookup_run_with_access(db, run_id, auth)
    rec = (
        await db.execute(
            select(RunRecord).where(
                RunRecord.run_id == run_id, RunRecord.control_number == control_number,
            )
        )
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    return RunMarcRecord(control_number=rec.control_number, marc=rec.marc)


# ── Approvals (editor+) ────────────────────────────────────────────────


@router.patch(
    "/runs/{run_id}/matches/{match_id}", response_model=AuthorityMatchResponse,
)
async def update_approval(
    run_id: uuid.UUID, match_id: uuid.UUID,
    payload: ApprovalUpdate,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> AuthorityMatchResponse:
    await _lookup_run_with_access(db, run_id, auth, write=True)
    m = (
        await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.id == match_id, AuthorityMatch.run_id == run_id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    _apply_approval(m, payload.approved, auth.user.id)
    await db.commit()
    return AuthorityMatchResponse(**serialise_match(m))


@router.post(
    "/runs/{run_id}/matches/bulk-approve", response_model=list[AuthorityMatchResponse],
)
async def bulk_approve(
    run_id: uuid.UUID, payload: ApprovalBatch,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[AuthorityMatchResponse]:
    await _lookup_run_with_access(db, run_id, auth, write=True)
    if not payload.match_ids:
        return []
    rows = (
        await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.run_id == run_id,
                AuthorityMatch.id.in_(payload.match_ids),
            )
        )
    ).scalars().all()
    for m in rows:
        _apply_approval(m, payload.approved, auth.user.id)
    await db.commit()
    return [AuthorityMatchResponse(**serialise_match(m)) for m in rows]


# ── helpers ────────────────────────────────────────────────────────────


def _to_list_item(r: Run) -> RunListItem:
    return RunListItem(
        id=r.id, project_id=r.project_id, name=r.name, status=r.status,  # type: ignore[arg-type]
        record_count=r.record_count, match_count=r.match_count, error=r.error,
        created_at=r.created_at, completed_at=r.completed_at,
    )


def _apply_approval(m: AuthorityMatch, approved: bool, user_id: uuid.UUID) -> None:
    m.approved = approved
    m.approved_by = user_id if approved else None
    m.approved_at = datetime.now(timezone.utc) if approved else None


async def _lookup_run_with_access(
    db: AsyncSession, run_id: uuid.UUID, auth: AuthContext, *, write: bool = False,
) -> Run:
    """Resolve the run + check the user has project access. Mirrors the
    project_perms RBAC but works off a run-id input (no project_id in
    the URL)."""
    run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    proj = (
        await db.execute(select(Project).where(Project.id == run.project_id))
    ).scalar_one_or_none()
    if proj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if proj.owner_id == auth.user.id:
        return run
    m = (
        await db.execute(
            select(Membership).where(
                Membership.project_id == proj.id, Membership.user_id == auth.user.id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this project",
        )
    if write and m.role not in (PROJECT_ROLE_OWNER, PROJECT_ROLE_EDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Editor role required",
        )
    if not write and m.role not in (
        PROJECT_ROLE_OWNER, PROJECT_ROLE_EDITOR, PROJECT_ROLE_VIEWER,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Viewer role required",
        )
    return run
