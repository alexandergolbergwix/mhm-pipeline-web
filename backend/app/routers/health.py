"""Liveness / readiness endpoints (no auth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Cheap liveness check — does not touch the DB."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(db: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Readiness — verifies the DB connection is alive."""
    await db.execute(text("SELECT 1"))
    return {"status": "ready"}
