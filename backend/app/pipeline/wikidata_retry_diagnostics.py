"""Retain bounded API retry evidence without raw responses or credentials."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

RETRY_CODES = frozenset({"maxlag", "ratelimited", "readonly"})


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _name(value: object, *, limit: int) -> str | None:
    if isinstance(value, str) and len(value) <= limit and re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        return value
    return None


@dataclass(frozen=True, slots=True)
class ApiRetryDiagnostics:
    code: str
    observed_at: datetime
    host: str | None = None
    lag_type: str | None = None
    lag_seconds: float | None = None
    queryservice_lag_seconds: float | None = None
    retry_after_seconds: float | None = None

    @classmethod
    def from_error(
        cls, error: Mapping[str, object], *, retry_after_seconds: float | None = None,
        observed_at: datetime | None = None,
    ) -> ApiRetryDiagnostics | None:
        code = error.get("code")
        if not isinstance(code, str) or code not in RETRY_CODES:
            return None
        return cls(
            code=code, observed_at=observed_at or datetime.now(UTC),
            host=_name(error.get("host"), limit=253),
            lag_type=_name(error.get("type"), limit=64),
            lag_seconds=_number(error.get("lag")),
            queryservice_lag_seconds=_number(error.get("queryserviceLag")),
            retry_after_seconds=_number(retry_after_seconds),
        )

    def to_dict(self) -> dict[str, str | float | None]:
        return {
            "code": self.code, "observed_at": self.observed_at.isoformat(),
            "host": self.host, "type": self.lag_type, "lag": self.lag_seconds,
            "queryserviceLag": self.queryservice_lag_seconds,
            "retry_after_seconds": self.retry_after_seconds,
        }

    def to_detail(self, message: str) -> str:
        return json.dumps({"api_retry_version": 1, "message": message, "api_retry": self.to_dict()})

    @classmethod
    def from_detail(cls, detail: str | None) -> ApiRetryDiagnostics | None:
        if not detail or len(detail) > 8192:
            return None
        try:
            envelope = json.loads(detail)
            if not isinstance(envelope, dict) or envelope.get("api_retry_version") != 1:
                return None
            value = envelope.get("api_retry")
            if not isinstance(value, dict):
                return None
            timestamp = value.get("observed_at")
            if not isinstance(timestamp, str):
                return None
            observed = datetime.fromisoformat(timestamp)
            if observed.tzinfo is None:
                return None
            return cls.from_error(
                value, retry_after_seconds=value.get("retry_after_seconds"),
                observed_at=observed.astimezone(UTC),
            )
        except (ValueError, RecursionError):
            return None
