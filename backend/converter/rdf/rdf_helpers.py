"""Shared RDF graph helpers — label hygiene, role normalisation, geo sanity."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..config.vocabularies import ROLE_MAPPINGS

_INSTITUTIONAL_KEYWORDS: frozenset[str] = frozenset({
    "library",
    "collection",
    "archive",
    "archives",
    "museum",
    "university",
    "institute",
    "foundation",
    "trust",
    "seminary",
    "academy",
    "society",
    "בית",
    "ספרייה",
    "אוסף",
    "מכון",
    "אוניברסיטה",
    "bodleian",
    "palatina",
})


_ISBD_ADJACENT_QUOTED = re.compile(
    r'"([^"]+?)"\s+"([^"]+?)"',
    flags=re.UNICODE,
)


def _normalize_marc_isbd_quotes(text: str) -> str:
    """Collapse MARC ISBD ``"title :" "subtitle"`` quote nesting."""
    out = text.replace('""', '"')

    def _merge_quoted_pair(match: re.Match[str]) -> str:
        left = match.group(1).strip()
        right = match.group(2).strip()
        if ":" in left:
            return f"{left} {right}"
        return match.group(0)

    prev = None
    while prev != out:
        prev = out
        out = _ISBD_ADJACENT_QUOTED.sub(_merge_quoted_pair, out, count=1)

    out = re.sub(r'(?<=\s)"([^"]+?)"(?=\s|$)', r"\1", out)
    out = re.sub(r'^"([^"]+?)"', r"\1", out)
    out = re.sub(r'"([^"]+?)"$', r"\1", out)
    out = re.sub(r':\s*"+\s*', ": ", out)
    out = out.replace('""', '"')
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


_DESCRIPTIVE_CONTENT_PREFIXES: tuple[str, ...] = (
    "גם ",
    "כולל גם ",
    "also ",
    "נוסח ",
    "באותיות ",
)


def is_descriptive_content_title(title: str) -> bool:
    """True when a 505/500 fragment is a note, not a named work title."""
    cleaned = clean_marc_label(title)
    if not cleaned:
        return True
    lower = cleaned.casefold()
    if any(lower.startswith(prefix) for prefix in _DESCRIPTIVE_CONTENT_PREFIXES):
        return True
    if "באותיות לטיניות" in lower or "latin letters" in lower:
        return True
    if lower.startswith("כולל ") and "נוסח" in lower:
        return True
    return False


def clean_marc_label(text: str) -> str:
    """Strip MARC ISBD quote artifacts and surrounding whitespace."""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    cleaned = _normalize_marc_isbd_quotes(text.strip())
    cleaned = cleaned.strip("\"'").strip()
    while cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) > 1:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def normalize_participation_role(role: str | None) -> URIRef | None:
    """Map pipeline role tokens to hm:ParticipationRole individuals."""
    from ..config.namespaces import HM

    token = normalize_role(role)
    mapping = {
        "author": HM.Author_role,
        "scribe": HM.Scribe_role,
        "copyist": HM.Scribe_role,
        "translator": HM.Translator_role,
        "commentator": HM.Commentator_role,
        "owner": HM.Owner_role,
    }
    return mapping.get(token)


def normalize_role(role: str | None) -> str:
    """Map MARC relator strings to canonical pipeline role tokens."""
    if not role:
        return "contributor"
    raw = clean_marc_label(str(role)).lower().strip().rstrip(".")
    if not raw:
        return "contributor"
    mapped = ROLE_MAPPINGS.get(raw)
    if mapped:
        return mapped
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return slug or "contributor"


def is_institutional_name(name: str) -> bool:
    """Heuristic: corporate holder / library names should be E74_Group."""
    lowered = clean_marc_label(name).lower()
    if not lowered:
        return False
    return any(kw in lowered for kw in _INSTITUTIONAL_KEYWORDS)


def infer_person_type(person_data: dict[str, Any]) -> str:
    """Return ``organization`` when the record should emit E74_Group."""
    explicit = str(person_data.get("type") or "").lower()
    if explicit in {"organization", "org", "corporate", "institution"}:
        return "organization"
    marc_field = str(person_data.get("field") or "")
    if marc_field in {"110", "710", "610", "810"}:
        return "organization"
    name = str(person_data.get("name") or "")
    if is_institutional_name(name):
        return "organization"
    return "person"


def is_plausible_coords(lat: float | int | str | None, lon: float | int | str | None) -> bool:
    """Reject swapped / garbage geocodes before writing WGS84 triples."""
    try:
        lat_f = float(lat)  # type: ignore[arg-type]
        lon_f = float(lon)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return False
    if abs(lat_f) < 0.01 and abs(lon_f) < 0.01:
        return False
    return True


def names_overlap(a: str, b: str) -> bool:
    """Case-insensitive bidirectional substring match for authority merge."""
    left = clean_marc_label(a).casefold()
    right = clean_marc_label(b).casefold()
    if not left or not right:
        return False
    return left == right or left in right or right in left


def person_dict_key(person: dict[str, Any]) -> str:
    return clean_marc_label(str(person.get("name") or "")).casefold()


def ensure_person_in_list(
    people: list[dict[str, Any]],
    name: str,
    *,
    role: str = "contributor",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find or append a person dict; return the mutable entry."""
    key = clean_marc_label(name).casefold()
    for person in people:
        if person_dict_key(person) == key:
            return person
    entry: dict[str, Any] = {
        "name": clean_marc_label(name),
        "role": normalize_role(role),
    }
    if extra:
        entry.update(extra)
    people.append(entry)
    return people[-1]
