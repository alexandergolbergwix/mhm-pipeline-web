"""GDPR Article 5(1)(e) + Article 17 — storage-limitation purge.

Runs once a day under Heroku Scheduler. Walks the ``access_requests``
table and removes rows that have outlived their lawful retention window:

* ``pending_email_confirm`` rows abandoned by the requester (no click on
  the double-opt-in link within ``ABANDONED_TTL_DAYS`` days) are deleted.
  We promised to "verify your email"; if the requester never confirmed,
  we have no lawful basis to keep their PII.
* ``denied`` rows are deleted ``DENIED_TTL_DAYS`` days after the admin
  decision. The audit trail of the denial has by then served its triage
  purpose; keeping the requester's PII indefinitely is disproportionate.
* ``pending_admin`` rows that have been sitting un-reviewed for longer
  than ``STALE_ADMIN_WARN_DAYS`` days are NOT auto-decided — the
  admin's discretion is the lawful basis here, so we only log a warning
  with the IDs to nudge a human.
* ``approved`` rows are KEPT. They document who let whom in, which is
  the actual audit trail; the corresponding ``Invitation`` and
  eventually-created user account hold the same PII anyway.

Returns a counter dict for the caller / scheduler log.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_request import (
    STATUS_DENIED,
    STATUS_PENDING_ADMIN,
    STATUS_PENDING_EMAIL_CONFIRM,
    AccessRequest,
)

logger = logging.getLogger(__name__)

ABANDONED_TTL_DAYS = 7
DENIED_TTL_DAYS = 30
STALE_ADMIN_WARN_DAYS = 14


async def purge_access_requests(db: AsyncSession) -> dict[str, int]:
    """Apply the four retention rules. Commits once at the end."""

    now = datetime.now(timezone.utc)
    abandoned_cutoff = now - timedelta(days=ABANDONED_TTL_DAYS)
    denied_cutoff = now - timedelta(days=DENIED_TTL_DAYS)
    stale_cutoff = now - timedelta(days=STALE_ADMIN_WARN_DAYS)

    # Rule 1 — abandoned double-opt-in submissions.
    abandoned_stmt = (
        delete(AccessRequest)
        .where(AccessRequest.status == STATUS_PENDING_EMAIL_CONFIRM)
        .where(AccessRequest.created_at < abandoned_cutoff)
        .returning(AccessRequest.id)
    )
    abandoned_result = await db.execute(abandoned_stmt)
    abandoned_ids = abandoned_result.scalars().all()
    abandoned_purged = len(abandoned_ids)

    # Rule 2 — denied requests past the audit window.
    denied_stmt = (
        delete(AccessRequest)
        .where(AccessRequest.status == STATUS_DENIED)
        .where(AccessRequest.reviewed_at.is_not(None))
        .where(AccessRequest.reviewed_at < denied_cutoff)
        .returning(AccessRequest.id)
    )
    denied_result = await db.execute(denied_stmt)
    denied_ids = denied_result.scalars().all()
    denied_purged = len(denied_ids)

    # Rule 3 — stale ``pending_admin`` rows: warn only, never auto-decide.
    stale_stmt = (
        select(AccessRequest.id)
        .where(AccessRequest.status == STATUS_PENDING_ADMIN)
        .where(AccessRequest.confirmed_at.is_not(None))
        .where(AccessRequest.confirmed_at < stale_cutoff)
    )
    stale_result = await db.execute(stale_stmt)
    stale_ids = [str(row_id) for row_id in stale_result.scalars().all()]
    stale_pending = len(stale_ids)
    if stale_pending:
        logger.warning(
            "purge_access_requests: %d pending_admin row(s) older than %d days "
            "awaiting human review: %s",
            stale_pending,
            STALE_ADMIN_WARN_DAYS,
            ", ".join(stale_ids),
        )

    await db.commit()

    summary: dict[str, int] = {
        "abandoned_purged": abandoned_purged,
        "denied_purged": denied_purged,
        "stale_pending": stale_pending,
    }
    logger.info("purge_access_requests: %s", summary)
    return summary
