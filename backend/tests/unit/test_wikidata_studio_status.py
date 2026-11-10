import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers import wikidata_studio as router


@pytest.mark.asyncio
async def test_status_reads_total_from_cache_summary(monkeypatch):
    monkeypatch.setattr(router, "_lookup_run_with_access", AsyncMock(return_value=None))
    row = SimpleNamespace(summary={"total_items": 2414})
    monkeypatch.setattr(router, "_get_studio_cache_row", AsyncMock(return_value=row))
    result = await router.studio_status(
        uuid.uuid4(), source="canonical", approved_only=True, auth=None, db=None,
    )
    assert result == {"ready": True, "total_items": 2414}


@pytest.mark.asyncio
async def test_status_without_cache_is_not_ready(monkeypatch):
    monkeypatch.setattr(router, "_lookup_run_with_access", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_get_studio_cache_row", AsyncMock(return_value=None))
    result = await router.studio_status(
        uuid.uuid4(), source="canonical", approved_only=True, auth=None, db=None,
    )
    assert result == {"ready": False, "total_items": None}
