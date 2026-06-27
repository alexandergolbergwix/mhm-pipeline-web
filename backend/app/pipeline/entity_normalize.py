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
    "מעיר": "commentator",
    "מוזכר": "mentioned",
    "ממנו": "copied_from",
    "חותם": "signatory",
    "אליו": "addressee",
    "מיוחס לו": "attributed_author",
    "מתרגם": "translator",
    "מחבר משוער": "presumed_author",
    "פרשן": "commentator",
    "מפרש": "commentator",
    "מאייר": "illuminator",
    "עורך": "editor",
    "מלקט": "compiler",
}

# Person MARC roles that should resolve to Mazal אישיות (tag 100), not נושא/כותר.
MAZAL_PERSONALITY_PREFER_ROLE_KEYS = frozenset({
    "author",
    "contributor",
    "scribe",
    "translator",
    "editor",
    "commentator",
    "former_owner",
    "current_owner",
    "owner",
    "signatory",
    "addressee",
    "copied_from",
    "mentioned",
    "attributed_author",
    "presumed_author",
    "illuminator",
    "compiler",
})


def prefers_mazal_personality(role: str) -> bool:
    """True when a person entity should prefer Mazal tag-100 over subject/work headings."""
    return normalize_role_key(role) in MAZAL_PERSONALITY_PREFER_ROLE_KEYS


_ROLE_DISPLAY: dict[str, str] = {
    "author": "author",
    "presumed_author": "presumed author",
    "attributed_author": "attributed author",
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
    "mentioned": "mentioned",
    "copied_from": "copied from",
    "signatory": "signatory",
    "addressee": "addressee",
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
    t = normalize_entity_text(role)
    while t.startswith("(") and t.endswith(")"):
        t = t[1:-1].strip()
    return t.lower().rstrip(".")


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
