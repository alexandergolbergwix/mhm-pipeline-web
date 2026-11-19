import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers import wikidata_studio as router


@pytest.mark.asyncio
async def test_status_reads_total_from_cache_summary(monkeypatch):
    monkeypatch.setattr(router, "_lookup_run_with_access", AsyncMock(return_value=None))
    counts = {"total_items": 2414, "manuscripts": 2000, "persons": 14, "works": 400, "statements": 4}
    db = SimpleNamespace(scalar=AsyncMock(return_value={**counts, "canonical_reuse_key": "private"}))
    result = await router.studio_status(
        uuid.uuid4(), source="canonical", approved_only=True, auth=None, db=db,
    )
    assert result == {"ready": True, "total_items": 2414, "summary": counts}
    query = str(db.scalar.await_args.args[0])
    assert "SELECT wikidata_studio_cache.summary" in query
    assert "result_items" not in query


@pytest.mark.asyncio
async def test_status_without_cache_is_not_ready(monkeypatch):
    monkeypatch.setattr(router, "_lookup_run_with_access", AsyncMock(return_value=None))
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    result = await router.studio_status(
        uuid.uuid4(), source="canonical", approved_only=True, auth=None, db=db,
    )
    assert result == {"ready": False, "total_items": None, "summary": None}
