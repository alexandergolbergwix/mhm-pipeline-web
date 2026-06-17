"""Tests for per-segment entity kind inference (710 pipe-split fix)."""
from __future__ import annotations

from app.pipeline.entity_kind_infer import infer_entity_kind, looks_like_inverted_person


def test_inverted_person_latin() -> None:
    assert looks_like_inverted_person("Allony, Nehemia")
    assert looks_like_inverted_person("Adler, Elkan Nathan")
    assert looks_like_inverted_person("Sassoon, David Solomon")


def test_inverted_person_with_dates() -> None:
    assert looks_like_inverted_person("Adler, Nathan ben Simeon, 1835-1906")


def test_collection_is_not_inverted_person() -> None:
    assert not looks_like_inverted_person("Gaster, Moses Collection")


def test_institution_on_710() -> None:
    assert infer_entity_kind("The National Library of Israel", "710") == "corporate"
    assert infer_entity_kind("The British Library", "710") == "corporate"
    assert infer_entity_kind("Hekhal Shlomo", "710") == "corporate"


def test_person_on_710_pipe_segment() -> None:
    assert infer_entity_kind("Allony, Nehemia", "710") == "person"
    assert infer_entity_kind("Adler, Elkan Nathan", "710") == "person"
    assert infer_entity_kind("Kapah, Joseph", "710") == "person"


def test_gaster_collection_stays_corporate() -> None:
    assert infer_entity_kind("Gaster, Moses Collection", "710") == "corporate"


def test_700_always_person() -> None:
    assert infer_entity_kind("זנגר, יוסף", "700") == "person"


def test_711_meeting() -> None:
    assert infer_entity_kind("Some Conference, 1990", "711") == "meeting"


def test_hebrew_family_person() -> None:
    assert infer_entity_kind("רוויגו (משפחה)", "700") == "person"
