"""MARC extract merge helpers."""

from __future__ import annotations

from eval_agent.ingest import marc_extract


def test_merge_records_unions_authors() -> None:
    rec_a = {
        "_control_number": "990000403370205171",
        "title": "MS A",
        "authors": [{"name": "Author A"}],
    }
    rec_b = {
        "_control_number": "990000880710205171",
        "title": "MS B",
        "authors": [{"name": "Author B"}],
    }
    merged = marc_extract.merge_records([rec_a, rec_b], primary=rec_a)
    assert merged["title"] == "MS A"
    names = {a["name"] for a in merged["authors"]}
    assert names == {"Author A", "Author B"}


def test_project_many_merges_linked_manuscripts() -> None:
    index = {
        "990000403370205171": {
            "_control_number": "990000403370205171",
            "authors": [{"name": "Shared Author"}],
        },
        "990000880710205171": {
            "_control_number": "990000880710205171",
            "contributors": [{"name": "Scribe X", "role": "scribe"}],
        },
    }
    projected = marc_extract.project_many(
        index,
        ["990000880710205171", "990000403370205171"],
        ["authors", "contributors"],
        primary_cn="990000880710205171",
    )
    assert "Shared Author" in projected["authors"]
    assert "Scribe X" in projected["contributors"]


def test_index_by_id_canonicalises_quoted_control_number() -> None:
    # Stage-1 persists _control_number with literal surrounding quotes; the
    # index must key on the clean digits so a clean item control number joins.
    records = [{"_control_number": '"990000827290205171"', "title": "פיוטים ושירים"}]
    index = marc_extract.index_by_id(records)
    assert "990000827290205171" in index
    assert index["990000827290205171"]["title"] == "פיוטים ושירים"


def test_canonical_control_number_strips_quotes_and_space() -> None:
    assert marc_extract.canonical_control_number('"990"') == "990"
    assert marc_extract.canonical_control_number("  990 ") == "990"
    assert marc_extract.canonical_control_number(None) == ""


def test_project_fills_missing_slices_from_raw_marc_tags() -> None:
    """Rule W-137 — collapsed-key runs store raw ``NNN$x`` keys only."""
    record = {
        "_control_number": "990000592310205171",
        "title": "גלא עמיקתא",
        "008": "1651",
        "300$a": "39 leaves",
        "852$j": "F 7956",
    }
    out = marc_extract.project(record, ["title", "dates", "extent", "shelfmark"])
    assert out["title"] == "גלא עמיקתא"
    assert out["dates"] == "008: 1651"
    assert "39 leaves" in out["extent"]
    assert "F 7956" in out["shelfmark"]


def test_normalised_values_are_not_overwritten_by_raw_tags() -> None:
    record = {"_control_number": "1", "extent": "12 folios", "300$a": "39 leaves"}
    assert marc_extract.project(record, ["extent"])["extent"] == "12 folios"
