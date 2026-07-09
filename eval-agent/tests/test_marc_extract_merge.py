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
