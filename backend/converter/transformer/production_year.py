"""Canonical MARC date sources for the web pipeline.

Only the fields below carry extractable dates. MARC 008 bytes 00–14 are
catalog-entry metadata and are never used for manuscript or entity dating.

| MARC field   | Subfield | Stored as              | Role                          |
|--------------|----------|------------------------|-------------------------------|
| 260 / 264    | $c       | record["dates"]        | Production / writing (primary)|
| 590 / 500    | $a       | record["colophon_year"] (CE), ``colophon_hebrew_year`` (AM) | Colophon |
| 541          | $d       | provenance_events[].year | Acquisition (custody)       |
| 583          | $c       | provenance_events[].year | Conservation / exhibition   |
| 100/700/600  | $d       | authors/contributors/  | Biographical (person entities)|
|              |          | subjects[].dates       |                               |
"""

from __future__ import annotations

import re
from typing import Any

# Production — 260/264 $c
PRODUCTION_DATE_TAGS: tuple[str, ...] = ("260", "264")
PRODUCTION_DATE_SUBFIELD = "c"

# Colophon — 590 $a + keyword-detected 500 $a → colophon_text → colophon_year
COLOPHON_NOTE_TAGS: tuple[str, ...] = ("590", "500")

# Custody events
ACQUISITION_TAG = "541"
ACQUISITION_DATE_SUBFIELD = "d"
ACTION_TAG = "583"
ACTION_DATE_SUBFIELD = "c"

# Biographical — $d on name headings
BIOGRAPHICAL_NAME_TAGS: tuple[str, ...] = (
    "100", "700", "800",  # authors / contributors / series persons
    "600", "611",        # subject / meeting headings
)


def _parse_year_scalar(value: Any) -> int | None:
    if isinstance(value, int):
        if value < 0:
            return value
        if 100 < value < 2100:
            return value
    if isinstance(value, str):
        text = value.strip().strip("\"' ")
        if not text:
            return None
        m = re.search(r"\b(\d{3,4})\b", text)
        if m:
            try:
                yr = int(m.group(1))
                if 100 < yr < 2100:
                    return yr
            except ValueError:
                return None
    return None


_CENTURY_DATE_FORMATS = frozenset({
    "HebrewCentury",
    "Century",
    "HebrewGematriaCentury",
})


def _catalog_production_year(record: dict[str, Any]) -> int | None:
    """Production year from 260/264 $c only — never colophon."""
    dates = record.get("dates")
    if isinstance(dates, dict):
        year = _parse_year_scalar(dates.get("year"))
        if year is not None:
            return year
        year = _parse_year_scalar(dates.get("year_start"))
        if year is not None:
            return year
        year = _parse_year_scalar(dates.get("original_string"))
        if year is not None:
            return year
    elif isinstance(dates, str):
        year = _parse_year_scalar(dates)
        if year is not None:
            return year
    return None


def _year_within_catalog_range(year: int, dates: dict[str, Any]) -> bool:
    start = _parse_year_scalar(dates.get("year_start"))
    end = _parse_year_scalar(dates.get("year_end"))
    if start is not None and end is not None:
        return start <= year <= end
    catalog = _parse_year_scalar(dates.get("year"))
    if catalog is not None:
        return abs(catalog - year) <= 100
    return True


def _catalog_date_is_imprecise(dates: dict[str, Any]) -> bool:
    fmt = str(dates.get("date_format") or "")
    if fmt in _CENTURY_DATE_FORMATS:
        return True
    start = _parse_year_scalar(dates.get("year_start"))
    end = _parse_year_scalar(dates.get("year_end"))
    if start is not None and end is not None and (end - start) >= 99:
        return True
    return False


def manuscript_production_year(record: dict[str, Any] | None) -> int | None:
    """Return the manuscript production year from canonical MARC sources only.

  Priority:
    1. 260/264 $c when it carries an exact production year.
    2. Colophon year (590/500 $a) when the catalog date is century-level or
       range-only but the colophon year falls inside that range — e.g. catalog
       ``מאה י"ט`` (1801–1900) + colophon ``תרל"א`` (1871).
    3. Colophon year when no catalog production year is available.

    Never reads 008, 541 $d, 583 $c, or biographical $d subfields.
    """
    if not record:
        return None

    catalog_year = _catalog_production_year(record)
    colophon_year = _parse_year_scalar(record.get("colophon_year"))
    dates = record.get("dates")

    if (
        colophon_year is not None
        and isinstance(dates, dict)
        and _catalog_date_is_imprecise(dates)
        and _year_within_catalog_range(colophon_year, dates)
    ):
        return colophon_year

    if catalog_year is not None:
        return catalog_year

    return colophon_year


def manuscript_year_provenance(record: dict[str, Any] | None) -> dict[str, Any]:
    """Return ms year plus catalog/colophon breakdown for authority payloads."""
    if not record:
        return {
            "ms_year": None,
            "catalog_year": None,
            "colophon_year": None,
            "colophon_hebrew_year": None,
            "ms_year_source": None,
        }
    catalog_year = _catalog_production_year(record)
    colophon_year = _parse_year_scalar(record.get("colophon_year"))
    colophon_hebrew = record.get("colophon_hebrew_year")
    if isinstance(colophon_hebrew, str):
        colophon_hebrew = _parse_year_scalar(colophon_hebrew)
    ms_year = manuscript_production_year(record)
    source: str | None = None
    if ms_year is not None:
        if colophon_year is not None and ms_year == colophon_year and ms_year != catalog_year:
            source = "colophon"
        else:
            source = "catalog"
    return {
        "ms_year": ms_year,
        "catalog_year": catalog_year,
        "colophon_year": colophon_year,
        "colophon_hebrew_year": colophon_hebrew if isinstance(colophon_hebrew, int) else None,
        "ms_year_source": source,
    }
