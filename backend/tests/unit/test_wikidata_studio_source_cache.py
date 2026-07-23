from __future__ import annotations

import uuid

import pytest

from app.models.wikidata_studio_cache import WikidataStudioCache
from app.routers.wikidata_studio import _get_studio_cache_row


@pytest.mark.asyncio
async def test_studio_cache_lookup_is_source_scoped(db_session) -> None:
    run_id = uuid.uuid4()
    common = {
        "run_id": run_id,
        "approved_only": True,
        "input_fingerprint": "a" * 64,
        "result_items": [],
        "quickstatements": "",
        "summary": {},
        "approved_match_count": 0,
        "pending_match_count": 0,
        "used_match_count": 0,
        "record_count": 0,
    }
    db_session.add(WikidataStudioCache(**common, source="legacy"))
    db_session.add(WikidataStudioCache(**common, source="canonical"))
    await db_session.commit()

    legacy = await _get_studio_cache_row(db_session, run_id, True, "legacy")
    canonical = await _get_studio_cache_row(db_session, run_id, True, "canonical")

    assert legacy is not None and legacy.source == "legacy"
    assert canonical is not None and canonical.source == "canonical"
