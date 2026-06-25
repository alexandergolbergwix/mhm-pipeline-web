"""Tests for provenance DATE normalization to YYYY."""
from __future__ import annotations

from app.pipeline.date_entity_normalize import (
    normalize_date_entity_year,
    normalize_provenance_date_entities,
)


def test_besnat_gregorian() -> None:
    assert normalize_date_entity_year("בשנת 1648") == 1648


def test_besnat_hebrew_gershayim() -> None:
    assert normalize_date_entity_year("בשנת ב'קל\"ז") == 1377


def test_nli_uncertain_censorship_year() -> None:
    assert normalize_date_entity_year("משנת 7[5]16") == 1816


def test_plain_gregorian() -> None:
    assert normalize_date_entity_year("1654") == 1654


def test_marc_equivalent_bracket() -> None:
    assert normalize_date_entity_year("[=1826]") == 1826


def test_hebrew_year_with_prefix() -> None:
    assert normalize_date_entity_year('משנת הת"ר') == 1845


def test_short_fragment_returns_none() -> None:
    assert normalize_date_entity_year("6") is None
    assert normalize_date_entity_year("15") is None


def test_normalize_entities_rewrites_text() -> None:
    ents = [
        {
            "text": "בשנת 1648",
            "type": "DATE",
            "source": "provenance_ner",
        },
        {
            "text": "6",
            "type": "DATE",
            "source": "provenance_ner",
        },
        {
            "text": "1654",
            "type": "PERSON",
            "source": "person_ner",
        },
    ]
    out = normalize_provenance_date_entities(ents)
    assert len(out) == 2
    date = next(e for e in out if e.get("type") == "DATE")
    assert date["text"] == "1648"
    assert date["date_text_raw"] == "בשנת 1648"
    assert date["year"] == 1648
