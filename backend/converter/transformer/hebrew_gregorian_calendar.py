"""Hebrew ↔ Gregorian calendar normalisation for parsed MARC production dates.

Downstream code (RDF, authority guards, maps) expects ``dates["year"]`` in
Gregorian CE. When MARC 260/264 $c carries a Hebrew year (``תשכ\"ט``), this
module fills both ``hebrew_year`` (5786-style AM) and ``year`` (2026 CE).
"""

from __future__ import annotations

from typing import Any

from .gematria import (
    gregorian_to_hebrew_year,
    hebrew_year_to_gregorian,
    letters_to_gregorian_year,
    letters_to_hebrew_year,
)


def normalize_marc_date_string(date_str: str) -> str:
    """Collapse TSV-style doubled quotes and trailing punctuation."""
    text = (date_str or "").strip()
    text = text.replace('""', '"')
    return text.strip(" .;,")


def _is_gregorian_ce(year: int | None) -> bool:
    return isinstance(year, int) and 100 < year < 2100


def _hebrew_token_from_dates(dates: dict[str, Any]) -> str | None:
    for key in ("hebrew_date", "hebrew_year_letters", "original_string"):
        raw = dates.get(key)
        if isinstance(raw, str) and any("\u05d0" <= c <= "\u05ea" for c in raw):
            return normalize_marc_date_string(raw)
    return None


def enrich_dates_with_calendar_fields(dates: dict[str, Any]) -> dict[str, Any]:
    """Ensure ``dates`` carries paired ``year`` (CE) and ``hebrew_year`` (AM)."""
    if not isinstance(dates, dict):
        return dates

    if dates.get("bce"):
        return dates

    hebrew_year = dates.get("hebrew_year")
    if not isinstance(hebrew_year, int):
        hebrew_year = None

    token = _hebrew_token_from_dates(dates)
    if hebrew_year is None and token:
        hebrew_year = letters_to_hebrew_year(token)
        if hebrew_year is not None:
            dates["hebrew_year"] = hebrew_year
            if token and not dates.get("hebrew_date"):
                dates["hebrew_date"] = token

    year = dates.get("year")
    if not _is_gregorian_ce(year) and hebrew_year is not None:
        greg = hebrew_year_to_gregorian(hebrew_year)
        if _is_gregorian_ce(greg):
            dates["year"] = greg

    year = dates.get("year")
    if _is_gregorian_ce(year) and dates.get("hebrew_year") is None:
        hy = gregorian_to_hebrew_year(year)
        if hy is not None:
            dates["hebrew_year"] = hy

    if token and dates.get("hebrew_date") is None:
        dates["hebrew_date"] = token

    return dates


def hebrew_letters_to_gregorian_year(text: str) -> int | None:
    """Public alias: abbreviated Hebrew year text → CE."""
    return letters_to_gregorian_year(text)


def gregorian_year_to_hebrew_year(ce_year: int) -> int | None:
    """Public alias: CE → full Hebrew AM year."""
    return gregorian_to_hebrew_year(ce_year)
