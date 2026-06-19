"""Regression tests derived from Gilla's supervisor examples.

These tests are fixture-driven (mock Mazal rows) and verify the specific
failure modes reported:
  1. Nehemya Allony → personality record (tag 100), NOT subject (tag 150).
  2. Place strings must NOT receive a person mazal_id.
  3. Colophon structured extraction: control_number 990025632890205171.
  4. Work mentions from "כולל:" notes.
  5. guard_mazal_subject_heading fires for non-100 person matches.
"""
from __future__ import annotations

import pytest


# ── 1 + 2. Nehemya Allony — personality vs subject ────────────────────────


def test_guard_mazal_subject_heading_fires_for_tag_150_author() -> None:
    """guard_mazal_subject_heading must fire when main_marc_tag='150' and role='author'."""
    from app.pipeline.authority_hardening import guard_mazal_subject_heading

    verdict = guard_mazal_subject_heading(
        main_marc_tag="150",
        entity_kind="person",
        role="author",
    )
    assert verdict.fired, "guard must fire for tag 150 + person author"
    assert "mazal_subject_not_personality" in verdict.flag
    assert verdict.new_confidence == "medium"


def test_guard_mazal_subject_heading_does_not_fire_for_tag_100_author() -> None:
    """guard_mazal_subject_heading must NOT fire for the correct personality tag."""
    from app.pipeline.authority_hardening import guard_mazal_subject_heading

    verdict = guard_mazal_subject_heading(
        main_marc_tag="100",
        entity_kind="person",
        role="author",
    )
    assert not verdict.fired, "guard must NOT fire for tag 100 (אישיות)"


def test_guard_mazal_subject_heading_does_not_fire_for_subject_role() -> None:
    """A person-subject (600) entity may legitimately hit a subject heading."""
    from app.pipeline.authority_hardening import guard_mazal_subject_heading

    verdict = guard_mazal_subject_heading(
        main_marc_tag="150",
        entity_kind="person",
        role="subject",
    )
    assert not verdict.fired, (
        "guard must NOT fire when role=subject — subject entities may hit subject headings"
    )


def test_guard_mazal_subject_heading_does_not_fire_for_missing_tag() -> None:
    """When main_marc_tag is absent (old rows before migration 0020), guard must be silent."""
    from app.pipeline.authority_hardening import guard_mazal_subject_heading

    verdict = guard_mazal_subject_heading(
        main_marc_tag=None,
        entity_kind="person",
        role="author",
    )
    assert not verdict.fired, "guard must NOT fire when main_marc_tag is None"


def test_apply_hardening_includes_subject_heading_guard() -> None:
    """apply_hardening_guards must include the mazal_subject_not_personality flag
    when the candidate payload carries a non-100 main_marc_tag for a person author."""
    from app.pipeline.authority_hardening import HardeningContext, apply_hardening_guards

    candidate = {
        "matched_name": "אלוני, נחמיה",
        "entity_text": "אלוני, נחמיה",
        "entity_kind": "person",
        "confidence": "high",
        "mazal_id": "987001234567",
        "viaf_id": "",
        "wikidata_qid": "",
        "payload": {
            "guard_flags": [],
            "main_marc_tag": "150",  # subject heading — should trigger guard
        },
    }
    result = apply_hardening_guards(
        candidate,
        context=HardeningContext(entity_kind="person", role="author"),
    )
    assert "mazal_subject_not_personality" in result["payload"]["guard_flags"], (
        "apply_hardening_guards must stamp mazal_subject_not_personality flag"
    )
    assert result["confidence"] in ("medium", "low"), (
        "confidence must be downgraded from high"
    )


def test_guard_mazal_subject_heading_does_not_fire_after_rematch() -> None:
    from app.pipeline.authority_hardening import guard_mazal_subject_heading

    verdict = guard_mazal_subject_heading(
        main_marc_tag="150",
        entity_kind="person",
        role="author",
        payload={"personality_rematch_from": "987001234567"},
    )
    assert not verdict.fired


def test_guard_mazal_entity_type_mismatch_fires() -> None:
    from app.pipeline.authority_hardening import guard_mazal_entity_type_mismatch

    verdict = guard_mazal_entity_type_mismatch(
        payload={
            "mazal_entity_type_mismatch": True,
            "mazal_expected_entity_type": "person",
            "mazal_got_entity_type": "place",
        },
    )
    assert verdict.fired
    assert verdict.flag == "mazal_entity_type_mismatch"


def test_apply_mazal_entity_type_gate_clears_person_place_mismatch() -> None:
    from app.pipeline.authority import _apply_mazal_entity_type_gate

    mid, details, extras = _apply_mazal_entity_type_gate(
        is_place=False,
        is_person_entity=True,
        entity_kind="person",
        mazal_id="9870",
        mazal_details={"entity_type": "place"},
        extras={},
    )
    assert mid == ""
    assert details is None
    assert extras.get("mazal_entity_type_mismatch") is True


def test_personality_row_from_pg_skips_same_id() -> None:
    from app.pipeline.authority_backend import _personality_row_from_pg

    row = ("987001", "person", "he", "lat", None, None, "100")
    assert _personality_row_from_pg(row, "987001") is None


def test_provenance_institution_candidates_goldschmidt() -> None:
    from app.pipeline.marc_ingest import _provenance_institution_candidates

    record = {
        "provenance": "From the Goldschmidt collection, Jerusalem",
    }
    ents = _provenance_institution_candidates(record)
    assert ents, "expected corporate entity from provenance"
    assert ents[0]["kind"] == "corporate"


# ── 3. Colophon extraction (990025632890205171 example) ───────────────────


def test_500a_colophon_keyword_detected() -> None:
    """A 500$a with the word 'קולופון' should populate colophon_text."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {
        "500$a": "קולופון: נכתב על ידי אברהם בן יצחק בשנת [תרל\"א]"
    }
    _collapse_marc_subfields(record)
    assert record.get("colophon_text"), "colophon_text must be populated from 500$a keyword"
    assert "אברהם" in record.get("colophon_text", "")


def test_colophon_gregorian_year_extracted() -> None:
    """Gregorian year in colophon_text should populate colophon_year."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {
        "590$a": "Colophon: written in 1871 by Avraham ben Yitzhak"
    }
    _collapse_marc_subfields(record)
    assert record.get("colophon_year") == 1871, (
        "colophon_year must be extracted from Gregorian year in colophon_text"
    )


def test_colophon_scribe_extracted() -> None:
    """A 'בן' patronymic in the colophon should populate colophon_scribe."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {
        "590$a": "קולופון: נכתב בן אברהם בשנת תרל"
    }
    _collapse_marc_subfields(record)
    scribe = record.get("colophon_scribe")
    assert scribe, "colophon_scribe must be extracted from patronymic pattern"
    assert "אברהם" in scribe


def test_colophon_text_flows_into_extract_named_entities() -> None:
    """colophon_text appears in the _extract_person_texts feed so NER can see it."""
    from app.pipeline.extraction import _extract_person_texts

    record: dict = {
        "colophon_text": "נכתב בן שלמה הסופר",
    }
    texts = _extract_person_texts(record)
    combined = " ".join(texts)
    assert "שלמה" in combined, (
        "_extract_person_texts must include colophon_text"
    )


# ── 4. Work mentions from notes ───────────────────────────────────────────


def test_work_mentions_extracted_from_kolel_notes() -> None:
    """כולל: triggers work_mentions extraction."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {
        "500$a": "כולל: עת שערי רצון; שיר השירים"
    }
    _collapse_marc_subfields(record)
    mentions = record.get("work_mentions") or []
    titles = [m.get("title") for m in mentions]
    assert any("עת שערי רצון" in t for t in titles), (
        "work_mentions must include 'עת שערי רצון' from כולל: note"
    )
    assert any("שיר השירים" in t for t in titles), (
        "work_mentions must include 'שיר השירים' from semicolon-separated list"
    )


def test_work_mentions_produce_work_entities() -> None:
    """Work mentions must surface as kind=work / role=contained_work entities."""
    from app.pipeline.marc_ingest import extract_named_entities

    record = {
        "work_mentions": [
            {"title": "עת שערי רצון", "source_field": "500"},
        ],
    }
    entities = extract_named_entities(record)
    work_entities = [e for e in entities if e.get("kind") == "work"]
    assert work_entities, "extract_named_entities must yield work entities from work_mentions"
    assert work_entities[0]["role"] == "contained_work"
    assert work_entities[0]["text"] == "עת שערי רצון"


# ── 5. Place entities must NOT receive person mazal_id ────────────────────


def test_place_entity_excluded_from_person_dedup() -> None:
    """A place entity and a person with the same name-text should not
    be collapsed into a single entity by the dedup pass."""
    from app.pipeline.marc_ingest import extract_named_entities

    record = {
        "authors": [{"name": "ירושלים", "role": "author", "field": "100"}],
        "subjects": [{"name": "ירושלים", "type": "place", "field": "651"}],
    }
    entities = extract_named_entities(record)
    kinds = {e.get("kind") for e in entities}
    assert "person" in kinds, "person entity must survive"
    assert "place" in kinds, "place entity must survive — different kind must not be deduped"


def test_person_homonym_same_kind_deduped_by_priority() -> None:
    """When the same name appears as both author and contributor (same kind),
    the author role should win the dedup."""
    from app.pipeline.marc_ingest import extract_named_entities

    record = {
        "authors": [{"name": "שמואל בן עלי", "role": "author", "field": "100"}],
        "contributors": [{"name": "שמואל בן עלי", "role": "contributor", "field": "700"}],
    }
    entities = extract_named_entities(record)
    person_entities = [e for e in entities if e.get("kind") == "person"]
    # Should collapse to 1 entity (same normalized text + same kind)
    assert len(person_entities) == 1, (
        f"expected 1 deduped person entity, got {len(person_entities)}"
    )
    assert person_entities[0]["role"] == "author", (
        "author role should win over contributor in role-priority dedup"
    )
    alt_roles = person_entities[0].get("alt_roles") or []
    assert "contributor" in alt_roles, (
        "contributor must be recorded in alt_roles for audit"
    )
