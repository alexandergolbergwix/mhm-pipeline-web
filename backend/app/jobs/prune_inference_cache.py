"""Daily prune of expired inference_cache rows.

Runs once a day under Heroku Scheduler. Hard-deletes every row where
``expires_at < now()``. NER and ai_verdict rows have NULL expires_at
so they are NEVER touched.

The read path already treats expired rows as misses; this job just
reclaims disk space and keeps the table from growing unbounded.

Returns a counter dict for the caller / scheduler log.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inference_cache import InferenceCache

logger = logging.getLogger(__name__)


async def prune_inference_cache(db: AsyncSession) -> dict[str, int]:
    """Delete all expired inference_cache rows. Returns {deleted: N}."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        delete(InferenceCache).where(
            InferenceCache.expires_at.is_not(None),
            InferenceCache.expires_at < now,
        )
    )
    await db.commit()
    deleted = result.rowcount
    logger.info("prune_inference_cache: deleted %d expired rows", deleted)
    return {"deleted": deleted}
