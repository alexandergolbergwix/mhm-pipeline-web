"""Tests for Hebrew gematria conversion (1–50_000)."""

from __future__ import annotations

import pytest

from converter.transformer.gematria import (
    MAX_VALUE,
    MIN_VALUE,
    build_gematria_dict,
    letters_to_value,
    value_to_letters,
)

# 20 diverse integers across the supported range (hand-picked, not random).
_SAMPLE_NUMBERS: tuple[int, ...] = (
    1,
    7,
    11,
    15,
    16,
    18,
    72,
    100,
    400,
    729,
    999,
    1000,
    1001,
    1500,
    1969,
    329,
    5729,
    12_345,
    49_999,
    50_000,
)


class TestValueToLetters:
    @pytest.mark.parametrize("n", _SAMPLE_NUMBERS)
    def test_roundtrip(self, n: int) -> None:
        letters = value_to_letters(n)
        assert letters_to_value(letters) == n, f"{n} → {letters!r} → {letters_to_value(letters)!r}"

    def test_known_year_tashkaz(self) -> None:
        # 729 → תשכ״ט (1969 CE in Hebrew-year catalog shorthand)
        letters = value_to_letters(729)
        assert letters_to_value(letters) == 729
        cleaned = letters.replace("\u05F4", "").replace("\u05F3", "")
        assert "ת" in cleaned and "ש" in cleaned and "כ" in cleaned and "ט" in cleaned

    def test_fifteen_sixteen_special_forms(self) -> None:
        assert value_to_letters(15) == "טו"
        assert value_to_letters(16) == "טז"

    def test_fifty_thousand(self) -> None:
        letters = value_to_letters(50_000)
        assert letters_to_value(letters) == 50_000


class TestBuildGematriaDict:
    def test_dict_size_and_bounds(self) -> None:
        d = build_gematria_dict()
        assert len(d) == MAX_VALUE - MIN_VALUE + 1
        assert d[1] == "א"
        assert d[MIN_VALUE] == value_to_letters(MIN_VALUE)
        assert d[MAX_VALUE] == value_to_letters(MAX_VALUE)

    @pytest.mark.parametrize("n", _SAMPLE_NUMBERS)
    def test_dict_matches_function(self, n: int) -> None:
        d = build_gematria_dict()
        assert d[n] == value_to_letters(n)


class TestHebrewCalendarYear:
    def test_tashpa_v_full_hebrew_year(self) -> None:
        from converter.transformer.gematria import (
            hebrew_year_to_letters,
            letters_to_hebrew_year,
            letters_to_gregorian_year,
        )

        assert letters_to_hebrew_year('תשפ"ו') == 5786
        assert hebrew_year_to_letters(5786) == 'תשפ״ו'
        assert letters_to_gregorian_year('תשפ"ו') == 2026

    def test_tashkaz_still_parses(self) -> None:
        from converter.transformer.gematria import letters_to_hebrew_year

        assert letters_to_hebrew_year('תשכ"ט') == 5729


class TestLettersToValueEdgeCases:
    def test_doubled_ascii_quote_from_tsv(self) -> None:
        assert letters_to_value('תשכ""ט') == 729

    def test_empty_and_invalid(self) -> None:
        assert letters_to_value("") is None
        assert letters_to_value("xyz") is None

    def test_reject_out_of_range_encode(self) -> None:
        with pytest.raises(ValueError):
            value_to_letters(0)
        with pytest.raises(ValueError):
            value_to_letters(50_001)
