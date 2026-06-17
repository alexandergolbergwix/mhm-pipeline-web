"""Tests for 710 pipe-split ownership + kind inference (Allony fixture)."""
from __future__ import annotations

from app.pipeline.marc_ingest import (
    _collapse_marc_subfields,
    extract_named_entities,
    prepare_record_for_pipeline,
)


def _allony_collapsed_record() -> dict:
    record: dict = {
        "710$a": '"The National Library of Israel|Allony, Nehemia"',
        "710$e": '"current owner|former owner"',
        "700$a": '"זנגר, יוסף"',
        "700$e": '"בעלים קודמים"',
    }
    _collapse_marc_subfields(record)
    return record


def test_collapse_splits_710_into_two_contributors() -> None:
    record = _allony_collapsed_record()
    contribs = record.get("contributors") or []
    names = {c["name"] for c in contribs if isinstance(c, dict)}
    assert "The National Library of Israel" in names
    assert "Allony, Nehemia" in names


def test_allony_is_person_nli_is_corporate() -> None:
    record = _allony_collapsed_record()
    entities = extract_named_entities(record)
    by_text = {e["text"]: e for e in entities}
    assert by_text["Allony, Nehemia"]["kind"] == "person"
    assert by_text["Allony, Nehemia"]["role"] == "former owner"
    assert by_text["The National Library of Israel"]["kind"] == "corporate"
    assert by_text["The National Library of Israel"]["role"] == "current owner"


def test_zenger_stays_person_from_700() -> None:
    record = _allony_collapsed_record()
    entities = extract_named_entities(record)
    zenger = [e for e in entities if "זנגר" in e["text"]]
    assert len(zenger) == 1
    assert zenger[0]["kind"] == "person"
    assert zenger[0]["role"] == "former owner"


def test_prepare_expands_unsplit_desktop_contributor() -> None:
    record = {
        "contributors": [{
            "name": "The National Library of Israel|Allony, Nehemia",
            "role": "current owner|former owner",
            "field": "710",
        }],
    }
    prepared = prepare_record_for_pipeline(record)
    entities = extract_named_entities(prepared)
    kinds = {(e["text"], e["kind"]) for e in entities}
    assert ("Allony, Nehemia", "person") in kinds
    assert ("The National Library of Israel", "corporate") in kinds


def test_quote_roles_normalized() -> None:
    record = _allony_collapsed_record()
    entities = extract_named_entities(record)
    roles = {e["role"] for e in entities}
    assert 'former owner"' not in roles
    assert "former owner" in roles
