"""Tests for Wikidata item verdict persistence."""

from __future__ import annotations

import uuid

import pytest

from app.models.item_override import WikidataItemOverride
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline.marc_verify_context import load_run_marc_records
from app.pipeline.wikidata_item_verify import _persist_wikidata_verdicts_to_overrides
from app.pipeline.wikidata_item_views import fetch_merged_wikidata_items
from app.pipeline.wikidata_verdict_cache import (
    WIKIDATA_VERDICT_KEY_VERSION,
    marc_context_for_wikidata_item,
    sanitise_stale_wikidata_verdict,
    wikidata_verdict_input_fingerprint,
)


@pytest.mark.asyncio
async def test_persist_verdicts_to_override_rows(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            input_fingerprint="a" * 64,
            result_items=[
                {
                    "local_id": "person::x",
                    "records": ["990000000000000001"],
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
        "record_ids": ["990000000000000001"],
        "entity_type": "person",
        "labels": {"he": "ישראל"},
        "descriptions": {"en": "author"},
        "statements": [],
        "validation_issues": [],
        "_local_id": "person::x",
    }
    marc_records = await load_run_marc_records(db_session, run_id)
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
        marc_records=marc_records,
    )

    merged = await fetch_merged_wikidata_items(db_session, run_id)
    assert merged[0]["ai_verdict"]["overall"] == "pass"
    assert merged[0]["ai_verdict"]["cache_key_version"] == WIKIDATA_VERDICT_KEY_VERSION
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


def test_legacy_verdict_with_pre_fix_fingerprint_remains_visible() -> None:
    raw_item = {
        "local_id": "person::x",
        "records": ["990000000000000001"],
        "entity_type": "person",
        "labels": {"he": "ישראל"},
        "descriptions": {"en": "author"},
        "statements": [],
        "validation_issues": [],
    }
    worker_item = {**raw_item, "record_ids": raw_item["records"]}
    marc_context = marc_context_for_wikidata_item(
        raw_item,
        [{"_control_number": "990000000000000001", "title": "Sefer"}],
    )
    legacy_key = wikidata_verdict_input_fingerprint(
        worker_item,
        "gemini-3.5-flash",
        marc_context={},
    )
    current_key = wikidata_verdict_input_fingerprint(
        raw_item,
        "gemini-3.5-flash",
        marc_context=marc_context,
    )
    assert legacy_key != current_key

    restored = sanitise_stale_wikidata_verdict(
        raw_item,
        {
            "overall": "pass",
            "cache_key": legacy_key,
            "model": "gemini-3.5-flash",
            "evaluator": "wikidata_item",
        },
        marc_context=marc_context,
    )

    assert restored is not None
    assert restored["overall"] == "pass"
    assert restored["cache_key"] == current_key
    assert restored["cache_key_version"] == WIKIDATA_VERDICT_KEY_VERSION
