"""Production dates must come from 260/264 $c, never MARC 008."""

from __future__ import annotations

from app.pipeline.marc_ingest import _collapse_marc_subfields, _dates_from_260_264
from converter.parser.marc_reader import MarcField, MarcRecord
from converter.transformer.field_handlers import FieldHandlers, extract_all_data


class TestHandle008SkipsCatalogDates:
    def test_008_bytes_7_14_not_treated_as_production_year(self) -> None:
        field = MarcField(
            tag="008",
            data="850101s1407    xx            000 0 heb d       ",
        )
        info = FieldHandlers.handle_008(field)
        assert "date_start" not in info
        assert "date_end" not in info
        assert "date_type" not in info
        assert info.get("language") == "heb"


class TestDatesFrom260264:
    def test_collapse_uses_264_c(self) -> None:
        record = {"264$c": "1612"}
        _collapse_marc_subfields(record)
        assert record["dates"]["year"] == 1612

    def test_collapse_prefers_260_over_empty_264(self) -> None:
        record = {"260$c": "1523", "264$c": ""}
        _collapse_marc_subfields(record)
        assert record["dates"]["year"] == 1523

    def test_helper_parses_hebrew_year_string(self) -> None:
        parsed = _dates_from_260_264({"264$c": "שנת תק\"ז"})
        assert parsed is not None
        assert parsed.get("date_format") == "HebrewYear"

    def test_extract_all_data_uses_260_not_008(self) -> None:
        record = MarcRecord(control_number="CN1")
        record.fields["008"] = [
            MarcField(tag="008", data="150101s1500    is hbrtxt c0        ")
        ]
        record.fields["264"] = [
            MarcField(tag="264", subfields={"a": ["X"], "c": ["1407"]})
        ]
        data = extract_all_data(record)
        assert data.dates.get("year") == 1407
        assert data.dates.get("date_start") is None
