"""Shared bulk policy for confirmed API rejections before an edit."""

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

BULK_MAXLAG = 5


def retry_after_seconds(
    value: object, *, default: float = 5.0, now: datetime | None = None
) -> float:
    try:
        number = float(value)
        return max(0.0, number) if math.isfinite(number) else default
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return max(0.0, (date - (now or datetime.now(UTC))).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return default
