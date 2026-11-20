"""Shared bulk policy for confirmed API rejections before an edit."""

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from requests import Response

BULK_MAXLAG = 5
STUDIO_MAXLAG = 50
MAXLAG_REJECTION_PREFIX = "Wikidata reported maxlag;"


class ConfirmedMaxlagError(RuntimeError):
    """The MediaWiki API refused the write because of lag."""

    def __init__(self, retry_after: object) -> None:
        self.retry_after_seconds = max(5.0, retry_after_seconds(retry_after))
        super().__init__(
            f"{MAXLAG_REJECTION_PREFIX} retry after {self.retry_after_seconds:g} seconds"
        )


def preserve_maxlag_rejection(response: Response, **_kwargs: object) -> None:
    """Expose a confirmed refusal before WikibaseIntegrator discards it."""
    if response.status_code != 200:
        return
    try:
        payload = response.json()
    except (ValueError, RecursionError):
        return
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("code") == "maxlag":
        raise ConfirmedMaxlagError(response.headers.get("Retry-After"))


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
