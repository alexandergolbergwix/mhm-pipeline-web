"""Wikidata Studio AI verdict cache invalidation."""

from __future__ import annotations

from app.pipeline.wikidata_verdict_cache import (
    WIKIDATA_VERDICT_SCHEMA,
    sanitise_stale_wikidata_verdict,
    wikidata_verdict_input_fingerprint,
    wikidata_verdict_query_summary,
)


def test_query_summary_changes_when_labels_change() -> None:
    item = {
        "_local_id": "manuscript_1",
        "entity_type": "manuscript",
        "record_ids": ["990000403370205171"],
        "labels": {"en": "MS 1"},
        "descriptions": {"en": "Hebrew manuscript."},
        "statements": [],
        "validation_issues": [],
        "_marc_context": {"title": "MS 1"},
    }
    a = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    item["labels"] = {"en": "MS 1 revised"}
    b = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    assert a != b


def test_query_summary_changes_when_marc_context_changes() -> None:
    item = {
        "_local_id": "manuscript_1",
        "entity_type": "manuscript",
        "record_ids": ["990000403370205171"],
        "labels": {"en": "MS 1"},
        "descriptions": {"en": "Hebrew manuscript."},
        "statements": [],
        "validation_issues": [],
        "_marc_context": {"authors": "Author A"},
    }
    a = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    item["_marc_context"] = {"authors": "Author A", "notes": "Colophon"}
    b = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    assert a != b


def test_query_summary_includes_schema_salt() -> None:
    summary = wikidata_verdict_query_summary(
        {"_local_id": "x", "labels": {}, "descriptions": {}},
        "gemini-3.5-flash",
    )
    assert summary["wikidata_verdict_schema"] == WIKIDATA_VERDICT_SCHEMA


def test_sanitise_stale_wikidata_verdict_hides_mismatched_key() -> None:
    item = {
        "_local_id": "manuscript_1",
        "labels": {"en": "MS 1"},
        "descriptions": {"en": "Hebrew manuscript."},
        "statements": [],
        "_marc_context": {},
    }
    stored = {
        "overall": "full",
        "cache_key": "stale-eval-agent-prompt-hash",
        "model": "gemini-3.5-flash",
        "evaluator": "wikidata_item",
    }
    assert sanitise_stale_wikidata_verdict(item, stored) is None


def test_sanitise_stale_wikidata_verdict_keeps_matching_key() -> None:
    item = {
        "_local_id": "manuscript_1",
        "labels": {"en": "MS 1"},
        "descriptions": {"en": "Hebrew manuscript."},
        "statements": [],
        "_marc_context": {},
    }
    fp = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    stored = {
        "overall": "full",
        "cache_key": fp,
        "model": "gemini-3.5-flash",
        "evaluator": "wikidata_item",
    }
    kept = sanitise_stale_wikidata_verdict(item, stored)
    assert kept is not None
    assert kept["cache_key"] == fp



def test_record_ids_recover_from_nli_reference_when_legacy_item_lacks_records() -> None:
    item = {
        "statements": [{
            "references": [
                {"property": "P248", "value": "Q118384267"},
                {"property": "P3959", "value": "990000000000000123"},
            ],
        }],
    }
    assert wikidata_verdict_query_summary(item)["record_ids"] == ["990000000000000123"]
