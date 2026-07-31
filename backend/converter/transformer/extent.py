"""MARC 300$a physical-extent parsing for P1104 (Rule W-140).

The historical ingest regex took the first number *adjacent to a folio unit*,
which silently mis-stated every multi-sequence extent (``111, [2] דף`` became
2) and dropped every page-unit, Hebrew-numeral and volume-collated extent.

This module parses the extent honestly and **fails closed**: when the unit is
not one we can name, or the string is a folio *reference* rather than a count,
it returns ``None`` so no claim is emitted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNIT_LEAF = "leaf"
UNIT_PAGE = "page"

# Unit vocabulary. Longest forms first so "דפים" wins over "דף".
_LEAF_WORDS = ("דפים", "דף", "עלים", "עלה", "leaves", "leaf", "folios", "folio", "ff.", "ff", "f.")
_PAGE_WORDS = ("עמודים", "עמודות", "עמוד", "עמ'", "עמ׳", "pages", "page", "pp.", "pp", "p.")

# Columns/lines are a real extent but not leaves or pages — naming them
# "page" would assert something false, so they stay unparsed.
_UNSUPPORTED_UNITS = ("עמודות", "טורים", "שורות", "columns", "lines")

_GEMATRIA = {
    "א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9,
    "י": 10, "כ": 20, "ך": 20, "ל": 30, "מ": 40, "ם": 40, "נ": 50, "ן": 50,
    "ס": 60, "ע": 70, "פ": 80, "ף": 80, "צ": 90, "ץ": 90,
    "ק": 100, "ר": 200, "ש": 300, "ת": 400,
}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# A Hebrew token that is a unit word must never be read as a numeral —
# "דף" would otherwise score 84.
_NEVER_NUMERALS = frozenset(_LEAF_WORDS + _PAGE_WORDS + tuple(_UNSUPPORTED_UNITS) + (
    "כרך", "כרכים", "חסר", "הסוף", "ספירה", "מקורית", "בערך", "ועוד", "לוח", "לוחות",
))

# "דף 2א-2ב" cites a folio range; it is not an extent count.
_FOLIO_REFERENCE = re.compile(
    r"(?:דף|דפים|folios?|ff?\.)\s*\d+\s*[אב]?\s*[-–—]",
    re.IGNORECASE,
)
_VOLUME_COLLATION = re.compile(r"(\d+)\s*כרכים?\s*\((?P<inner>[^)]*)\)")
_PARENTHETICAL = re.compile(r"\([^)]*\)")


@dataclass(frozen=True)
class ExtentParse:
    """A parsed extent: *count* of *unit*, summed from *parts*."""

    count: int
    unit: str
    parts: tuple[int, ...]
    volumes: int = 0


def _gematria_value(token: str) -> int | None:
    """Value of a Hebrew-numeral token, or None if it is not one."""
    cleaned = token.strip().strip("\"'״׳.,;:")
    if not cleaned or cleaned in _NEVER_NUMERALS or len(cleaned) > 5:
        return None
    if not all(ch in _GEMATRIA for ch in cleaned):
        return None
    return sum(_GEMATRIA[ch] for ch in cleaned)


def _roman_value(token: str) -> int | None:
    cleaned = token.strip().strip(".,;:").upper()
    if not cleaned or not all(ch in _ROMAN for ch in cleaned):
        return None
    total = 0
    previous = 0
    for ch in reversed(cleaned):
        value = _ROMAN[ch]
        total += -value if value < previous else value
        previous = max(previous, value)
    return total or None


def _unit_for(segment: str) -> tuple[str, int] | None:
    """The extent unit and the offset where its word starts."""
    lowered = segment.lower()
    for word in _UNSUPPORTED_UNITS:
        if word in lowered:
            return None
    best: tuple[str, int] | None = None
    for unit, words in ((UNIT_LEAF, _LEAF_WORDS), (UNIT_PAGE, _PAGE_WORDS)):
        for word in words:
            index = lowered.find(word.lower())
            if index >= 0 and (best is None or index < best[1]):
                best = (unit, index)
    return best


def parse_extent(raw: str | int | None) -> ExtentParse | None:
    """Parse a MARC 300$a extent string, or return None when unsure."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return ExtentParse(count=raw, unit=UNIT_LEAF, parts=(raw,)) if raw > 0 else None

    text = str(raw).strip().strip('"').replace("[", " ").replace("]", " ")
    if not text or _FOLIO_REFERENCE.search(text):
        return None

    volumes = 0
    collation = _VOLUME_COLLATION.search(text)
    if collation:
        # "2 כרכים (160, 210 דף)" — the leading number counts volumes, and the
        # per-volume sequences inside the parentheses are the real extent.
        volumes = int(collation.group(1))
        segment = collation.group("inner")
    else:
        segment = _PARENTHETICAL.sub(" ", text)

    unit_at = _unit_for(segment)
    if unit_at is None:
        return None
    unit, offset = unit_at

    parts: list[int] = []
    for token in re.split(r"[,;/]| ו-", segment[:offset]):
        token = token.strip()
        if not token:
            continue
        digits = re.fullmatch(r"\D*(\d+)\D*", token)
        if digits:
            parts.append(int(digits.group(1)))
            continue
        value = _gematria_value(token) or _roman_value(token)
        if value:
            parts.append(value)

    total = sum(parts)
    if total <= 0:
        return None
    return ExtentParse(count=total, unit=unit, parts=tuple(parts), volumes=volumes)
