"""Tests for structured colophon extraction (Phase 4A)."""
from __future__ import annotations

import pytest


def test_500_colophon_keyword_sets_colophon_text() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"500$a": "קולופון: הועתק בשנת תרל\"א"}
    _collapse_marc_subfields(record)
    assert record.get("colophon_text"), "500$a with קולופון must set colophon_text"


def test_590_sets_colophon_text() -> None:
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"590$a": "Colophon stating scribe was Shlomo"}
    _collapse_marc_subfields(record)
    assert "Shlomo" in (record.get("colophon_text") or "")


def test_hebrew_year_bracket_extracted() -> None:
    from app.pipeline.marc_ingest import _extract_colophon_fields

    record: dict = {"colophon_text": 'נכתב ב[ה\'תרל"א]'}
    _extract_colophon_fields(record)
    year = record.get("colophon_year")
    assert year is not None, "Hebrew year in brackets should produce colophon_year"
    assert 1860 < year < 1890, f"Year {year} not in expected range for תרל\"א (~1871)"


def test_gregorian_year_extracted() -> None:
    from app.pipeline.marc_ingest import _extract_colophon_fields

    record: dict = {"colophon_text": "Written in 1871 by the scribe Avraham"}
    _extract_colophon_fields(record)
    assert record.get("colophon_year") == 1871


def test_no_year_does_not_crash() -> None:
    from app.pipeline.marc_ingest import _extract_colophon_fields

    record: dict = {"colophon_text": "This colophon has no year"}
    _extract_colophon_fields(record)
    assert record.get("colophon_year") is None


def test_scribe_ben_pattern_extracted() -> None:
    from app.pipeline.marc_ingest import _extract_colophon_fields

    record: dict = {"colophon_text": "נכתב בן יצחק הסופר בשנת 1600"}
    _extract_colophon_fields(record)
    scribe = record.get("colophon_scribe")
    assert scribe, "patronymic 'בן יצחק' must produce colophon_scribe"
    assert "יצחק" in scribe
