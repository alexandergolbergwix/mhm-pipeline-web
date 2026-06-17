"""Infer entity kind (person / corporate / meeting) from a heading + MARC tag.

NLI ownership records often pack ``710$a = Institution|Person`` in one
corporate added entry.  Tag-only routing misclassifies the person half as
``corporate``, which skips VIAF/Wikidata.  This module classifies each
segment by name shape, reusing desktop ``is_institutional_name``.
"""
from __future__ import annotations

import re

from app.pipeline.entity_normalize import normalize_entity_text

_DATE_SUFFIX_RE = re.compile(r",\s*(-?\d{2,4}(?:[-–]\d{0,4})?)\s*$")

# Beyond desktop keywords — named collections on 710.
_EXTRA_INSTITUTIONAL_KEYWORDS: tuple[str, ...] = (
    "collection",
    "national library",
    "british library",
    "russian state library",
    "jewish theological seminary",
    "ben zvi institute",
)

_HEBREW_PERSON_MARKERS: tuple[str, ...] = (
    " בן ",
    " בת ",
    "משפחה",
    "משפחת",
)


def _is_institutional(name: str) -> bool:
    if not name:
        return False
    lowered = name.lower()
    if any(kw in lowered for kw in _EXTRA_INSTITUTIONAL_KEYWORDS):
        return True
    try:
        from converter.wikidata.item_builder import is_institutional_name  # noqa: PLC0415

        return bool(is_institutional_name(name))
    except Exception:  # noqa: BLE001
        return False


def looks_like_inverted_person(name: str) -> bool:
    """True for MARC ``Surname, Given`` personal-name cataloging form."""
    n = normalize_entity_text(name)
    if not n or _is_institutional(n):
        return False
    base = _DATE_SUFFIX_RE.sub("", n).strip()
    parts = [p.strip() for p in base.split(",")]
    if len(parts) != 2:
        return False
    surname, given = parts
    if not surname or not given:
        return False
    if len(given.split()) > 5:
        return False
    if _is_institutional(given) or _is_institutional(surname):
        return False
    if re.search(r"[A-Za-z]", surname) and re.search(r"[A-Za-z]", given):
        return True
    return False


def _hebrew_person_hint(name: str) -> bool:
    n = normalize_entity_text(name)
    if not n:
        return False
    return any(m in n for m in _HEBREW_PERSON_MARKERS)


def infer_entity_kind(name: str, field_tag: str) -> str:
    """Return ``person``, ``corporate``, or ``meeting`` for authority routing."""
    tag = (field_tag or "").strip()
    clean = normalize_entity_text(name)
    if not clean:
        return "person"

    if tag == "711":
        return "meeting"
    if tag == "111":
        return "meeting"

    if tag in ("700", "800", "600"):
        return "person"
    if tag == "100":
        return "person"

    if _is_institutional(clean):
        return "corporate"
    if looks_like_inverted_person(clean):
        return "person"
    if _hebrew_person_hint(clean):
        return "person"

    if tag in ("710", "110", "810", "610"):
        return "corporate"

    return "person"
