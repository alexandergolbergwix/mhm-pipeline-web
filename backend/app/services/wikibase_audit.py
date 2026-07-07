"""Record curator-attributed outcomes for Wikibase Cloud writes."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wikibase_cloud_write import WikibaseCloudWrite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WikibaseAuditContext:
    actor_user_id: uuid.UUID
    channel: str
    project_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None


async def record_wikibase_write(
    db: AsyncSession,
    ctx: WikibaseAuditContext,
    *,
    operation: str,
    target_kind: str,
    target_key: str,
    wikibase_id: str | None = None,
    outcome_message: str = "ok",
) -> None:
    """Append one audit row. Failures are logged and swallowed."""
    try:
        db.add(
            WikibaseCloudWrite(
                actor_user_id=ctx.actor_user_id,
                project_id=ctx.project_id,
                run_id=ctx.run_id,
                job_id=ctx.job_id,
                channel=ctx.channel,
                operation=operation,
                target_kind=target_kind,
                target_key=target_key,
                wikibase_id=wikibase_id,
                outcome_message=outcome_message,
            )
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — audit must never break writes
        logger.warning("failed to record wikibase_cloud_write", exc_info=True)
        await db.rollback()


async def fetch_latest_wikibase_writes(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    channel: str,
    target_kind: str,
) -> dict[str, WikibaseCloudWrite]:
    """Latest audit row per ``target_key`` for this run/channel/target_kind.

    Uses a portable "group by max(created_at)" join rather than Postgres-only
    ``DISTINCT ON`` so it also works against the shared SQLite connection the
    test suite uses.
    """
    latest_sub = (
        select(
            WikibaseCloudWrite.target_key.label("target_key"),
            func.max(WikibaseCloudWrite.created_at).label("max_created_at"),
        )
        .where(
            WikibaseCloudWrite.run_id == run_id,
            WikibaseCloudWrite.channel == channel,
            WikibaseCloudWrite.target_kind == target_kind,
        )
        .group_by(WikibaseCloudWrite.target_key)
        .subquery()
    )
    rows = (
        await db.execute(
            select(WikibaseCloudWrite).join(
                latest_sub,
                (WikibaseCloudWrite.target_key == latest_sub.c.target_key)
                & (WikibaseCloudWrite.created_at == latest_sub.c.max_created_at),
            ).where(
                WikibaseCloudWrite.run_id == run_id,
                WikibaseCloudWrite.channel == channel,
                WikibaseCloudWrite.target_kind == target_kind,
            )
        )
    ).scalars().all()
    out: dict[str, WikibaseCloudWrite] = {}
    for row in rows:
        # Two writes for the same target_key landing in the same instant
        # (coarser SQLite timestamp resolution) is unlikely but not
        # impossible — break ties deterministically on id.
        existing = out.get(row.target_key)
        if existing is None or row.id > existing.id:
            out[row.target_key] = row
    return out
