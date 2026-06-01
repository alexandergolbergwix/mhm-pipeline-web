"""Compatibility shim: re-expose :mod:`app.models.email_throttle`.

The :mod:`app.services.email` module imports ``EmailThrottle`` from
``app.services.email_throttle`` and calls ``EmailThrottle.allow(db, to)``.
The actual model lives in :mod:`app.models.email_throttle`, where
``allow`` is a free function alongside the ORM class. This module
re-exports the class with a classmethod-shaped ``allow`` so both
consumption styles work.

Keeping the shim in the service layer (rather than mutating the model
module) preserves the model's purity — it remains a plain SQLAlchemy
table with a free helper — while honouring the API contract documented
in the access-request recovery brief.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_throttle import (
    PER_DAY_CAP,
    PER_MINUTE_COOLDOWN_SECONDS,
    EmailThrottle,
)
from app.models.email_throttle import allow as _allow

__all__ = ["EmailThrottle", "PER_DAY_CAP", "PER_MINUTE_COOLDOWN_SECONDS"]


async def _allow_classmethod(cls: type[EmailThrottle], db: AsyncSession, email_addr: str) -> bool:
    return await _allow(db, email_addr)


# Attach ``allow`` to the existing ORM class without subclassing — the
# table is already mapped, so a subclass would need ``__table_args__``
# tricks. A bound classmethod is the cleanest path.
EmailThrottle.allow = classmethod(_allow_classmethod)  # type: ignore[attr-defined]
