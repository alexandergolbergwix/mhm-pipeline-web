from __future__ import annotations

import uuid

import pytest

from app.models.wikidata_studio_cache import WikidataStudioCache
from app.routers.wikidata_studio import _get_studio_cache_row
from app.pipeline.wikidata_item_views import fetch_merged_wikidata_items


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


@pytest.mark.asyncio
async def test_merged_view_lookup_is_source_scoped(db_session) -> None:
    run_id = uuid.uuid4()
    common = {
        "run_id": run_id,
        "approved_only": True,
        "input_fingerprint": "a" * 64,
        "result_items": [{"local_id": "legacy-only"}],
        "quickstatements": "",
        "summary": {"total_items": 1},
        "approved_match_count": 0,
        "pending_match_count": 0,
        "used_match_count": 0,
        "record_count": 1,
    }
    db_session.add(WikidataStudioCache(**common, source="legacy"))
    canonical_row = {**common, "source": "canonical", "result_items": [{"local_id": "canonical-only"}]}
    db_session.add(WikidataStudioCache(**canonical_row))
    await db_session.commit()

    legacy = await fetch_merged_wikidata_items(db_session, run_id, source="legacy")
    canonical = await fetch_merged_wikidata_items(db_session, run_id, source="canonical")

    assert [row["local_id"] for row in legacy] == ["legacy-only"]
    assert [row["local_id"] for row in canonical] == ["canonical-only"]
