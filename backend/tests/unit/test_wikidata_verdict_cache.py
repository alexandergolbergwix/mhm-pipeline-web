"""Wikidata Studio AI verdict cache invalidation."""

from __future__ import annotations

from app.pipeline.wikidata_verdict_cache import (
    WIKIDATA_VERDICT_SCHEMA,
    attach_local_reference_targets,
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


def test_query_summary_changes_when_verifier_evidence_changes() -> None:
    item = {
        "_local_id": "person_1",
        "entity_type": "person",
        "labels": {"en": "Jane Doe"},
        "descriptions": {"en": "Person"},
        "statements": [],
        "authority_evidence": [{"source": "NLI", "birth_year": 1950}],
        "local_reference_targets": {},
    }
    a = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    item["authority_evidence"] = [{"source": "NLI", "birth_year": 1951}]
    b = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    assert a != b


def test_query_summary_changes_when_work_candidate_evidence_changes() -> None:
    item = {
        "_local_id": "work:test",
        "entity_type": "work",
        "statements": [],
        "work_candidate_evidence": {"source_text": "Work by Author A"},
    }
    before = wikidata_verdict_input_fingerprint(item)
    item["work_candidate_evidence"] = {"source_text": "Work by Author B"}
    assert wikidata_verdict_input_fingerprint(item) != before


def test_query_summary_changes_when_statement_labels_change() -> None:
    item = {
        "_local_id": "manuscript:1",
        "statements": [{
            "property": "P921",
            "property_label": "main subject",
            "value": "Q107427",
            "value_label": "Halakha",
        }],
    }
    before = wikidata_verdict_input_fingerprint(item)
    item["statements"][0]["value_label"] = "incorrect label"
    assert wikidata_verdict_input_fingerprint(item) != before


def test_attach_local_reference_targets_uses_full_item_set() -> None:
    person = {
        "local_id": "person:1",
        "entity_type": "person",
        "labels": {"en": "Jane Doe"},
        "authority_evidence": [{"source": "NLI", "role": "author"}],
    }
    manuscript = {
        "local_id": "manuscript:1",
        "entity_type": "manuscript",
        "statements": [{"property": "P50", "value": "__LOCAL:person:1"}],
    }

    attach_local_reference_targets([manuscript, person])

    assert manuscript["local_reference_targets"]["person:1"] == {
        "entity_type": "person",
        "labels": {"en": "Jane Doe"},
        "existing_qid": None,
        "authority_evidence": [{"source": "NLI", "role": "author"}],
    }
