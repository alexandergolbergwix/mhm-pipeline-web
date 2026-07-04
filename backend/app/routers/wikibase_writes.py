"""Read APIs for the Wikibase Cloud curator audit log."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.auth.project_perms import ProjectContext, require_editor
from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.wikibase_cloud_write import WikibaseCloudWrite

router = APIRouter(tags=["wikibase-writes"])


class WikibaseWriteDto(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID
    project_id: uuid.UUID | None
    run_id: uuid.UUID | None
    job_id: uuid.UUID | None
    channel: str
    operation: str
    target_kind: str
    target_key: str
    wikibase_id: str | None
    outcome_message: str
    created_at: datetime


def _serialise(row: WikibaseCloudWrite) -> WikibaseWriteDto:
    return WikibaseWriteDto(
        id=row.id,
        actor_user_id=row.actor_user_id,
        project_id=row.project_id,
        run_id=row.run_id,
        job_id=row.job_id,
        channel=row.channel,
        operation=row.operation,
        target_kind=row.target_kind,
        target_key=row.target_key,
        wikibase_id=row.wikibase_id,
        outcome_message=row.outcome_message,
        created_at=row.created_at,
    )


@router.get(
    "/projects/{project_id}/wikibase-writes",
    response_model=list[WikibaseWriteDto],
)
async def list_project_wikibase_writes(
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: ProjectContext = Depends(require_editor),
    db: AsyncSession = Depends(get_session),
) -> list[WikibaseWriteDto]:
    """Paginated audit log for Wikibase Cloud writes in a project."""
    _ = ctx
    stmt = (
        select(WikibaseCloudWrite)
        .where(WikibaseCloudWrite.project_id == project_id)
        .order_by(WikibaseCloudWrite.created_at.desc())
        .limit(limit)
    )
    if run_id is not None:
        stmt = stmt.where(WikibaseCloudWrite.run_id == run_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialise(r) for r in rows]


@router.get(
    "/admin/wikibase-writes",
    response_model=list[WikibaseWriteDto],
)
async def list_admin_wikibase_writes(
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(require_admin),  # noqa: ARG001 — gate
    db: AsyncSession = Depends(get_session),
) -> list[WikibaseWriteDto]:
    """Global Wikibase Cloud write audit (admin only)."""
    rows = (
        await db.execute(
            select(WikibaseCloudWrite)
            .order_by(WikibaseCloudWrite.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [_serialise(r) for r in rows]
