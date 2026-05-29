"""WebSocket endpoints for real-time collaboration."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Cookie, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import COOKIE_NAME, _decode_cookie  # noqa: SLF001 — internal helper
from app.db import SessionLocal
from app.models.project import Membership, Project
from app.models.session import Session as SessionRow
from app.realtime import broker

router = APIRouter(tags=["realtime"])


async def _resolve_session_user(
    db: AsyncSession, cookie_value: str | None,
) -> uuid.UUID | None:
    if not cookie_value:
        return None
    decoded = _decode_cookie(cookie_value)
    if decoded is None:
        return None
    sid, _secret = decoded
    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == sid))
    ).scalar_one_or_none()
    if row is None:
        return None
    return row.user_id


async def _has_project_access(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID,
) -> bool:
    proj = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if proj is None:
        return False
    if proj.owner_id == user_id:
        return True
    m = (
        await db.execute(
            select(Membership).where(
                Membership.project_id == project_id, Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    return m is not None


@router.websocket("/ws/projects/{project_id}")
async def project_events_ws(
    websocket: WebSocket,
    project_id: uuid.UUID,
    cookie_value: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> None:
    # AuthN/AuthZ before accepting the upgrade.
    async with SessionLocal() as db:
        user_id = await _resolve_session_user(db, cookie_value)
        if user_id is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if not await _has_project_access(db, project_id, user_id):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await websocket.accept()
    await broker.join(project_id, websocket)
    try:
        # Drain whatever the client sends (ping frames mostly). The
        # contract here is server-pushed events only.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broker.leave(project_id, websocket)
