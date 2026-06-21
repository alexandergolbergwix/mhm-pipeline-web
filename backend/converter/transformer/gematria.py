"""Hebrew gematria: bidirectional conversion for integers 1–50_000.

Used by MARC date parsing and available as a full lookup table via
:func:`build_gematria_dict`.
"""

from __future__ import annotations

import re
from functools import lru_cache

MIN_VALUE = 1
MAX_VALUE = 50_000
HEBREW_YEAR_MILLENNIUM = 5_000
HEBREW_YEAR_MIN = 5_001
HEBREW_YEAR_MAX = 5_999

# Standard values (non-final forms for encoding).
_VALUE_TO_LETTER: tuple[tuple[int, str], ...] = (
    (400, "ת"), (300, "ש"), (200, "ר"), (100, "ק"),
    (90, "צ"), (80, "פ"), (70, "ע"), (60, "ס"), (50, "נ"),
    (40, "מ"), (30, "ל"), (20, "כ"), (10, "י"), (9, "ט"), (8, "ח"),
    (7, "ז"), (6, "ו"), (5, "ה"), (4, "ד"), (3, "ג"), (2, "ב"), (1, "א"),
)

_LETTER_TO_VALUE: dict[str, int] = {
    "א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9,
    "י": 10, "כ": 20, "ך": 20, "ל": 30, "מ": 40, "ם": 40, "נ": 50, "ן": 50,
    "ס": 60, "ע": 70, "פ": 80, "ף": 80, "צ": 90, "ץ": 90, "ק": 100, "ר": 200,
    "ש": 300, "ת": 400,
}

_GERESH_RE = re.compile(r'["\'״\u05F4\u05F3]')


def _validate_range(n: int) -> int:
    if not isinstance(n, int) or not MIN_VALUE <= n <= MAX_VALUE:
        raise ValueError(f"gematria value must be int in {MIN_VALUE}..{MAX_VALUE}, got {n!r}")
    return n


def _insert_gershayim(letters: str) -> str:
    """Place a gershayim mark before the last letter (Hebrew year convention)."""
    if len(letters) < 2:
        return letters
    return letters[:-1] + "\u05F4" + letters[-1]


def _encode_under_1000(n: int) -> str:
    """Encode 1..999 without a thousands component."""
    if n == 15:
        return "טו"
    if n == 16:
        return "טז"
    parts: list[str] = []
    remaining = n
    for value, letter in _VALUE_TO_LETTER:
        while remaining >= value:
            parts.append(letter)
            remaining -= value
    body = "".join(parts)
    return _insert_gershayim(body) if len(body) >= 2 else body


def value_to_letters(n: int) -> str:
    """Convert an integer ``1..50000`` to Hebrew gematria letters."""
    n = _validate_range(n)
    if n < 1000:
        return _encode_under_1000(n)
    thousands = n // 1000
    remainder = n % 1000
    prefix = _encode_under_1000(thousands) + "\u05F3"  # geresh = ×1000
    if remainder == 0:
        return prefix
    return prefix + _encode_under_1000(remainder)


def letters_to_value(text: str) -> int | None:
    """Parse Hebrew gematria letters (with optional geresh / gershayim) to int."""
    if not text or not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    if raw.isdigit():
        val = int(raw)
        return val if MIN_VALUE <= val <= MAX_VALUE else None

    # Thousands: letter(s) followed by geresh, then optional hundreds part.
    if "\u05F3" in raw or "'" in raw:
        parts = re.split(r"[\u05F3']", raw, maxsplit=1)
        if len(parts) == 2:
            hi = _sum_letters(parts[0])
            lo = _sum_letters(parts[1])
            if hi is None:
                return None
            total = hi * 1000 + (lo or 0)
            return total if MIN_VALUE <= total <= MAX_VALUE else None

    total = _sum_letters(raw)
    if total is None or total < MIN_VALUE:
        return None
    return total if total <= MAX_VALUE else None


def _sum_letters(chunk: str) -> int | None:
    cleaned = _GERESH_RE.sub("", chunk)
    if not cleaned:
        return 0
    if cleaned == "טו":
        return 15
    if cleaned == "טז":
        return 16
    total = 0
    for ch in cleaned:
        val = _LETTER_TO_VALUE.get(ch)
        if val is None:
            return None
        total += val
    return total


@lru_cache(maxsize=1)
def build_gematria_dict() -> dict[int, str]:
    """Return ``{1: 'א', 2: 'ב', …, 50000: 'נ\\u05F3'}`` — full forward table."""
    return {n: value_to_letters(n) for n in range(MIN_VALUE, MAX_VALUE + 1)}


def gematria_for(n: int) -> str:
    """Lookup gematria string for *n* (uses cached dict when warm)."""
    return build_gematria_dict()[n]


def letters_to_hebrew_year(text: str) -> int | None:
    """Parse an abbreviated Hebrew **calendar** year to full AM year.

    Cataloguers omit the leading 5 (5th millennium):

    >>> letters_to_hebrew_year('תשפ"ו')
    5786
  """
    val = letters_to_value(text)
    if val is None:
        return None
    if 1 <= val <= 999:
        return HEBREW_YEAR_MILLENNIUM + val
    if HEBREW_YEAR_MIN <= val <= HEBREW_YEAR_MAX:
        return val
    return None


def hebrew_year_to_letters(year: int) -> str | None:
    """Encode a Hebrew calendar year (5786 → ``תשפ״ו``)."""
    if not HEBREW_YEAR_MIN <= year <= HEBREW_YEAR_MAX:
        return None
    return value_to_letters(year - HEBREW_YEAR_MILLENNIUM)


def hebrew_year_to_gregorian(year: int) -> int | None:
    """Convert full Hebrew calendar year to CE (5786 → 2026).

    Uses the catalog shorthand offset ``+5000 − 3760`` (same as ``gematria + 1240``).
    """
    if not HEBREW_YEAR_MIN <= year <= HEBREW_YEAR_MAX:
        return None
    return year - 3760


def gregorian_to_hebrew_year(ce_year: int) -> int | None:
    """Convert CE year to full Hebrew calendar year (2026 → 5786)."""
    if not isinstance(ce_year, int) or not (100 < ce_year < 2100):
        return None
    hy = ce_year + 3760
    if HEBREW_YEAR_MIN <= hy <= HEBREW_YEAR_MAX:
        return hy
    return None


def letters_to_gregorian_year(text: str) -> int | None:
    """Abbreviated Hebrew year letters → CE year (``תשפ\"ו`` → 2026)."""
    hy = letters_to_hebrew_year(text)
    if hy is None:
        return None
    return hebrew_year_to_gregorian(hy)
