"""Hebrew ↔ Gregorian calendar enrichment for MARC production dates."""

from __future__ import annotations

from converter.transformer.field_handlers import FieldHandlers
from converter.transformer.hebrew_gregorian_calendar import (
    enrich_dates_with_calendar_fields,
    gregorian_year_to_hebrew_year,
    hebrew_letters_to_gregorian_year,
)
from converter.transformer.gematria import (
    gregorian_to_hebrew_year,
    letters_to_hebrew_year,
)
from app.pipeline.marc_ingest import _collapse_marc_subfields


class TestHebrewGregorianCalendar:
    def test_hebrew_marc_c_gets_both_years(self) -> None:
        parsed = enrich_dates_with_calendar_fields(
            FieldHandlers._parse_date_string('תשכ""ט.')
        )
        assert parsed["hebrew_year"] == 5729
        assert parsed["year"] == 1969
        assert parsed["date_format"] == "HebrewYear"

    def test_gregorian_marc_c_gets_hebrew_year(self) -> None:
        parsed = enrich_dates_with_calendar_fields(
            FieldHandlers._parse_date_string("1612")
        )
        assert parsed["year"] == 1612
        assert parsed["hebrew_year"] == 5372

    def test_converter_aliases(self) -> None:
        assert hebrew_letters_to_gregorian_year('תשפ"ו') == 2026
        assert gregorian_year_to_hebrew_year(2026) == 5786
        assert gregorian_to_hebrew_year(1612) == 5372

    def test_collapse_subset_hebrew_date(self) -> None:
        record = {"260$c": 'תשכ""ט.'}
        _collapse_marc_subfields(record)
        dates = record["dates"]
        assert dates["year"] == 1969
        assert dates["hebrew_year"] == 5729

    def test_colophon_hebrew_and_gregorian(self) -> None:
        record = {"500$a": 'קולופון: נכתב בשנת [ה\'תק\"ז]'}
        _collapse_marc_subfields(record)
        assert record.get("colophon_hebrew_year") == 5507
        assert record.get("colophon_year") == 1747
