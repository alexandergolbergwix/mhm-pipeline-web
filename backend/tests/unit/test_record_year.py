"""Regression tests for _record_year — the function that extracts the
manuscript production year from a MARC record dict.

Each test captures a real bug that reached production:

  Bug 1 (2026-06-04): the function checked "dates" (dict), "year", and
  "production_year" but NOT "date" (singular).  Some legacy ingest paths
  wrote marc["date"] as a scalar, so ms_year was always None for those
  records and the Dates & guards tab showed "—" for every manuscript.

  Bug 2 (latent): string values like '"1612"' (with embedded quotes from
  JSON round-trips) were not stripped before int() parsing, causing
  ValueError and silent fallthrough to None.
"""

from __future__ import annotations

import pytest

from app.pipeline.authority import _record_year


# ── "date" (singular) — the NLI MARC ingest path ─────────────────────


class TestDateSingularKey:
    """marc['date'] is a legacy scalar key on some stored records."""

    def test_date_as_int(self) -> None:
        assert _record_year({"date": 1612}) == 1612

    def test_date_as_plain_string(self) -> None:
        assert _record_year({"date": "1612"}) == 1612

    def test_date_as_quoted_string(self) -> None:
        """Stored as '"1612"' with surrounding quotes — must be stripped."""
        assert _record_year({"date": '"1612"'}) == 1612

    def test_date_long_string_takes_first_four_digits(self) -> None:
        assert _record_year({"date": "1612 (some extra text)"}) == 1612

    def test_date_non_numeric_returns_none(self) -> None:
        assert _record_year({"date": "undated"}) is None

    def test_date_empty_string_returns_none(self) -> None:
        assert _record_year({"date": ""}) is None

    def test_date_none_value_skipped(self) -> None:
        assert _record_year({"date": None}) is None


# ── "year" and "production_year" — alternate ingest paths ────────────


class TestYearKey:
    def test_year_as_int(self) -> None:
        assert _record_year({"year": 1500}) == 1500

    def test_year_as_string(self) -> None:
        assert _record_year({"year": "1500"}) == 1500

    def test_production_year_as_int(self) -> None:
        assert _record_year({"production_year": 1723}) == 1723

    def test_production_year_as_string(self) -> None:
        assert _record_year({"production_year": "1723"}) == 1723


# ── "dates" dict — desktop pipeline ingest path ──────────────────────


class TestDatesDict:
    def test_dates_dict_year_key(self) -> None:
        assert _record_year({"dates": {"year": 1600}}) == 1600

    def test_dates_dict_production_key(self) -> None:
        assert _record_year({"dates": {"production": 1650}}) == 1650

    def test_dates_dict_publication_key(self) -> None:
        assert _record_year({"dates": {"publication": "1700"}}) == 1700

    def test_dates_string_parsed_before_scalar_year(self) -> None:
        """Canonical production year reads dates scalar before legacy year key."""
        assert _record_year({"dates": "1800", "year": 1801}) == 1800

    def test_dates_dict_unknown_keys_ignored(self) -> None:
        assert _record_year({"dates": {"unknown_key": 1234}}) is None


# ── Priority: dates dict wins if present ─────────────────────────────


class TestPriority:
    def test_dates_dict_wins_over_date_scalar(self) -> None:
        assert _record_year({"dates": {"year": 1600}, "date": "1700"}) == 1600

    def test_date_scalar_wins_over_year_scalar(self) -> None:
        """'date' is checked before 'year' in the scalar loop."""
        assert _record_year({"date": "1600", "year": 1700}) == 1600

    def test_year_wins_over_production_year(self) -> None:
        assert _record_year({"year": 1600, "production_year": 1700}) == 1600


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_record_returns_none(self) -> None:
        assert _record_year({}) is None

    def test_unrelated_fields_return_none(self) -> None:
        assert _record_year({"title": "Some MS", "authors": []}) is None

    def test_whitespace_only_string_returns_none(self) -> None:
        assert _record_year({"date": "   "}) is None

    def test_single_quoted_string_stripped(self) -> None:
        assert _record_year({"date": "'1612'"}) == 1612

    def test_four_digit_century_boundary(self) -> None:
        assert _record_year({"date": "1000"}) == 1000
        assert _record_year({"date": "1999"}) == 1999
