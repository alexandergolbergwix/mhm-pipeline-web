"""Unit tests for canonical MARC subject / genre record normalization."""

from __future__ import annotations

from converter.transformer.subject_records import (
    normalize_genre_entries,
    normalize_subject_entry,
    normalize_subjects_list,
    subject_term,
)


def test_subject_term_prefers_term_then_name() -> None:
    assert subject_term({"name": "מקרא"}) == "מקרא"
    assert subject_term({"term": "Responsa", "name": "ignored"}) == "Responsa"
    assert subject_term({}) == ""


def test_normalize_subject_entry_sets_term_and_drops_empty() -> None:
    assert normalize_subject_entry({"name": "הלכה", "type": "topic", "field": "650"}) == {
        "name": "הלכה",
        "term": "הלכה",
        "type": "topic",
        "field": "650",
    }
    assert normalize_subject_entry({"name": "", "type": "topic"}) is None


def test_normalize_subjects_list_dedupes() -> None:
    rows = normalize_subjects_list([
        {"name": "מקרא", "type": "topic", "field": "650"},
        {"term": "מקרא", "type": "topic", "field": "650"},
        {"name": "", "type": "person", "field": "600"},
    ])
    assert len(rows) == 1
    assert rows[0]["term"] == "מקרא"


def test_normalize_genre_entries_preserves_authority() -> None:
    flat, entries = normalize_genre_entries(
        [],
        genre_entries=[{
            "name": "Commentaries",
            "authority_id": "http://example.org/id",
            "source": "lcsh",
        }],
    )
    assert flat == ["Commentaries"]
    assert entries[0]["authority_id"] == "http://example.org/id"
