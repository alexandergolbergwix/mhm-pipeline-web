"""Tests for :mod:`app.models.email_throttle` — the ``allow()`` gate.

Two limits stack on the throttle:

* a per-recipient 60-second cooldown, and
* a per-recipient per-UTC-day cap of 5.

The model reads wall time via ``datetime.now(timezone.utc)``. Rather
than wire ``freezegun`` (not in the project's dependency set), we
swap the module's ``datetime`` symbol for a small fake whose ``now``
returns whatever the test set on it last. The fake delegates every
other attribute access to the real ``datetime`` so calls like
``datetime.now(timezone.utc).date()`` continue to work unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.models import email_throttle as throttle_mod
from app.models.email_throttle import PER_DAY_CAP, PER_MINUTE_COOLDOWN_SECONDS, allow


# ── Frozen-clock helper ────────────────────────────────────────────────


class _FrozenDatetime:
    """Drop-in for the ``datetime`` symbol inside ``email_throttle``.

    Only ``.now(tz)`` is overridden; everything else (``date``,
    ``timedelta`` constructors used elsewhere in the module — there are
    none, but for safety) falls through to the real ``datetime`` class.
    """

    current: datetime = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz: timezone | None = None) -> datetime:
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)

    def __getattr__(self, name: str) -> Any:  # pragma: no cover — defensive
        return getattr(datetime, name)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> type[_FrozenDatetime]:
    """Replace ``email_throttle.datetime`` with the frozen fake.

    Tests advance time by reassigning ``_FrozenDatetime.current``.
    """
    _FrozenDatetime.current = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(throttle_mod, "datetime", _FrozenDatetime)
    return _FrozenDatetime


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allow_first_call_returns_true(db_session, frozen_clock) -> None:
    """First-ever send to an address passes and seeds the row."""
    assert await allow(db_session, "alice@example.com") is True
    await db_session.commit()


@pytest.mark.asyncio
async def test_allow_second_call_within_60s_returns_false(
    db_session, frozen_clock,
) -> None:
    """A second send inside the 60-second cooldown is refused."""
    assert await allow(db_session, "alice@example.com") is True
    await db_session.commit()

    # Advance 30s — still inside the cooldown.
    frozen_clock.current = frozen_clock.current + timedelta(seconds=30)
    assert await allow(db_session, "alice@example.com") is False
    await db_session.commit()


@pytest.mark.asyncio
async def test_allow_after_60s_window_returns_true(
    db_session, frozen_clock,
) -> None:
    """Once the per-minute cooldown elapses, the next send passes."""
    assert await allow(db_session, "alice@example.com") is True
    await db_session.commit()

    frozen_clock.current = frozen_clock.current + timedelta(
        seconds=PER_MINUTE_COOLDOWN_SECONDS + 1,
    )
    assert await allow(db_session, "alice@example.com") is True
    await db_session.commit()


@pytest.mark.asyncio
async def test_allow_count_per_day_cap_5(db_session, frozen_clock) -> None:
    """At most ``PER_DAY_CAP`` (5) sends per UTC day are allowed.

    Five successful sends, then the sixth is refused — all inside the
    same UTC day, each spaced past the per-minute cooldown so only the
    daily cap can be the gate that rejects the sixth call.
    """
    for _ in range(PER_DAY_CAP):
        assert await allow(db_session, "alice@example.com") is True
        await db_session.commit()
        frozen_clock.current = frozen_clock.current + timedelta(
            seconds=PER_MINUTE_COOLDOWN_SECONDS + 1,
        )

    # Sixth attempt — cooldown is satisfied, the daily cap is not.
    assert await allow(db_session, "alice@example.com") is False
    await db_session.commit()


@pytest.mark.asyncio
async def test_allow_new_day_resets_counter(db_session, frozen_clock) -> None:
    """Rolling into the next UTC day starts a fresh per-day counter.

    The model keys the row on ``(recipient_index, bucket_day)`` so a
    new day finds no existing row and the insert branch fires.
    """
    for _ in range(PER_DAY_CAP):
        assert await allow(db_session, "alice@example.com") is True
        await db_session.commit()
        frozen_clock.current = frozen_clock.current + timedelta(
            seconds=PER_MINUTE_COOLDOWN_SECONDS + 1,
        )

    # Same day, capped.
    assert await allow(db_session, "alice@example.com") is False
    await db_session.commit()

    # Jump to the next UTC day — counter resets, new send passes.
    frozen_clock.current = datetime(2026, 6, 2, 0, 0, 1, tzinfo=timezone.utc)
    assert await allow(db_session, "alice@example.com") is True
    await db_session.commit()


@pytest.mark.asyncio
async def test_allow_different_recipients_independent(
    db_session, frozen_clock,
) -> None:
    """Two recipients have independent throttle rows.

    One address being inside its cooldown must NOT block the other.
    """
    assert await allow(db_session, "alice@example.com") is True
    await db_session.commit()

    # Same instant — alice is still inside her cooldown, bob has never
    # been mailed before.
    assert await allow(db_session, "alice@example.com") is False
    assert await allow(db_session, "bob@example.com") is True
    await db_session.commit()
