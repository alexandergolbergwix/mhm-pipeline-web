"""Hebrew / NLI MARC 260$c date parsing."""

from __future__ import annotations

from converter.transformer.field_handlers import FieldHandlers
from app.pipeline.marc_date_sources import manuscript_production_year
from app.pipeline.marc_ingest import _collapse_marc_subfields, _dates_from_260_264


class TestHebrewCenturyParsing:
    def test_eleventh_century(self) -> None:
        parsed = FieldHandlers._parse_date_string('מאה י""א.')
        assert parsed["year_start"] == 1001
        assert parsed["year_end"] == 1100
        assert parsed["year"] == 1001
        assert parsed["date_format"] == "HebrewCentury"

    def test_sixteenth_seventeenth_century_range(self) -> None:
        parsed = FieldHandlers._parse_date_string('מאה ט""ז-י""ז.')
        assert parsed["year_start"] == 1501
        assert parsed["year_end"] == 1700
        assert parsed["century_start"] == 16
        assert parsed["century_end"] == 17

    def test_second_century_bce(self) -> None:
        parsed = FieldHandlers._parse_date_string("מאה שניה לפני הספירה.")
        assert parsed.get("bce") is True
        assert parsed["year_start"] == -199
        assert parsed["year_end"] == -100
        assert manuscript_production_year({"dates": parsed}) == -199


class TestStandaloneHebrewYear:
    def test_tashkaz_doubled_quotes(self) -> None:
        parsed = FieldHandlers._parse_date_string('תשכ""ט.')
        assert parsed["year"] == 1969
        assert parsed["date_format"] == "HebrewYear"

    def test_collapse_subset_style(self) -> None:
        record = {"260$c": 'תשכ""ט.'}
        _collapse_marc_subfields(record)
        assert manuscript_production_year(record) == 1969


class TestSubsetPreviouslyUnparsed:
    CASES = [
        ("990001026150205171", 'מאה י""א.'),
        ("990001402000205171", "מאה שניה לפני הספירה."),
        ("990019020880205171", 'תשכ""ט.'),
        ("990000927260205171", 'מאה ט""ז-י""ז.'),
    ]

    def test_all_four_parse(self) -> None:
        for _cn, marc_c in self.CASES:
            parsed = _dates_from_260_264({"260$c": marc_c})
            assert parsed is not None, marc_c
            assert parsed.get("year") is not None or parsed.get("year_start") is not None, marc_c
