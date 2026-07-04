"""Record curator-attributed outcomes for Wikibase Cloud writes."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

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
