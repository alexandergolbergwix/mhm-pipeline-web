"""Saved SPARQL queries CRUD (Feature 2).

Endpoints:
  GET    /api/projects/{project_id}/research/saved-queries
  POST   /api/projects/{project_id}/research/saved-queries
  GET    /api/projects/{project_id}/research/saved-queries/{query_id}
  PUT    /api/projects/{project_id}/research/saved-queries/{query_id}
  DELETE /api/projects/{project_id}/research/saved-queries/{query_id}

Viewer role: read.  Editor/owner role: write (create/update/delete).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.project import Membership, PROJECT_ROLE_VIEWER
from app.models.saved_query import SavedQuery
from app.routers.linked_data_explorer import _require_viewer
from app.schemas.saved_query import SavedQueryCreate, SavedQueryOut, SavedQueryUpdate

router = APIRouter(tags=["research"])


async def _require_editor(
    project_id: uuid.UUID,
    auth: AuthContext,
    db: AsyncSession,
) -> None:
    row = await db.execute(
        select(Membership).where(
            Membership.project_id == project_id,
            Membership.user_id == auth.user.id,
        )
    )
    membership = row.scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this project.")
    if membership.role == PROJECT_ROLE_VIEWER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Editor or owner role required.")


async def _get_or_404(
    project_id: uuid.UUID,
    query_id: uuid.UUID,
    db: AsyncSession,
) -> SavedQuery:
    row = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == query_id,
            SavedQuery.project_id == project_id,
        )
    )
    obj = row.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved query not found.")
    return obj


@router.get("/projects/{project_id}/research/saved-queries", response_model=list[SavedQueryOut])
async def list_saved_queries(
    project_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[SavedQuery]:
    await _require_viewer(project_id, auth, db)
    rows = await db.execute(
        select(SavedQuery)
        .where(SavedQuery.project_id == project_id)
        .order_by(SavedQuery.created_at)
    )
    return list(rows.scalars().all())


@router.post(
    "/projects/{project_id}/research/saved-queries",
    response_model=SavedQueryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_saved_query(
    project_id: uuid.UUID,
    body: SavedQueryCreate,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SavedQuery:
    await _require_editor(project_id, auth, db)
    obj = SavedQuery(
        project_id=project_id,
        created_by=auth.user.id,
        name=body.name,
        description=body.description,
        query=body.query,
        params=body.params,
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.get(
    "/projects/{project_id}/research/saved-queries/{query_id}",
    response_model=SavedQueryOut,
)
async def get_saved_query(
    project_id: uuid.UUID,
    query_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SavedQuery:
    await _require_viewer(project_id, auth, db)
    return await _get_or_404(project_id, query_id, db)


@router.put(
    "/projects/{project_id}/research/saved-queries/{query_id}",
    response_model=SavedQueryOut,
)
async def update_saved_query(
    project_id: uuid.UUID,
    query_id: uuid.UUID,
    body: SavedQueryUpdate,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SavedQuery:
    await _require_editor(project_id, auth, db)
    obj = await _get_or_404(project_id, query_id, db)
    if body.name is not None:
        obj.name = body.name
    if body.description is not None:
        obj.description = body.description
    if body.query is not None:
        obj.query = body.query
    if body.params is not None:
        obj.params = body.params
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete(
    "/projects/{project_id}/research/saved-queries/{query_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_saved_query(
    project_id: uuid.UUID,
    query_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> None:
    await _require_editor(project_id, auth, db)
    obj = await _get_or_404(project_id, query_id, db)
    await db.delete(obj)
    await db.commit()
