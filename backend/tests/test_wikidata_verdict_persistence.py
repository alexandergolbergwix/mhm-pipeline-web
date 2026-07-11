"""Tests for Wikidata item verdict persistence."""

from __future__ import annotations

import uuid

import pytest

from app.models.item_override import WikidataItemOverride
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline.wikidata_item_verify import _persist_wikidata_verdicts_to_overrides
from app.pipeline.wikidata_item_views import fetch_merged_wikidata_items
from app.pipeline.wikidata_verdict_cache import wikidata_verdict_input_fingerprint


@pytest.mark.asyncio
async def test_persist_verdicts_to_override_rows(db_session) -> None:
    run_id = uuid.uuid4()
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            input_fingerprint="a" * 64,
            result_items=[
                {
                    "local_id": "person::x",
                    "entity_type": "person",
                    "labels": {"he": "ישראל"},
                    "descriptions": {"en": "author"},
                    "statements": [],
                    "validation_issues": [],
                },
            ],
            quickstatements="",
            summary={
                "total_items": 1,
                "manuscripts": 0,
                "persons": 1,
                "works": 0,
                "statements": 0,
            },
            approved_match_count=1,
            pending_match_count=0,
            used_match_count=1,
            record_count=1,
        )
    )
    await db_session.commit()

    item = {
        "local_id": "person::x",
        "entity_type": "person",
        "labels": {"he": "ישראל"},
        "descriptions": {"en": "author"},
        "statements": [],
        "validation_issues": [],
        "_local_id": "person::x",
    }
    await _persist_wikidata_verdicts_to_overrides(
        run_id=run_id,
        items_by_id={"person::x": item},
        verdicts=[{
            "candidate": item,
            "verdict": {
                "overall": "pass",
                "name_ok": "yes",
                "reasoning": "looks good",
            },
            "judge_id": "gemini-3.5-flash",
            "judged_at": "2026-07-10T12:00:00Z",
        }],
        judge_model="gemini-3.5-flash",
    )

    merged = await fetch_merged_wikidata_items(db_session, run_id)
    assert merged[0]["ai_verdict"]["overall"] == "pass"
    assert merged[0]["ai_verdict_at"] is not None


@pytest.mark.asyncio
async def test_stale_verdict_dropped_after_override_edit(db_session) -> None:
    run_id = uuid.uuid4()
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            input_fingerprint="b" * 64,
            result_items=[
                {
                    "local_id": "person::x",
                    "entity_type": "person",
                    "labels": {"he": "ישראל"},
                    "descriptions": {"en": "author"},
                    "statements": [],
                    "validation_issues": [],
                },
            ],
            quickstatements="",
            summary={
                "total_items": 1,
                "manuscripts": 0,
                "persons": 1,
                "works": 0,
                "statements": 0,
            },
            approved_match_count=1,
            pending_match_count=0,
            used_match_count=1,
            record_count=1,
        )
    )
    stale_key = wikidata_verdict_input_fingerprint(
        {
            "local_id": "person::x",
            "entity_type": "person",
            "labels": {"he": "ישראל"},
            "descriptions": {"en": "author"},
            "statements": [],
            "validation_issues": [],
        },
        "gemini-3.5-flash",
    )
    db_session.add(
        WikidataItemOverride(
            run_id=run_id,
            local_id="person::x",
            labels={"he": "ישראל חדש"},
            ai_verdict={
                "overall": "pass",
                "cache_key": stale_key,
                "model": "gemini-3.5-flash",
                "evaluator": "wikidata_item",
            },
        )
    )
    await db_session.commit()

    merged = await fetch_merged_wikidata_items(db_session, run_id)
    assert merged[0]["ai_verdict"] is None
    assert merged[0]["ai_verdict_at"] is None
