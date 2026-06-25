"""Tests for canonical MARC date sources (marc_date_sources.py)."""

from __future__ import annotations

from app.pipeline.marc_date_sources import manuscript_production_year
from app.pipeline.marc_ingest import _collapse_marc_subfields
from app.pipeline.authority import _record_year
from converter.authority.stage3_guards import extract_manuscript_year


class TestManuscriptProductionYear:
    def test_260_c_primary(self) -> None:
        assert manuscript_production_year({"dates": {"year": 1407}}) == 1407

    def test_colophon_year_fallback(self) -> None:
        record = {"500$a": "קולופון: נכתב בשנת 1871"}
        _collapse_marc_subfields(record)
        assert record.get("colophon_year") == 1871
        assert manuscript_production_year(record) == 1871

    def test_ignores_008_and_custody_and_bio(self) -> None:
        record = {
            "008": "150101s1500    is hbrtxt c0",
            "541$d": "1923",
            "100$d": "1135-1204",
            "583$c": "1999",
        }
        _collapse_marc_subfields(record)
        assert manuscript_production_year(record) is None

    def test_shared_by_record_year_and_stage3_guard(self) -> None:
        record = {"dates": {"year": 1612}, "colophon_year": 1871}
        assert _record_year(record) == 1612
        assert extract_manuscript_year(record) == 1612

    def test_century_catalog_uses_colophon_via_record_year(self) -> None:
        from app.pipeline.marc_ingest import _collapse_marc_subfields, prepare_record_for_pipeline

        record = {
            "264$c": 'מאה י"ט (סוף המאה)',
            "500$a": 'קולופון: תרל"א [4/9/1871]',
        }
        _collapse_marc_subfields(record)
        prepared = prepare_record_for_pipeline(record)
        assert prepared.get("colophon_year") == 1871
        assert _record_year(prepared) == 1871

    def test_colophon_only_record(self) -> None:
        record = {"colophon_year": 1723}
        assert manuscript_production_year(record) == 1723
        assert _record_year(record) == 1723

    def test_colophon_wins_over_century_catalog(self) -> None:
        """Gila MS 990025632890205171: מאה י\"ט + colophon תרל\"א → 1871."""
        record = {
            "dates": {
                "original_string": 'מאה י"ט (סוף המאה)',
                "date_format": "HebrewCentury",
                "year_start": 1801,
                "year_end": 1900,
                "year": 1801,
            },
            "colophon_year": 1871,
            "colophon_hebrew_year": 5631,
        }
        assert manuscript_production_year(record) == 1871
        assert _record_year(record) == 1871

    def test_exact_catalog_year_beats_colophon(self) -> None:
        record = {"dates": {"year": 1612}, "colophon_year": 1871}
        assert manuscript_production_year(record) == 1612
        assert _record_year(record) == 1612
