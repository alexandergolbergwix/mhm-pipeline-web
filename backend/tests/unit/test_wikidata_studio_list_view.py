"""Studio list_view trim + lean verify fixture helpers (Rule W-131)."""

from __future__ import annotations

import json
from pathlib import Path

from app.pipeline.wikidata_item_views import slim_ai_verdict_for_list, trim_studio_list_item
from app.pipeline.wikidata_verify_fixture import (
    compact_wikidata_verdict_candidate,
    compact_wikidata_verify_fixture_item,
    scope_marc_records_for_items,
    write_wikidata_verify_fixture,
)
from app.routers.wikidata_studio import _cached_wikidata_verdict_event


def test_trim_studio_list_item_drops_bulky_fields() -> None:
    item = {
        "local_id": "990001234",
        "entity_type": "manuscript",
        "labels": {"en": "MS"},
        "statements": [{"property": "P31", "value": "Q87167"}],
        "authority_evidence": [{"kind": "viaf", "value": "123"}],
        "work_candidate_evidence": {"accepted": True},
        "local_reference_targets": {"x": 1},
        "quickstatements": "CREATE",
        "ai_verdict": {
            "overall": "partial",
            "reasoning": "thin label",
            "model": "gemini-3.5-flash",
            "judged_at": "2026-07-27T00:00:00Z",
            "suggested_fixes": [{"field": "labels.en"}],
        },
    }
    trimmed = trim_studio_list_item(item)
    assert "statements" not in trimmed
    assert trimmed["statement_count"] == 1
    assert "authority_evidence" not in trimmed
    assert trimmed["ai_verdict"]["overall"] == "partial"
    assert trimmed["ai_verdict"]["has_suggested_fixes"] is True
    assert "suggested_fixes" not in trimmed["ai_verdict"]


def test_slim_ai_verdict_for_list_none_when_empty() -> None:
    assert slim_ai_verdict_for_list(None) is None
    assert slim_ai_verdict_for_list({}) is None


def test_cached_wikidata_verdict_event_uses_compact_candidate() -> None:
    item = {
        "local_id": "person::Foo",
        "entity_type": "person",
        "existing_qid": "Q5",
        "labels": {"en": "Foo"},
        "statements": [{"property": "P31"}] * 20,
        "authority_evidence": [{"kind": "mazal"}],
    }
    ev = _cached_wikidata_verdict_event(item, {"verdict": {"overall": "pass"}})
    cand = ev["candidate"]
    assert cand["label"] == "Foo"
    assert cand["entity_type"] == "person"
    assert "statements" not in cand
    assert "authority_evidence" not in cand


def test_compact_fixture_item_and_scoped_marc(tmp_path: Path) -> None:
    items = [
        {
            "_local_id": "990001234",
            "local_id": "990001234",
            "entity_type": "manuscript",
            "labels": {"en": "MS"},
            "statements": [{"property": "P31", "value": "Q87167", "value_id": "Q87167"}],
            "record_ids": ["990001234"],
            "verify_evidence": {
                "marc_present": True,
                "marc": {"title": "Big MARC blob"},
                "viaf": {"authority_rows": []},
            },
        },
    ]
    marc = [
        {"_control_number": "990001234", "title": "MS title"},
        {"_control_number": "990009999", "title": "other"},
    ]
    compact = compact_wikidata_verify_fixture_item(items[0])
    assert compact["statement_count"] == 1
    assert "marc" not in (compact.get("verify_evidence") or {})

    scoped = scope_marc_records_for_items([compact], marc)
    assert len(scoped) == 1
    assert scoped[0]["title"] == "MS title"

    write_wikidata_verify_fixture(
        dest_dir=tmp_path,
        marc_records=marc,
        items=items,
    )
    raw_items = json.loads((tmp_path / "wikidata_items.json").read_text(encoding="utf-8"))
    raw_marc = json.loads((tmp_path / "marc_extracted.json").read_text(encoding="utf-8"))
    assert len(raw_items) == 1
    assert len(raw_marc) == 1
    assert "\n  " not in (tmp_path / "wikidata_items.json").read_text(encoding="utf-8")


def test_compact_wikidata_verdict_candidate() -> None:
    cand = compact_wikidata_verdict_candidate(
        {"local_id": "x", "entity_type": "work", "existing_qid": "Q1"},
        label="Work title",
    )
    assert cand == {
        "_local_id": "x",
        "_item_id": "x",
        "local_id": "x",
        "label": "Work title",
        "entity_type": "work",
        "existing_qid": "Q1",
    }
