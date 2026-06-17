"""Corporate (610/110) and topical (650) entity extraction + note blob search."""
from __future__ import annotations


def test_610_corporate_subject_from_collapse() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"610$a": "אוניברסיטת בר-אילן. ספרייה"}
    _collapse_marc_subfields(record)
    subjects = record.get("subjects") or []
    assert any(
        s.get("type") in ("organization", "corporate") and "בר-אילן" in str(s.get("name"))
        for s in subjects
        if isinstance(s, dict)
    )


def test_650_topic_subject_from_collapse() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"650$a": "מקרא"}
    _collapse_marc_subfields(record)
    subjects = record.get("subjects") or []
    assert any(
        s.get("type") == "topic" and s.get("name") == "מקרא"
        for s in subjects
        if isinstance(s, dict)
    )


def test_110_corporate_author_from_collapse() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"110$a": "בית המדרש לרבנים בברלין"}
    _collapse_marc_subfields(record)
    authors = record.get("authors") or []
    assert any(
        isinstance(a, dict) and a.get("field") == "110" and "ברלין" in str(a.get("name"))
        for a in authors
    )


def test_extract_corporate_and_topic_entities() -> None:
    from app.pipeline.marc_ingest import extract_named_entities

    record = {
        "authors": [{"name": "בית המדרש לרבנים בברלין", "field": "110", "role": "author"}],
        "subjects": [
            {"name": "אוניברסיטת בר-אילן", "type": "organization", "field": "610"},
            {"name": "מקרא", "type": "topic", "field": "650"},
        ],
    }
    entities = extract_named_entities(record)
    kinds = {(e["text"], e["kind"], e["role"]) for e in entities}
    assert ("בית המדרש לרבנים בברלין", "corporate", "author") in kinds
    assert ("אוניברסיטת בר-אילן", "corporate", "institution") in kinds
    assert ("מקרא", "topic", "subject") in kinds


def test_build_record_note_blob_concatenates_searchable_text() -> None:
    from app.pipeline.marc_ingest import build_record_note_blob

    record = {
        "notes": ["הערות: כתב יד יפה"],
        "colophon_text": "נכתב בשנת תקנ״ה",
        "colophon_scribe": "משה הסופר",
        "work_mentions": [{"title": "שיר השירים"}],
        "provenance": "נרכש בשנת 1920",
    }
    blob = build_record_note_blob(record)
    assert "הערות" in blob
    assert "נכתב" in blob
    assert "משה הסופר" in blob
    assert "שיר השירים" in blob
    assert "1920" in blob
    assert blob == blob.lower()
