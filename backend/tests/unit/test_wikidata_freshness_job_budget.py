import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app.routers import wikidata_studio


async def _slow_check(*args, **kwargs) -> bool:
    await asyncio.sleep(0.2)
    return True


async def test_freshness_check_fails_closed_when_budget_is_exceeded(monkeypatch):
    monkeypatch.setattr(wikidata_studio, "_check_studio_cache_current", _slow_check)
    with pytest.raises(HTTPException) as excinfo:
        await wikidata_studio._studio_cache_is_current(
            None, uuid.uuid4(), source="canonical", approved_only=True,
            fingerprint="x", summary={}, timeout_seconds=0.05,
        )
    assert excinfo.value.status_code == 409


async def test_freshness_check_returns_result_within_budget(monkeypatch):
    monkeypatch.setattr(wikidata_studio, "_check_studio_cache_current", _slow_check)
    result = await wikidata_studio._studio_cache_is_current(
        None, uuid.uuid4(), source="canonical", approved_only=True,
        fingerprint="x", summary={}, timeout_seconds=1.0,
    )
    assert result is True


def test_job_budget_covers_measured_full_check():
    # The full check took 73.6 s on a Heroku dyno for run 47e03437.
    assert wikidata_studio.STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS >= 300.0
    assert wikidata_studio._STUDIO_FRESHNESS_TIMEOUT_SECONDS == 25.0
