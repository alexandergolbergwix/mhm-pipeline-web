"""Shared normalisation for authority entity text and MARC roles.

Curators always see English role labels (e.g. ``former owner``, not
``בעלים קודמים``). Entity text is stripped of CSV/MARC quote artefacts
so ``"Allony, Nehemia"`` and ``Allony, Nehemia"`` dedupe to one row.
"""
from __future__ import annotations

import re
import unicodedata

from converter.config.vocabularies import ROLE_MAPPINGS

# Hebrew / variant strings not yet in the shared vocab file.
_EXTRA_ROLE_MAPPINGS: dict[str, str] = {
    "בעלים קודמים": "former_owner",
    "בעלים נוכחיים": "current_owner",
    "בעלים": "owner",
    "בעל": "owner",
    "מחבר": "author",
    "סופר": "scribe",
    "מעתיק": "scribe",
}

_ROLE_DISPLAY: dict[str, str] = {
    "author": "author",
    "scribe": "scribe",
    "illuminator": "illuminator",
    "commentator": "commentator",
    "translator": "translator",
    "former_owner": "former owner",
    "current_owner": "current owner",
    "owner": "owner",
    "editor": "editor",
    "compiler": "compiler",
    "contributor": "contributor",
    "subject": "subject",
    "place": "place",
    "production_place": "production place",
    "institution": "institution",
    "contained_work": "contained work",
}


def normalize_entity_text(text: str) -> str:
    """Strip wrapping/stray quotes and collapse whitespace; preserve casing."""
    t = (text or "").strip()
    while t and t[0] in "\"'":
        t = t[1:].strip()
    while t and t[-1] in "\"'":
        t = t[:-1].strip()
    return re.sub(r"\s+", " ", t)


def normalize_entity_key(text: str) -> str:
    """Lowercase dedup key: strip niqqud, punctuation, and outer quotes."""
    t = normalize_entity_text(text).lower()
    t = "".join(c for c in t if not (0x0591 <= ord(c) <= 0x05C7))
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s\u0590-\u05FF]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _role_raw(role: str) -> str:
    return normalize_entity_text(role).lower().rstrip(".")


def normalize_role(role: str) -> str:
    """Map Hebrew/MARC role strings to canonical English display labels."""
    raw = _role_raw(role)
    if not raw:
        return ""
    internal = ROLE_MAPPINGS.get(raw) or _EXTRA_ROLE_MAPPINGS.get(raw, raw)
    internal = internal.replace(" ", "_")
    if internal in _ROLE_DISPLAY:
        return _ROLE_DISPLAY[internal]
    return internal.replace("_", " ")


def normalize_role_key(role: str) -> str:
    """Stable lowercase key for dedup/upsert (always English snake_case)."""
    display = normalize_role(role)
    if not display:
        return ""
    return display.replace(" ", "_")
