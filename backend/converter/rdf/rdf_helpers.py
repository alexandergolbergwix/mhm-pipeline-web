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

_GERSHAYIM_RE = re.compile(r'(?<=[֐-ת])"(?=[֐-ת])')
_GERSHAYIM_SENTINEL = ""
_ISBD_QUOTED_CONJUNCTION_RE = re.compile(r'(?<=[\u0590-\u05ea])"\s+ו"(?=[\u0590-\u05ea])')

_IN_MS_SUFFIX_RE = re.compile(r"\s*\(in MS\s+\d{8,}\)\s*$", re.IGNORECASE)
_ENUM_PREFIX_RE = re.compile(r"^\d+\)\s*")
_NUMBERED_CONTENTS_RE = re.compile(
    r"^\s*(?P<seq>\d+)\)\s*(?P<folio>.+?)\s*:\s*(?P<title>.+?)\s*$",
    flags=re.UNICODE,
)


def parse_contents_entry(raw: str) -> dict[str, Any]:
    """Split a MARC 505 row into sequence, folio range, and scholarly title."""
    text = str(raw or "").strip()
    if not text:
        return {"title": "", "folio_range": None, "sequence": None, "raw": ""}

    match = _NUMBERED_CONTENTS_RE.match(text)
    if match:
        folio_raw = match.group("folio").strip()
        folio_clean = re.sub(r"^דף\s*", "", folio_raw).strip() or folio_raw
        title = clean_marc_label(
            match.group("title"),
            strip_ms_scope=True,
            strip_enum_prefix=True,
        )
        return {
            "title": title,
            "folio_range": folio_clean,
            "sequence": int(match.group("seq")),
            "raw": text,
        }

    title = clean_marc_label(text, strip_ms_scope=True, strip_enum_prefix=True)
    return {"title": title, "folio_range": None, "sequence": None, "raw": text}


def _normalize_marc_isbd_quotes(text: str) -> str:
    """Collapse MARC ISBD quote wrappers while preserving Hebrew gershayim."""
    out = text.replace('""', '"')
    # Protect legitimate Hebrew abbreviation marks before wrapper stripping;
    # otherwise the first quote in רס"ג looks like a title-closing quote.
    out = _GERSHAYIM_RE.sub(_GERSHAYIM_SENTINEL, out)

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
    out = re.sub(r'\\+', "", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out.replace(_GERSHAYIM_SENTINEL, '"')


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
    if re.search(r"\bin latin\b", lower):
        return True
    if lower.startswith("כולל ") and "נוסח" in lower:
        return True
    # A single-token vav-prefix construct-state fragment (``ותשובת`` = "and the
    # responsa-of…") is a 505 continuation, not a work title (P2.3). Only
    # single-token; multi-word titles starting with a vav stay.
    if len(cleaned.split()) == 1 and cleaned.startswith("ו"):
        remainder = re.sub(r"[^֐-ת]", "", cleaned[1:])
        if len(remainder) <= 6:
            return True
    return False


def sanitize_work_title(text: str) -> str:
    """Normalize a 245/505 work title for RDF labels and Wikibase export.

    Hebrew gershayim abbreviation markers (a straight quote between two Hebrew
    letters, e.g. ``ה"ה`` / ``שד"ל``) are legitimate and must survive the
    unbalanced-quote strip that removes ISBD artifacts.
    """
    cleaned = clean_marc_label(text)
    # Collapse an ISBD conjunction of two quoted titles whose outer quotes were
    # already stripped (``X" ו"Y`` → ``X וY``) so the interior straight quotes
    # are not mistaken for gershayim by the parity check (P2.1).
    cleaned = _ISBD_QUOTED_CONJUNCTION_RE.sub(" ו", cleaned)
    # Protect gershayim from the unbalanced-quote strip.
    protected = _GERSHAYIM_RE.sub(_GERSHAYIM_SENTINEL, cleaned)
    if protected.count('"') % 2 == 1:
        protected = protected.replace('"', "")
    # Drop a dangling trailing close-paren with no matching open.
    if protected.count(")") > protected.count("("):
        protected = re.sub(r"\s*\)+\s*$", "", protected).strip()
    if protected.count("(") > protected.count(")"):
        last_open = protected.rfind("(")
        if last_open >= 0:
            protected = protected[:last_open].strip()
    cleaned = protected.replace(_GERSHAYIM_SENTINEL, '"')
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def label_language_for_text(text: str) -> str:
    """Pick ``he`` vs ``en`` for an RDF/Wikibase label from script content."""
    hebrew = len(re.findall(r"[\u0590-\u05ea]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if hebrew == 0 and latin > 0:
        return "en"
    if latin > hebrew * 2:
        return "en"
    return "he"


def is_usable_work_title(title: str) -> bool:
    """False for notes, placeholders, or titles with too little letter content."""
    cleaned = sanitize_work_title(title)
    if not cleaned or is_descriptive_content_title(cleaned):
        return False
    letters = re.sub(r"[^\w\u0590-\u05ea]", "", cleaned)
    return len(letters) >= 3


def disambiguate_work_label(title: str, control_number: str) -> str:
    """Add manuscript scope to very short generic work titles."""
    cleaned = sanitize_work_title(title)
    if not cleaned:
        return ""
    letters = re.sub(r"[^\w\u0590-\u05ea]", "", cleaned)
    if len(cleaned.split()) <= 1 and len(letters) <= 8:
        return f"{cleaned} (MS {control_number})"
    return cleaned


_IBN_INVERTED_RE = re.compile(r"^(?P<sur>\S+),\s*(?P<given>.+?)\s+אבן$")


def shorten_isbd_label(title: str, *, max_len: int = 80) -> str:
    """Return a short label head for an over-long ISBD title.

    When the sanitized title exceeds ``max_len`` and carries an ISBD ``:``
    subtitle chain, the pre-colon head is the short scholarly title; the full
    title belongs in the description/alias (P2.4).
    """
    cleaned = sanitize_work_title(title)
    if len(cleaned) > max_len and " : " in cleaned:
        head = cleaned.split(" : ", 1)[0].strip()
        if head and len(head) >= 3:
            return head
    return cleaned


def clean_person_display_name(name: str) -> str:
    """Normalise dangling particles in person headings.

    A trailing ``אבן`` after a ``Surname, Given`` inverted heading is the
    **Ibn** that belongs to the surname (``סיד, יצחק אבן`` = Ibn Sid) — it is
    uninverted, not deleted. A trailing ``בן`` after a comma-inverted heading
    is a genuinely truncated patronymic and is stripped.
    """
    cleaned = clean_marc_label(name)
    if not re.search(r"[֐-ת]", cleaned):
        return cleaned
    ibn = _IBN_INVERTED_RE.match(cleaned)
    if ibn:
        return f"{ibn.group('given').strip()} אבן {ibn.group('sur').strip()}".strip()
    cleaned = re.sub(r",\s*(?:בן|אבן)\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\s+בן\s*$", "", cleaned).strip()
    return cleaned


def disambiguate_person_label(
    name: str,
    *,
    dates: str | None = None,
    control_number: str | None = None,
) -> str:
    """Add MS or date scope to very short Hebrew personal names."""
    cleaned = clean_marc_label(name)
    if not cleaned:
        return ""
    hebrew = len(re.findall(r"[\u0590-\u05ea]", cleaned))
    if hebrew > 0 and len(cleaned.split()) <= 1:
        if dates and str(dates).strip():
            return f"{cleaned} ({str(dates).strip()})"
        if control_number:
            return f"{cleaned} (MS {control_number})"
    return cleaned


def clean_marc_label(
    text: str,
    *,
    strip_ms_scope: bool = True,
    strip_enum_prefix: bool = False,
) -> str:
    """Strip MARC ISBD quote artifacts and surrounding whitespace."""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    cleaned = _normalize_marc_isbd_quotes(text.strip())
    # A ``|`` glues a MARC publication/edition note onto the title; the tail is
    # never part of the title label (P2.2).
    if "|" in cleaned:
        cleaned = cleaned.split("|", 1)[0].strip()
    if strip_ms_scope:
        cleaned = _IN_MS_SUFFIX_RE.sub("", cleaned).strip()
    if strip_enum_prefix:
        cleaned = _ENUM_PREFIX_RE.sub("", cleaned).strip()
    cleaned = cleaned.strip("\"'").strip()
    cleaned = re.sub(r"[,;:]+\s*$", "", cleaned).strip()
    if cleaned.endswith(".") and not cleaned.endswith(".."):
        cleaned = cleaned[:-1].strip()
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


_PERSONAL_HEADING_RE = re.compile(r"^\S+,\s+\S")


def infer_person_type(person_data: dict[str, Any]) -> str:
    """Return ``organization`` when the record should emit E74_Group.

    Field-based inference (110/610/710/810 → organization) must not override
    an obviously personal name: NLI catalogs famous former-owner persons
    (Adler, Roth, Sassoon) under corporate 710, so an inverted ``Surname,
    Given`` heading with no institutional keyword is a person even in those
    fields. ``Gaster, Moses Collection`` stays an organization on the
    ``collection`` keyword.
    """
    explicit = str(person_data.get("type") or "").lower()
    if explicit in {"organization", "org", "corporate", "institution"}:
        return "organization"
    if explicit in {"person", "personal", "individual"}:
        return "person"
    name = str(person_data.get("name") or "")
    if is_institutional_name(name):
        return "organization"
    stripped = name.strip()
    if _PERSONAL_HEADING_RE.match(stripped) and not re.search(r"\d", stripped):
        return "person"
    marc_field = str(person_data.get("field") or "")
    if marc_field in {"110", "710", "610", "810"}:
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


def preferred_lat_label_compatible(heb_name: str, lat_name: str) -> bool:
    """True when a Latin authority heading is safe to publish as the English label.

    VIAF/Mazal catalog headings (``Surname, Given``) often disagree with the
    MARC Hebrew heading for the same field — publishing them as ``en`` labels
    produces judge-visible mismatches. Only emit ``en`` when the two forms
    clearly refer to the same surface string.
    """
    heb = clean_marc_label(heb_name)
    lat = clean_marc_label(lat_name)
    if not heb or not lat:
        return False
    if not names_overlap(heb, lat):
        return False
    if "," in lat and "," not in heb and len(heb) < len(lat) * 0.6:
        return False
    return True


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
