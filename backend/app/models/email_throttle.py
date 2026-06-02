"""Per-recipient email throttle.

A single small table that protects an outbound recipient from being
flooded by the access-request flow (or any other system mail that
opts in). Two limits stack:

* ``PER_MINUTE_COOLDOWN_SECONDS`` (60s) — no two messages to the same
  address within a minute.
* ``PER_DAY_CAP`` (5) — at most five messages per UTC day.

The row is keyed by a blind index of the address, not the plaintext —
same treatment as :class:`app.models.invitation.Invitation` so a DB
dump cannot enumerate who we have ever mailed.

The :func:`allow` helper runs inside the caller's transaction and uses
``SELECT ... FOR UPDATE`` so two concurrent send attempts cannot both
pass the gate. The row is upserted on first contact of the day.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Date, DateTime, Integer, LargeBinary, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.crypto.index import blind_index
from app.models.base import Base, _new_uuid

PER_MINUTE_COOLDOWN_SECONDS: int = 60
PER_DAY_CAP: int = 5


class EmailThrottle(Base):
    __tablename__ = "email_throttle"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )

    # Blind index of the lowercased recipient address — same construction
    # as ``users.email_index`` / ``invitations.email_index``.
    recipient_index: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)

    # UTC date the per-day counter belongs to. We deliberately key on a
    # calendar bucket rather than a sliding window so the row stays
    # cheap to find (single equality predicate, indexed).
    bucket_day: Mapped[date] = mapped_column(Date, nullable=False)

    count_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "recipient_index", "bucket_day", name="uq_email_throttle_recipient_day",
        ),
    )


async def allow(db: AsyncSession, email_addr: str) -> bool:
    """Atomically decide whether we may send another mail to ``email_addr``.

    Returns ``True`` when the send is allowed and the throttle row has
    already been updated (caller still commits the surrounding
    transaction). Returns ``False`` when the per-minute cooldown or the
    per-day cap would be breached.
    """
    recipient_index = blind_index(email_addr)
    today = datetime.now(timezone.utc).date()

    stmt = (
        select(EmailThrottle)
        .where(EmailThrottle.recipient_index == recipient_index)
        .where(EmailThrottle.bucket_day == today)
        .with_for_update()
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if row is not None:
        last_sent = row.last_sent_at
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        if (now - last_sent) < timedelta(seconds=PER_MINUTE_COOLDOWN_SECONDS):
            return False
        if row.count_today >= PER_DAY_CAP:
            return False
        row.count_today = row.count_today + 1
        row.last_sent_at = now
        await db.flush()
        return True

    db.add(
        EmailThrottle(
            recipient_index=recipient_index,
            bucket_day=today,
            count_today=1,
            last_sent_at=now,
        ),
    )
    await db.flush()
    return True
