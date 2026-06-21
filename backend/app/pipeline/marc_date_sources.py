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


def manuscript_production_year(record: dict[str, Any] | None) -> int | None:
    """Return the manuscript production year from canonical MARC sources only.

    Priority: 260/264 $c (``record["dates"]``) → colophon year (590/500 $a).
    Never reads 008, 541 $d, 583 $c, or biographical $d subfields.
    """
    if not record:
        return None

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

    return _parse_year_scalar(record.get("colophon_year"))
