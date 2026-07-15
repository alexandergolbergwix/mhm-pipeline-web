"""Hebrew / NLI-style production-date parsing for MARC 260/264 $c strings."""

from __future__ import annotations

import re
from typing import Any

from .gematria import (
    letters_to_gregorian_year as _letters_to_gregorian_year,
    letters_to_hebrew_year as _letters_to_hebrew_year,
    letters_to_value as _gematria_letters_to_value,
)
from .hebrew_gregorian_calendar import enrich_dates_with_calendar_fields, normalize_marc_date_string

_ORDINAL_CENTURY: dict[str, int] = {
    "ראשונה": 1, "ראשון": 1, "א": 1, "אחת": 1, "אחד": 1,
    "שניה": 2, "שני": 2, "שתיים": 2, "שתים": 2,
    "שלישית": 3, "שלישי": 3,
    "רביעית": 4, "רביעי": 4,
    "חמישית": 5, "חמישי": 5,
    "שישית": 6, "שישי": 6,
    "שביעית": 7, "שביעי": 7,
    "שמינית": 8, "שמיני": 8,
    "תשיעית": 9, "תשיעי": 9,
    "עשירית": 10, "עשירי": 10,
}

_HEB_CENTURY_RE = re.compile(
    r"מאה\s+(?P<c1>[^\s.\-–]+)(?:\s*[-–]\s*(?P<c2>[^\s.]+))?\s*(?:לפני\s+הספירה)?\s*\.?",
    re.UNICODE,
)
_BCE_RE = re.compile(r"לפני\s+הספירה", re.UNICODE)
_HEB_YEAR_TOKEN_RE = re.compile(
    r"(?:שנת\s+)?(?P<year>[הת]?['\u05F3]?[א-ת]{2,6}(?:[\"'״\u05F4][א-ת]?)?)\s*\.?",
    re.UNICODE,
)


def hebrew_gematria(token: str) -> int | None:
    """Parse a Hebrew letter cluster (e.g. ``י\"א``, ``ט\"ז``, ``תשכ\"ט``)."""
    cleaned = re.sub(r"[.\s]", "", token)
    if not cleaned:
        return None
    if cleaned.isdigit():
        val = int(cleaned)
        return val if val > 0 else None
    bare = re.sub(r'["\'״\u05F4\u05F3]', "", cleaned)
    ordinal = _ORDINAL_CENTURY.get(bare)
    if ordinal is not None:
        return ordinal
    # Geresh/gershayim are Hebrew punctuation, not thousands markers in
    # century notation. Passing them through makes ``כ'`` parse as 20,000
    # and aborts the entire MARC record instead of allowing the normal year
    # parser to continue.
    return _gematria_letters_to_value(bare)


def hebrew_year_token_to_gregorian(token: str) -> int | None:
    """Convert abbreviated Hebrew year (``תשכ\"ט``) to CE year."""
    greg = _letters_to_gregorian_year(token)
    if greg is not None and 100 < greg < 2100:
        return greg
    return None


def century_to_year_range(century: int, *, bce: bool = False) -> tuple[int, int]:
    """Map a 1-based century number to inclusive year bounds."""
    if century < 1 or century > 99:
        raise ValueError(f"century out of range: {century}")
    if bce:
        # Nth century BCE: 200–101 BCE for N=2 (astronomical -199..-100).
        start_bce = century * 100
        end_bce = (century - 1) * 100 + 1
        return -(start_bce - 1), -(end_bce - 1)
    return (century - 1) * 100 + 1, century * 100


def parse_hebrew_century(date_str: str) -> dict[str, Any] | None:
    """Parse ``מאה …`` century strings, including BCE and ranges."""
    text = normalize_marc_date_string(date_str)
    m = _HEB_CENTURY_RE.search(text)
    if not m:
        return None
    bce = _BCE_RE.search(text) is not None
    c1 = hebrew_gematria(m.group("c1"))
    if c1 is None:
        return None
    c2_raw = m.group("c2")
    c2 = hebrew_gematria(c2_raw) if c2_raw else None
    # A Hebrew year (e.g. תרפ"ד) can follow ``מאה`` in catalogue prose.
    # It is not a century token; leave it to the year parser rather than
    # raising from century_to_year_range.
    if c1 > 99 or (c2 is not None and c2 > 99):
        return None
    start_century = min(c1, c2) if c2 else c1
    end_century = max(c1, c2) if c2 else c1
    year_start, _ = century_to_year_range(start_century, bce=bce)
    _, year_end = century_to_year_range(end_century, bce=bce)
    result: dict[str, Any] = {
        "century": start_century if start_century == end_century else None,
        "century_start": start_century,
        "century_end": end_century,
        "year_start": year_start,
        "year_end": year_end,
        "year": year_start,
        "date_format": "HebrewCentury",
        "certainty": "Possible",
    }
    if bce:
        result["bce"] = True
        result["era"] = "bce"
    if start_century != end_century:
        result["century_range"] = f"{start_century}-{end_century}"
    return result


def parse_standalone_hebrew_year(date_str: str) -> dict[str, Any] | None:
    """Parse bare Hebrew year tokens like ``תשכ\"ט.``"""
    text = normalize_marc_date_string(date_str)
    m = _HEB_YEAR_TOKEN_RE.fullmatch(text) or _HEB_YEAR_TOKEN_RE.search(text)
    if not m:
        return None
    token = m.group("year")
    greg = hebrew_year_token_to_gregorian(token)
    if greg is None or not (100 < greg < 2100):
        return None
    return {
        "hebrew_date": token,
        "hebrew_year": _letters_to_hebrew_year(token),
        "year": greg,
        "date_format": "HebrewYear",
        "certainty": "Probable",
    }


def enrich_parsed_hebrew_dates(result: dict[str, Any], date_str: str) -> dict[str, Any]:
    """Fill ``year`` / century bounds when existing HebrewYear match lacked CE year."""
    if result.get("year") is not None:
        return result
    hebrew_date = result.get("hebrew_date")
    if isinstance(hebrew_date, str):
        greg = hebrew_year_token_to_gregorian(hebrew_date)
        if greg is not None:
            result["year"] = greg
    if result.get("year") is None:
        century = parse_hebrew_century(date_str)
        if century:
            result.update(century)
    if result.get("year") is None:
        standalone = parse_standalone_hebrew_year(date_str)
        if standalone:
            result.update(standalone)
    return enrich_dates_with_calendar_fields(result)
