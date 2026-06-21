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

    def test_colophon_only_record(self) -> None:
        record = {"colophon_year": 1723}
        assert manuscript_production_year(record) == 1723
        assert _record_year(record) == 1723
