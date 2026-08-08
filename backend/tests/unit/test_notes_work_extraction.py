"""Tests for work-mentions extraction from notes (Phase 4B)."""
from __future__ import annotations


def test_kolel_extracts_two_works() -> None:
    """'כולל: title1; title2' should produce two work_mentions."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"500$a": "כולל: עת שערי רצון; שיר השירים"}
    _collapse_marc_subfields(record)
    mentions = record.get("work_mentions") or []
    titles = [m["title"] for m in mentions]
    assert any("עת שערי רצון" in t for t in titles)
    assert any("שיר השירים" in t for t in titles)


def test_ubo_trigger_extracts_work() -> None:
    """'ובו:' trigger keyword should also fire."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"500$a": "ובו: מסכת ברכות"}
    _collapse_marc_subfields(record)
    mentions = record.get("work_mentions") or []
    assert any("מסכת ברכות" in m["title"] for m in mentions)


def test_work_mentions_source_field_is_500() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"500$a": "כולל: ספר חסידים"}
    _collapse_marc_subfields(record)
    mentions = record.get("work_mentions") or []
    assert all(m.get("source_field") == "500" for m in mentions)


def test_short_titles_skipped() -> None:
    """Titles shorter than 3 characters must be filtered out."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"500$a": "כולל: אב"}
    _collapse_marc_subfields(record)
    mentions = record.get("work_mentions") or []
    assert not mentions, "2-char title must be skipped"


def test_work_entities_kind() -> None:
    from app.pipeline.marc_ingest import extract_named_entities

    record = {
        "work_mentions": [{"title": "תשובות הרמב\"ם", "source_field": "500"}],
    }
    entities = extract_named_entities(record)
    work_ents = [e for e in entities if e.get("kind") == "work"]
    assert len(work_ents) == 1
    assert work_ents[0]["role"] == "contained_work"


def test_prepare_merges_work_mentions_into_contents() -> None:
    from app.pipeline.marc_ingest import prepare_record_for_pipeline

    record = {"500$a": "כולל: עת שערי רצון; שיר השירים"}
    prepared = prepare_record_for_pipeline(record)
    titles = [c.get("title") for c in prepared.get("contents") or []]
    assert any("עת שערי רצון" in str(t) for t in titles)
    assert any("שיר השירים" in str(t) for t in titles)


def test_place_from_related_751_role() -> None:
    """Bare / 'related place' 751 must NOT become production place (export-31)."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record = {
        "751$a": "Prague",
        "751$e": "related place",
    }
    _collapse_marc_subfields(record)
    assert record.get("place") in (None, "")
    assert "Prague" in (record.get("related_places") or [])


def test_place_from_751_writing_role() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record = {
        "751$a": "Prague",
        "751$e": "place of writing",
    }
    _collapse_marc_subfields(record)
    assert record.get("place") == "Prague"


def test_bare_751_does_not_fill_production_place() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record = {"751$a": "ʻAmrān (Yemen)"}
    _collapse_marc_subfields(record)
    assert record.get("place") in (None, "")
    assert record.get("related_places") == ["ʻAmrān (Yemen)"]


def test_place_from_260_a() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record = {"260$a": "Jerusalem"}
    _collapse_marc_subfields(record)
    assert record.get("place") == "Jerusalem"


def test_contextual_kolel_strips_catalogue_prefix_and_splits_named_heads() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record = {"500$a": 'כה"י כולל את שני החלקים הראשונים: ספר הדרושים וספר הכונות.'}
    _collapse_marc_subfields(record)
    assert [row["title"] for row in record["work_mentions"]] == [
        "ספר הדרושים",
        "ספר הכונות",
    ]


def test_quoted_titles_exclude_unquoted_category_words() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record = {
        "500$a": 'כולל: ""סדר קריאת השבוע"" ו""סדר קריאת השבתות"" '
        'ומועדים, ""מי שברך"", ""יקום פורקן"", ""אב הרחמים"", ו""יזכור"".',
    }
    _collapse_marc_subfields(record)
    titles = [row["title"] for row in record["work_mentions"]]
    assert titles == [
        "סדר קריאת השבוע",
        "סדר קריאת השבתות",
        "מי שברך",
        "יקום פורקן",
        "אב הרחמים",
        "יזכור",
    ]
    assert "מועדים" not in titles


def test_kolel_inside_person_prose_is_not_a_work_trigger() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record = {"500$a": "החכם הכולל מנדבנים בפולין."}
    _collapse_marc_subfields(record)
    assert not record.get("work_mentions")


def test_505_contents_retain_source_and_folio_evidence() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record = {"505$a": "1) דף א-ב : ספר הדרושים"}
    _collapse_marc_subfields(record)
    assert record["contents"] == [{
        "title": "ספר הדרושים",
        "source_field": "505",
        "candidate_kind": "named_work",
        "source_text": "1) דף א-ב : ספר הדרושים",
        "folio_range": "א-ב",
        "sequence": 1,
    }]


def test_stale_persisted_500_candidates_are_reparsed() -> None:
    from app.pipeline.marc_ingest import prepare_record_for_pipeline

    record = {
        "500$a": "החכם הכולל מנדבנים בפולין.",
        "work_mentions": [{
            "title": "נדבנים",
            "source_field": "500",
            "candidate_kind": "named_work",
        }],
        "contents": [{
            "title": "נדבנים",
            "source_field": "500",
            "candidate_kind": "named_work",
        }],
    }
    prepared = prepare_record_for_pipeline(record)
    assert not prepared.get("work_mentions")
    assert not prepared.get("contents")
