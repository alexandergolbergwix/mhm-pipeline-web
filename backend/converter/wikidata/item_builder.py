"""Build Wikidata item representations from authority-enriched pipeline records.

Converts the structured JSON output of Stage 2 (authority matching) into
WikidataItem dataclasses ready for upload or QuickStatements export.

Uses ALL available pipeline data: NER entities, VIAF/Mazal authority matches,
KIMA place links, subjects, genres, physical features, provenance, colophon,
contents, condition, and epistemological tracking.

Entity linking: VIAF IDs and NLI/Mazal IDs are resolved to Wikidata QIDs
via the reconciler. Person claims on manuscripts use the resolved QIDs
when available, ensuring proper LOD wiring.

Follows WikiProject Manuscripts Data Model and Digital Scriptorium methodology.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

from converter.wikidata.hebrew_translit import english_label_for_hebrew
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.property_mapping import (
    CONDITION_TO_QID,
    GENRE_TO_QID,
    HMO_NS_TEMPLATE,
    KNOWN_WORK_QIDS,
    LANG_TO_QID,
    MATERIAL_TO_QID,
    P_APPLIES_TO_PART,
    P_AUTHOR,
    P_AUTHOR_NAME_STRING,
    P_BASED_ON_HEURISTIC,
    P_CATALOG_CODE,
    P_COLLECTION,
    P_CONDITION,
    P_DATE_OF_BIRTH,
    P_DATE_OF_DEATH,
    P_DESCRIBED_AT_URL,
    P_EARLIEST_DATE,
    P_EXACT_MATCH,
    P_EXEMPLAR_OF,
    P_GENRE,
    P_HEIGHT,
    P_IIIF_MANIFEST,
    P_INCEPTION,
    P_INSCRIPTION,
    P_INSTANCE_OF,
    P_INVENTORY_NUMBER,
    P_LANGUAGE,
    P_LAST_LINE,
    P_LATEST_DATE,
    P_LOCATION_OF_CREATION,
    P_MAIN_SUBJECT,
    P_MATERIAL,
    P_NATURE_OF_STATEMENT,
    P_NLI_CATALOG_ID,
    P_NLI_J9U_ID,
    P_NUMBER_OF_FOLIOS,
    P_NUMBER_OF_PAGES,
    P_NUMBER_OF_PARTS,
    P_OBJECT_HAS_ROLE,
    P_OBJECT_NAMED_AS,
    P_OCCUPATION,
    P_ON_FOCUS_LIST,
    P_OWNED_BY,
    P_REASON_DEPRECATED_RANK,
    P_SCRIPT_STYLE,
    P_SIGNIFICANT_PLACE,
    P_SOURCING_CIRCUMSTANCES,
    P_START_TIME,
    P_STATEMENT_SUPPORTED_BY,
    P_TITLE,
    P_VIAF_ID,
    P_VOLUME,
    P_WIDTH,
    P_WRITING_SYSTEM,
    PRECISION_CENTURY,
    PRECISION_YEAR,
    Q_AUTHOR_OCCUPATION,
    Q_CIRCA,
    Q_CODEX,
    Q_COLOPHON,
    Q_COMMENTATOR_OCCUPATION,
    Q_COMPOSITE_MANUSCRIPT,
    Q_CORRECTION,
    Q_DUBIOUS,
    Q_GLOSS,
    Q_HEBREW_ALPHABET,
    Q_HUMAN,
    Q_HYPOTHESIS,
    Q_BODLEIAN,
    Q_BRITISH_LIBRARY,
    Q_ISRAEL_MUSEUM,
    Q_ILLUMINATED_MANUSCRIPT,
    Q_KTIV,
    Q_LEAF_UNIT,
    Q_MANUSCRIPT,
    Q_MARGINALIA,
    Q_NLI,
    Q_ORGANIZATION,
    Q_PALIMPSEST,
    Q_PRINTED_BOOK,
    Q_POSSIBLY,
    Q_PRESUMABLY,
    Q_SCRIBE,
    Q_TRANSLATOR_OCCUPATION,
    Q_UNKNOWN_TEXT,
    Q_WIKIPROJECT_MANUSCRIPTS,
    Q_WRITTEN_WORK,
    ROLE_TO_PID,
    SCRIPT_TYPE_TO_QID,
    date_to_wikidata,
    format_wikidata_time,
    repair_wikidata_time,
    wikidata_time_year,
    extract_viaf_id,
    extract_wikidata_qid,
    hmo_wikibase_entity_url,
    hmo_wikibase_page_url,
    known_work_qid_for_title,
    nli_authority_reference,
    nli_j9u_id,
    nli_reference,
    viaf_reference,
)

logger = logging.getLogger(__name__)


# ── Label normalisation ──────────────────────────────────────────────

# ASCII straight quotes + Unicode smart/typographic quotes found in
# MARC catalog exports (left/right single and double, angle brackets).
_QUOTE_CHARS = '"\'"\u201c\u201d\u2018\u2019\u00ab\u00bb\u2039\u203a'

# MARC geresh / gershayim (Hebrew diacritics used as quotation marks)
# that appear SURROUNDING a name rather than inside it.
_HEBREW_QUOTE_CHARS = "\u05f3\u05f4"  # ׳ and ״

# MARC relator terms that appear in parentheses after a name.
# e.g. "Cohen, David (author)" → strip "(author)" before inverting.
_MARC_RELATOR_RE = re.compile(
    r"\s*\((?:"
    r"author|ed(?:itor)?s?\.?|tr(?:anslator|ans)?\.?|ill(?:ustrator)?\.?|"
    r"compiler|comp\.?|copyist|scribe|annotator|contributor|adapter|"
    r"composer|performer|engraver|printer|publisher|collector|respondent|"
    r"cartographer|photographer|joint\s+author|joint\s+editor|"
    r"former\s+owner|current\s+owner|owner|"
    # Hebrew relators + catalog mention markers that pollute aliases.
    r"מעתיק|מחבר|עורך|מתרגם|מאייר|מוזכר|בעלים(?:\s+קודמים)?|אליו"
    r")\.?\s*\)\s*$",
    re.IGNORECASE,
)

# MARC "active/fl./circa Nth century" date suffix that isn't a numeric range.
# "Nathan ben Abraham, active 11th century" → strip before comma-split.
_MARC_ACTIVITY_DATE_RE = re.compile(
    r",\s*(?:active|fl\.|flourished?|circa|ca\.|approximately|עפ\"י|בערך)"
    r"[^,]*$",
    re.IGNORECASE,
)

# Bracketed MARC notes that must not appear in Wikidata labels.
# e.g. "[microform]", "[manuscript]", "[i.e. ...]", "[sic]", "[u.a.]"
_MARC_BRACKET_NOTE_RE = re.compile(r"\s*\[[^\]]{1,60}\]\s*")

# Catalogers embed restored Hebrew letters in square brackets
# (``מע[וצ']ה`` → ``מעוצ'ה``). Those are orthography, not notes — expand
# them before the note-strip regex turns them into ``מע ה`` (export-35).
_HEBREW_BRACKET_EXPAND_RE = re.compile(
    r"\[([\u0590-\u05ff'׳״\"]{1,40})\]",
)

# Trailing MARC ISBD punctuation including " /" (before statement of
# responsibility) and " :" (before subtitle).
_MARC_ISBD_TRAIL_RE = re.compile(r"[\s.,;:/\-–]+$")


def _normalise_label(s: str) -> str:
    """Comprehensive MARC-to-Wikidata label normalizer.

    Applied to EVERY label and alias value before it is written to a
    WikidataItem. Order matters — relator stripping must come before
    trailing-punctuation stripping.

    Steps:
    1. Strip leading/trailing whitespace.
    2. Strip surrounding ASCII and Unicode quote characters.
    3. Strip surrounding Hebrew geresh/gershayim quote marks.
    4. Strip MARC relator terms in trailing parentheses: "(author)",
       "(ed.)", "(copyist)", "(מעתיק)", etc.
    5. Expand Hebrew restoration brackets (``מע[וצ']ה`` → ``מעוצ'ה``).
    6. Strip bracketed MARC notes: "[microform]", "[sic]", etc.
    7. Strip trailing MARC ISBD punctuation: . , ; : / - –
    8. Collapse internal runs of multiple spaces to a single space.
    9. Title-case ALL-CAPS Latin labels (e.g. "COHEN, DAVID" → "Cohen, David").
       Hebrew and mixed-script names are left unchanged.
    """
    if not s:
        return s
    s = s.strip()
    s = s.strip(_QUOTE_CHARS + _HEBREW_QUOTE_CHARS).strip()
    # MARC exports frequently escape catalog quote wrappers as ASCII\".
    # Preserve an internal ASCII gershayim between Hebrew letters (רס"ג,
    # רש"י, רמב"ם); only remove wrapper/noise quotes.
    s = s.replace('\\"', '"')
    # A doubled MARC quote between Hebrew letters is one gershayim mark,
    # not two characters to delete (e.g. רס""ג → רס"ג).
    s = re.sub(r'(?<=[\u0590-\u05ff])"{2,}(?=[\u0590-\u05ff])', '"', s)
    protected = re.sub(r'(?<=[\u0590-\u05ff])"(?=[\u0590-\u05ff])', "\ue002", s)
    s = protected.replace('"', "").replace("\ue002", '"')
    s = _MARC_RELATOR_RE.sub("", s).strip()
    s = _HEBREW_BRACKET_EXPAND_RE.sub(r"\1", s)
    s = _MARC_BRACKET_NOTE_RE.sub(" ", s).strip()
    s = _MARC_ISBD_TRAIL_RE.sub("", s).strip()
    while s.endswith(")") and s.count(")") > s.count("("):
        s = s[:-1].rstrip()
    s = re.sub(r" {2,}", " ", s)
    # Title-case only when the string is entirely uppercase Latin script
    # (i.e. no Hebrew/Arabic/Cyrillic characters, and >50% are uppercase letters).
    has_nonlatin = bool(re.search(r"[\u0590-\u05ff\u0600-\u06ff\u0400-\u04ff]", s))
    if not has_nonlatin:
        latin_chars = [c for c in s if c.isalpha()]
        if latin_chars and sum(c.isupper() for c in latin_chars) / len(latin_chars) > 0.8:
            s = s.title()
    return s


def _strip_name_quotes(s: str) -> str:
    """Strip surrounding quote characters and trailing MARC ISBD punctuation.

    Retained for call sites that pre-date _normalise_label; internally
    delegates to _normalise_label for consistency.
    """
    return _normalise_label(s)


# ── Person deduplication key ─────────────────────────────────────────


def _person_key(name: str, viaf_uri: str | None, mazal_id: str | None) -> str:
    """Create a deduplication key for a person entity."""
    if mazal_id:
        return f"mazal:{mazal_id}"
    if viaf_uri:
        viaf_id = extract_viaf_id(viaf_uri)
        if viaf_id:
            return f"viaf:{viaf_id}"
    normalized = re.sub(r"[,.\s]+", "_", name.strip().lower())
    return f"name:{normalized}"


def _marc_entry_label(
    entry: object,
    *,
    keys: tuple[str, ...] = ("place", "name", "term", "title", "text"),
) -> str:
    """Coerce a MARC list entry (str or dict) to a plain label string."""
    if entry is None:
        return ""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in keys:
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return ""
    return str(entry).strip()


_INSTITUTIONAL_KEYWORDS: tuple[str, ...] = (
    "library",
    "museum",
    "university",
    "institute",
    "seminary",
    "school",
    "college",
    "society",
    "academy",
    "foundation",
    "association",
    "trust",
    "centre",
    "center",
    "archive",
    "bodleian",  # Bodleian Library, Oxford
    "palatina",  # Bibliotheca Palatina
    "ספרייה",
    "מכון",
    "אוניברסיטה",
    "מוזיאון",
    "קהילה",
    "מכללה",
    "ארכיון",
)

# MARC-artifact qualifier words that sometimes leak into person-name fields.
# E.g. MARC "Collection Gaster, Moses" inverts to "Moses Collection Gaster".
# These are NOT part of the personal name; strip them from interior positions
# (but not from boundary positions, where they signal a real institutional entity).
_PERSON_NAME_QUALIFIER_WORDS: frozenset[str] = frozenset(
    {
        "collection",
        "papers",
        "correspondence",
        "records",
        "letters",
        "documents",
        "bequest",
        "gift",
    }
)


def _strip_person_name_qualifiers(name: str) -> str:
    """Strip MARC-artifact qualifier words from the interior of a personal name.

    "Moses Collection Gaster"  → "Moses Gaster"
    "Gaster Collection"        → unchanged  (boundary — may be a real collection entity)
    "Ibn Gerson, Levi"         → unchanged  (no qualifier words)
    """
    tokens = name.split()
    if len(tokens) < 3:
        return name  # 1- or 2-token names: too short to safely strip
    lower_tokens = [t.lower().rstrip(".,") for t in tokens]
    cleaned = [
        tokens[i]
        for i, lt in enumerate(lower_tokens)
        if lt not in _PERSON_NAME_QUALIFIER_WORDS or i == 0 or i == len(tokens) - 1
    ]
    if len(cleaned) == len(tokens):
        return name
    return " ".join(cleaned) if cleaned else name


# Names that should never produce Wikidata person items — they are generic
# catalog placeholders for unknown or anonymous persons/authors.
# Fix 2026-04-15 third audit Fix #5.
_ANONYMOUS_NAMES: frozenset[str] = frozenset(
    {
        "unknown",
        "anonymous",
        "anon",
        "לא ידוע",
        "לא נודע",
        "סופר לא ידוע",
        "מחבר לא ידוע",
        "author unknown",
        "scribe unknown",
        "unknown author",
        "unknown scribe",
    }
)


def _is_anonymous_name(name: str) -> bool:
    """True if the name is a generic placeholder for an unknown person."""
    return name.strip().lower().rstrip(".,;:") in _ANONYMOUS_NAMES


# Role-descriptors and bare place-names that MARC catalogers use as person
# identifiers when no real name is known.  Wikidata items must not be created
# for these — they are descriptions of a person's role or origin, not names.
# Found in the wild: "משומד" (apostate), "שאלוניקי" (from Salonika).
_ROLE_DESCRIPTOR_NAMES: frozenset[str] = frozenset(
    {
        # Hebrew role/status descriptors
        "משומד",  # apostate (male)
        "משומדת",  # apostate (female)
        "מומר",  # apostate/convert (male)
        "מומרת",  # apostate/convert (female)
        "הגר",  # female convert to Judaism
        "כוהן",  # priest (without a name)
        "הכוהן",  # the priest
        "לוי",  # Levite (without a name — "לוי" as a surname is fine)
        "הלוי",  # the Levite
        "הסופר",  # the scribe
        "הנביא",  # the prophet
        "הרב",  # the rabbi (without a name)
        "הרופא",  # the physician
        "הדיין",  # the judge
        "הנגיד",  # the leader
        "הפרנס",  # the community leader
        # Bare city/country names used as sole personal identifiers
        "שאלוניקי",  # Salonika/Thessaloniki
        "סלוניקי",  # Salonika variant
        "קושטא",  # Constantinople/Istanbul
        "קושטנדינא",  # Constantinople variant
        "אשכנז",  # Germany
        "ספרד",  # Spain
        "ונציה",  # Venice
        "פאס",  # Fez
        "תוניס",  # Tunis
        "מצרים",  # Egypt
        "בגדאד",  # Baghdad
        "פראג",  # Prague
        "קרקא",  # Kraków
        "ויניציאה",  # Venice (variant)
        "ליוורנו",  # Livorno
        "אמשטרדם",  # Amsterdam
    }
)


def _is_role_descriptor(name: str) -> bool:
    """True when the entire name is a role-word or bare place-name.

    These appear in MARC records when a cataloger identified someone only by
    their social role ('apostate') or city of origin ('from Salonika') without
    knowing their actual name.  Creating Wikidata items for these produces
    garbage entries with generic labels that violate Wikidata notability.
    """
    return name.strip() in _ROLE_DESCRIPTOR_NAMES


# NLI's catalog stores the source filename as the first MARC 500 general-note
# field (e.g. "990000623390205171.mrc", "BIBLIOGRAPHIC_50929717600005171_5.txt").
# These must never be emitted as P7535 scope/content notes on Wikidata.
_SOURCE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-]+\.(mrc|txt|csv|xml|json)$", re.IGNORECASE)

# Hebrew Unicode block (U+0590..U+05FF). A title like "Bible" or "Diodati
# Segre" has no characters in this range and must NOT land in the `he`
# label slot — that triggers HE_LABEL_IS_LATIN in item_validator.py
# (see Kolja21 community report on Q139231608).
_HEBREW_SCRIPT_RE = re.compile(r"[\u0590-\u05ff]")


def _has_hebrew_script(text: str | None) -> bool:
    """True iff `text` contains at least one Hebrew-block code point."""
    return bool(text) and bool(_HEBREW_SCRIPT_RE.search(text))


# ── Genre classifier lazy singleton ─────────────────────────────────
# Loaded on first use; None when model file is absent (graceful degradation).
_GENRE_CLASSIFIER: object | None = "unloaded"


def _get_genre_classifier() -> object | None:
    global _GENRE_CLASSIFIER
    if _GENRE_CLASSIFIER == "unloaded":
        # PyInstaller-frozen exe stages the .pt under sys._MEIPASS/ner/;
        # the dev / .app fallback is the repo-relative path.
        model_path: Path | None = None
        try:
            from mhm_pipeline.platform_.paths import bundled_resource_root  # noqa: PLC0415

            frozen = bundled_resource_root() / "ner" / "genre_classifier_model.pt"
            if frozen.exists():
                model_path = frozen
        except Exception:
            pass
        if model_path is None:
            dev = (
                Path(__file__).resolve().parent.parent.parent / "ner" / "genre_classifier_model.pt"
            )
            if dev.exists():
                model_path = dev
        if model_path is not None:
            try:
                from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415

                _GENRE_CLASSIFIER = GenreClassifier(str(model_path))
            except Exception as exc:
                logger.warning("Could not load genre classifier: %s", exc)
                _GENRE_CLASSIFIER = None
        else:
            _GENRE_CLASSIFIER = None
    return _GENRE_CLASSIFIER


def is_institutional_name(name: str) -> bool:
    """True if the name looks like an institution (library, museum, etc.).

    Used to re-route MARC 710 (added entry — corporate name) values away
    from P50 (author) to P195 (collection) — fix for the Q139085958 pattern
    Geagea reported (2026-04-15) where institutions were being assigned as
    authors of manuscripts.
    """
    if not name:
        return False
    lowered = name.lower()
    return any(kw in lowered for kw in _INSTITUTIONAL_KEYWORDS)


_is_institutional_name = is_institutional_name  # backward-compat alias for internal callers


def _to_natural_name_order(name: str) -> str:
    """Convert MARC's inverted name form 'Surname, Given' to Wikidata's
    natural-order convention 'Given Surname'.

    Bug fix (2026-04-15, Geagea complaint on Q139230386, label "סופינו, עמנואל"):
    Wikidata expects person labels in natural order. The inverted form is a
    cataloging convention that belongs in P1559 (native name) for searchability,
    not in the human-facing label.

    Rules:
    - "Surname, Given" → "Given Surname"
    - "Surname, Given (qualifier)" → "Given Surname (qualifier)"
    - "Surname, Given, second-Given" → "second-Given Given Surname" (rare,
      conservatively NOT flipped — leave as-is to avoid worse mistakes)
    - Names without exactly one comma → returned unchanged
    - Trailing dates "Surname, Given, 1850-1900" → "Given Surname (1850-1900)"
    - "Surname, Given, active 11th century" → "Given Surname (active 11th century)"
    """
    if not name or "," not in name:
        return name

    # Strip trailing MARC relator and bracket notes first so they don't
    # confuse the comma-split logic.
    name = _MARC_RELATOR_RE.sub("", name).strip()
    name = _MARC_BRACKET_NOTE_RE.sub(" ", name).strip()
    name = re.sub(r" {2,}", " ", name)

    # Split off any trailing numeric date range like ", 1850-1900"
    date_match = re.search(r",\s*(-?\d{2,4}(?:[-–]\d{0,4})?)\s*$", name)
    date_suffix = ""
    base = name
    if date_match:
        date_suffix = f" ({date_match.group(1)})"
        base = name[: date_match.start()]

    # Split off trailing "active/fl./circa Nth century" activity date
    # e.g. "Nathan ben Abraham, active 11th century"
    activity_match = _MARC_ACTIVITY_DATE_RE.search(base)
    if activity_match:
        activity_text = base[activity_match.start() :].lstrip(",").strip()
        date_suffix = (date_suffix or "") + f" ({activity_text})"
        base = base[: activity_match.start()]

    parts = [p.strip() for p in base.split(",")]
    # Drop empty parts (trailing comma case)
    parts = [p for p in parts if p]
    if len(parts) != 2:
        # Either zero commas (unchanged) or more than one comma (ambiguous);
        # return unchanged + any trailing date suffix.
        return name if not date_suffix else (base.strip() + date_suffix)
    surname, given = parts
    return f"{given} {surname}{date_suffix}"


# ── Work deduplication key ──────────────────────────────────────────


def _work_key(title: str) -> str:
    """Create a deduplication key for a work entity."""
    normalized = re.sub(r'[,.\s"׳״]+', "_", title.strip().lower())
    return f"work:{normalized}"


def _cap_description(desc: str, max_len: int = 250) -> str:
    """Truncate a Wikidata description to the community-recommended maximum.

    Wikidata's soft limit is 250 characters (Help:Description). Descriptions
    over this length are accepted by the API but flagged by quality tools.
    Fix 2026-04-15 third audit Fix #14.
    """
    return desc[:max_len] if len(desc) > max_len else desc


def _ascii_dates(s: str) -> str:
    """Return only the ASCII portion of a dates string for English descriptions.

    MARC authority data for medieval scholars (Geonim etc.) sometimes stores
    Arabic date expressions (e.g. 'توفي 1013' = 'died 1013'). Wikidata English
    descriptions must be readable by all patrollers regardless of script.
    Non-ASCII chars are dropped; surrounding noise is trimmed.
    """
    cleaned = "".join(c for c in s if ord(c) < 128).strip(" .,;:-")
    return cleaned


def _build_work_description(author_name: str | None, century: str | None) -> str:
    """Build a disambiguating English description for a work item.

    Wikidata requires descriptions to disambiguate same-label items.
    Bug fix 2026-04-15 (web audit): previously all work descriptions were
    identical ('Hebrew manuscript work'), making same-titled works
    indistinguishable. Now includes author and century when available.
    """
    parts = ["Work preserved in a Hebrew manuscript"]
    if author_name:
        cleaned = author_name.strip().rstrip(",;:")
        if cleaned:
            # English descriptions must remain readable in English. Hebrew
            # author names are retained in P50/P2093 and source evidence, not
            # copied verbatim into the English description slot.
            if _has_hebrew_script(cleaned):
                parts.append("with author recorded in the source catalogue")
            else:
                parts.append(f"by {cleaned}")
    if century:
        ascii_century = _ascii_dates(str(century))
        if ascii_century:
            parts.append(f"({ascii_century})")
    return _cap_description(" ".join(parts))


def _build_work_description_for_record(
    author_name: str | None,
    century: str | None,
    source_record: dict[str, object] | None = None,
) -> str:
    """Work description that respects printed-facsimile source records (W-172)."""
    if source_record is not None and _is_printed_facsimile_record(source_record):
        parts = ["Work issued as a printed facsimile edition"]
        if author_name:
            cleaned = author_name.strip().rstrip(",;:")
            if cleaned and not _has_hebrew_script(cleaned):
                parts.append(f"by {cleaned}")
            elif cleaned:
                parts.append("with author recorded in the source catalogue")
        if century:
            ascii_century = _ascii_dates(str(century))
            if ascii_century:
                parts.append(f"({ascii_century})")
        return _cap_description(" ".join(parts))
    return _build_work_description(author_name, century)


_ROLE_TO_LABEL: dict[str, str] = {
    "AUTHOR": "author",
    "author": "author",
    "SCRIBE": "scribe",
    "scribe": "scribe",
    # The keyword classifier in ``ner/inference_pipeline.py`` emits
    # ``TRANSCRIBER`` (not ``SCRIBE``); both alias to the same label.
    "TRANSCRIBER": "scribe",
    "transcriber": "scribe",
    "OWNER": "owner",
    "owner": "owner",
    "TRANSLATOR": "translator",
    "translator": "translator",
    "EDITOR": "editor",
    "editor": "editor",
    "COMMENTATOR": "commentator",
    "commentator": "commentator",
    "PATRON": "patron",
    "patron": "patron",
}


def _is_catalog_note_placeholder(value: object) -> bool:
    """Return whether text is catalog/workflow metadata, not manuscript text.

    These values can arrive in ``colophon_text`` or ``scribal_interventions``
    after the shared MARC/NER merge. Keeping them in source evidence is useful,
    but projecting them as P1684 would make a false scholarly claim.
    """
    from converter.wikidata.catalog_notes import is_catalog_note_placeholder

    return is_catalog_note_placeholder(value)

_HOLDER_PLACEHOLDER_NAMES = {
    "unknown library",
    "unknown institution",
    "unknown holder",
    "לא ידוע",
    "ספריה לא ידועה",
    "library of the admor",
    "ha-rav shochet",
}


def _is_placeholder_holder(name: str) -> bool:
    folded = name.casefold()
    return (
        not name
        or folded in _HOLDER_PLACEHOLDER_NAMES
        or any(
            token in folded
            for token in _HOLDER_PLACEHOLDER_NAMES
            if len(token) > 5
        )
    )


def holder_names_from_record(record: dict[str, object]) -> list[str]:
    """Every name the record attests as the CURRENT holder, best first.

    Shared with ``manuscript_projection._current_holder_qid`` so the name that
    keys the label and the name that keys P195 cannot diverge — they used to scan
    different fields (contributors + holding_institution here, contributors +
    marc_authority_matches there).
    """
    names: list[str] = []
    for contributor in record.get("contributors") or []:
        if not isinstance(contributor, dict):
            continue
        role = str(contributor.get("role") or "").casefold().replace("_", " ")
        if "current owner" not in role:
            continue
        name = _normalise_label(str(contributor.get("name") or ""))
        if name and name not in names:
            names.append(name)
    holding = _normalise_label(str(record.get("holding_institution") or ""))
    if holding and holding not in names:
        names.append(holding)
    return [name for name in names if not _is_placeholder_holder(name)]


def holder_resolution_for_record(record: dict[str, object]):
    """The audited holder resolution for *record*, or None when none is attested.

    Routes through ``holding_institutions`` (Rule W-143) rather than the
    ``_INSTITUTIONAL_KEYWORDS`` substring test that used to gate this. That test
    rejected "Braginsky Collection" — "collection" is in
    ``_PERSON_NAME_QUALIFIER_WORDS``, not the institutional keywords — and the
    caller then fell back to "Jerusalem, NLI" over the record's own attested
    holder (Rule W-161).
    """
    from converter.wikidata.holding_institutions import (  # noqa: PLC0415
        resolve_first_holder,
    )

    return resolve_first_holder(holder_names_from_record(record))


def _holding_institution_name(record: dict[str, object]) -> str:
    """The holder name to put in a label: the verified form, else what MARC said.

    Empty only when the record attests no holder at all. A holder we cannot link
    is still a holder, and naming it is attestation — inventing NLI instead is
    fabrication (Rule W-75 / W-82).
    """
    resolution = holder_resolution_for_record(record)
    return resolution.display_name if resolution else ""


def manuscript_en_label(shelfmark: str, holder_name: str) -> str:
    """The English shelfmark label. NEVER defaults to a holder (Rule W-161).

    Three cases, and the third is the one that was broken:

    1. holder resolved   → the verified table label + shelfmark.
    2. holder attested but unlinkable → the record's own 710 string + shelfmark.
       Attestation, not fabrication — this is what fixes the 11 items that read
       "Jerusalem, NLI, F 39766" while MARC named Braginsky or Beit Ariela.
    3. no holder attested → the shelfmark alone. An unowned shelfmark is honest;
       an invented owner is not.
    """
    shelfmark = str(shelfmark or "").strip()
    holder_name = str(holder_name or "").strip()
    if not shelfmark:
        return ""
    return f"{holder_name}, {shelfmark}" if holder_name else shelfmark


def manuscript_record_label(control_number: str, record: dict[str, object] | None = None) -> str:
    """The no-shelfmark fallback: a CATALOGUE designation, not an ownership claim.

    When the holder is known, name it (Cambridge must not read as an NLI holding).
    When unknown, say ``catalog record`` — never invent ``NLI record`` (Rule W-161 / W-170).
    """
    cn = str(control_number or "").strip()
    if not cn:
        return ""
    languages = (record or {}).get("languages") or []
    primary = str(languages[0]) if languages else "heb"
    lang_name = _LANG_CODE_TO_ENGLISH.get(primary, "Hebrew")
    kind = (
        "printed facsimile edition"
        if _is_printed_facsimile_record(record or {})
        else "manuscript"
    )
    holder = _holding_institution_name(record or {})
    if holder:
        return f"{lang_name} {kind}, {holder}, {cn}"
    return f"{lang_name} {kind}, catalog record {cn}"


# Hebrew forms for holders whose verified English label we already trust. A holder
# absent here keeps its attested Latin name — transliterating it would be
# inventing a name Wikidata does not carry.
HEBREW_INSTITUTION_NAMES: dict[str, str] = {
    "The National Library of Israel": "הספרייה הלאומית",
    "National Library of Israel": "הספרייה הלאומית",
    "The Israel Museum": "מוזיאון ישראל",
    "Israel Museum": "מוזיאון ישראל",
    "The Ben Zvi Institute": "מכון בן־צבי",
}


def manuscript_he_designation(
    record: dict[str, object] | None,
    suffix: str,
    *,
    holder_name: str | None = None,
) -> str:
    """The `he` designation label — the record's own language and holder.

    The old form hardcoded "כתב יד עברי, ספרייה לאומית", so an Israel Museum
    manuscript announced the National Library as its holder (Rule W-142 / W-82).
    A holder that did not resolve contributes its attested name, never NLI.
    """
    languages = (record or {}).get("languages") or []
    primary = str(languages[0]) if languages else "heb"
    if _is_printed_facsimile_record(record or {}):
        parts = [f"מהדורת פקסימיליה מודפסת ({_LANG_CODE_TO_HEBREW.get(primary, 'עברי')})"]
    else:
        parts = [f"כתב יד {_LANG_CODE_TO_HEBREW.get(primary, 'עברי')}"]
    holder = (
        holder_name
        if holder_name is not None
        else _holding_institution_name(record or {})
    )
    if holder:
        parts.append(HEBREW_INSTITUTION_NAMES.get(holder, holder))
    if suffix:
        parts.append(str(suffix))
    return ", ".join(parts)


def _is_placeholder_title(title: str | None) -> bool:
    """Return True if a MARC 245 title is a generic catalog placeholder.

    Bug fix 2026-04-15 (Geagea complaint, 2026-04-15): catalogers use
    "קובץ" / "קבץ" (= "compilation" / "file") and short topical variants
    ("קובץ בקבלה" = "Kabbalah compilation") as the title field of MARC
    records for multi-text anthologies that have no overarching real
    title. When emitted as a Wikidata Hebrew label, these strings are
    useless for disambiguation and were flagged as nonsense by the
    Hebrew-Wikidata community.

    We treat as placeholder:
    - exact "קובץ" / "קבץ" (with optional trailing punctuation)
    - "קובץ X" / "קבץ X" where the whole string is short (≤ 25 chars)

    The original string is preserved as a Hebrew alias by the caller so
    it remains searchable; the Wikidata LABEL falls back to a synthetic
    shelfmark-based label.
    """
    if not title:
        return False
    cleaned = _normalise_label(title).strip().rstrip(".,;:")
    if cleaned in {
        "קובץ",
        "קבץ",
        # "Writings" — biblical category used as a catch-all heading for
        # anthological MARC records with no specific title. Adding here
        # prevents """כתובים""" or "כתובים" from landing as a Wikidata label.
        "כתובים",
        # Stand-alone generic headings without a discriminating subtitle:
        "כתב יד",
        "מחזור",
        "סידור",
        "אוסף",
    }:
        return True
    # Short topical placeholder like "קובץ בקבלה" or "קבץ מדרשים"
    if cleaned.startswith(("קובץ ", "קבץ ")) and len(cleaned) <= 25:
        return True
    return False


def _build_person_description(role: str, dates_str: str, is_org: bool) -> str:
    """Build a disambiguating English description for a person item.

    Wikidata expects descriptions to disambiguate same-label items.
    Bug fix 2026-04-16 (deeper audit Fix #13): previously emitted a bare
    "person (1200-1280)" or generic "person associated with Hebrew
    manuscripts". Now incorporates the role so e.g. two different scribes
    with the same name can be told apart.
    """
    # Strip non-ASCII from dates_str: NLI/Mazal authority data for Gaonic-era
    # scholars stores Arabic date expressions (e.g. 'توفي 1013'). English
    # descriptions must be ASCII-readable for all Wikidata patrollers.
    safe_dates = _ascii_dates(dates_str) if dates_str else ""
    if is_org:
        if safe_dates:
            return _cap_description(f"organization ({safe_dates})")
        return "organization associated with Hebrew manuscripts"
    role_label = _ROLE_TO_LABEL.get((role or "").strip(), "")
    # Role-only "Hebrew manuscript editor/commentator" descriptions arrive on
    # Studio persons whose authority_evidence was slimmed, so the judge sees an
    # unsupported description (export-28 Person_164). Keep role wording when
    # dates disambiguate; otherwise fall back to the generic person line.
    if role_label in {"editor", "commentator"} and not safe_dates:
        return "person associated with Hebrew manuscripts"
    if role_label and safe_dates:
        return _cap_description(f"{role_label} ({safe_dates})")
    if role_label:
        return _cap_description(f"Hebrew manuscript {role_label}")
    if safe_dates:
        return _cap_description(f"person ({safe_dates})")
    return "person associated with Hebrew manuscripts"


def _extract_inception_year(record: dict[str, object]) -> int | None:
    """Return the manuscript's earliest known year (CE) if available.

    Used by the public-domain (P6216) gate so we only assert public-domain
    status on demonstrably pre-1900 works. Returns ``None`` when no year
    can be determined — caller should err on the side of NOT asserting
    public domain.

    Looks at: record["dates"]["year"], 260/264 $c via original_string, and
    parse of the original Hebrew/English date string.
    """
    dates = record.get("dates")
    if isinstance(dates, dict):
        year = dates.get("year") or dates.get("date1") or dates.get("year_start")
        if year is not None:
            try:
                return int(year)
            except (TypeError, ValueError):
                pass
        original = str(dates.get("original_string") or "")
        m = re.search(r"\b(\d{3,4})\b", original)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


_LANG_CODE_TO_ENGLISH: dict[str, str] = {
    "heb": "Hebrew",
    "ara": "Arabic",
    "jrb": "Judeo-Arabic",
    "jpr": "Judeo-Persian",
    "lat": "Latin",
    "per": "Persian",
    "yid": "Yiddish",
    "grk": "Greek",
    "ita": "Italian",
    "spa": "Spanish",
    "por": "Portuguese",
    "ger": "German",
    "fre": "French",
    "tur": "Turkish",
    "syr": "Syriac",
    "cop": "Coptic",
    "sam": "Samaritan",
}

# Hebrew counterpart of _LANG_CODE_TO_ENGLISH, so the generated `he`
# description can never disagree with `en` about the language (Rule W-140).
_LANG_CODE_TO_HEBREW: dict[str, str] = {
    "heb": "עברי",
    "ara": "ערבי",
    "jrb": "יהודי-ערבי",
    "jpr": "יהודי-פרסי",
    "lat": "לטיני",
    "per": "פרסי",
    "yid": "ביידיש",
    "grk": "יווני",
    "ita": "איטלקי",
    "spa": "ספרדי",
    "por": "פורטוגזי",
    "ger": "גרמני",
    "fre": "צרפתי",
    "tur": "טורקי",
    "syr": "סורי",
    "cop": "קופטי",
    "sam": "שומרוני",
}

_SCRIPT_TYPE_LABELS: dict[str, str] = {
    "AshkenaziScript": "Ashkenazi script",
    "SepharadicScript": "Sephardi script",
    "ItalianScript": "Italian script",
    "ByzantineScript": "Byzantine script",
    "YemeniteScript": "Yemenite script",
    "OrientalScript": "Oriental script",
}

_MATERIAL_LABELS: dict[str, str] = {
    "Parchment": "parchment",
    "parchment": "parchment",
    "Vellum": "vellum",
    "vellum": "vellum",
    "Paper": "paper",
    "paper": "paper",
    "Papyrus": "papyrus",
    "papyrus": "papyrus",
    "קלף": "parchment",
    "נייר": "paper",
    "פפירוס": "papyrus",
}


_FACSIMILE_RE = re.compile(
    r"דפוס\s+צלום|פקסימיל|photographic\s+(?:print|facsimile)|printed\s+facsimile|facsimile\s+edition",
    re.IGNORECASE,
)


def _is_printed_facsimile_record(record: dict[str, object]) -> bool:
    text = " ".join(
        str(record.get(key) or "")
        for key in ("title", "summary", "notes", "physical_description")
    )
    return bool(_FACSIMILE_RE.search(text))


def _description_date_fragment(dates: dict[str, object]) -> str | None:
    """Return an English date phrase for manuscript descriptions at the right precision.

    Never put Hebrew century text (מאה …) or a century midpoint year (1501/1001)
    into the English description (Rule W-164 / W-170).
    """
    if not isinstance(dates, dict):
        return None
    original = str(dates.get("original_string") or "").replace('""', '"').strip()
    century_range = re.search(
        r"\d{1,2}(?:th|st|nd|rd)\s*[-–]\s*\d{1,2}(?:th|st|nd|rd)\s*centur(?:y|ies)",
        original,
        re.IGNORECASE,
    )
    if century_range:
        return century_range.group(0).lower()
    single_century = re.search(
        r"\d{1,2}(?:th|st|nd|rd)\s*century",
        original,
        re.IGNORECASE,
    )
    if single_century:
        return single_century.group(0).lower()
    # Hebrew century in original_string → English century phrasing when possible.
    from converter.wikidata.property_mapping import (  # noqa: PLC0415
        _HEBREW_ORDINAL_TO_INT,
        _parse_hebrew_century,
    )

    heb_range = re.search(
        r'מאה\s+([א-ת]["\u05F4\']?[א-ת]?)\s*[-–]\s*([א-ת]["\u05F4\']?[א-ת]?)',
        original,
    )
    if heb_range:
        c1 = _HEBREW_ORDINAL_TO_INT.get(heb_range.group(1).strip())
        c2 = _HEBREW_ORDINAL_TO_INT.get(heb_range.group(2).strip())
        if not c1:
            c1 = _parse_hebrew_century(f"מאה {heb_range.group(1)}")
        if not c2:
            c2 = _parse_hebrew_century(f"מאה {heb_range.group(2)}")
        if c1 and c2:
            earlier, later = min(c1, c2), max(c1, c2)
            return f"{earlier}th–{later}th century"
    heb_century = _parse_hebrew_century(original)
    if heb_century:
        return f"{heb_century}th century"
    date_format = str(dates.get("date_format") or "")
    year = str(dates.get("year") or "").strip('" ')
    if year and re.match(r"\d{3,4}$", year):
        # Exact year rows (with or without FullDate) may enter the description.
        # Century-encoded midpoints (HebrewCentury / Century) must not.
        if date_format in {
            "HebrewCentury", "Century", "HebrewGematriaCentury",
        }:
            pass
        elif (
            not date_format
            or date_format == "FullDate"
            or re.search(rf"\b{re.escape(year)}\b", original)
        ):
            return year
    year_match = re.search(r"\b(\d{3,4})\b", original)
    if year_match and not re.search(r"centur|מאה", original, re.IGNORECASE):
        return year_match.group(1)
    return None


def _build_manuscript_description(record: dict[str, object]) -> str:
    """Build a rich, disambiguating English description for a manuscript item.

    Format: "<language> manuscript, <date/century>, <script tradition>,
              <material>, National Library of Israel"

    Each fragment is included only when available so the description is
    always meaningful but never padded with empty placeholders.

    Examples:
      "Hebrew manuscript, 16th century, Sephardi script, parchment, NLI"
      "Hebrew manuscript, 1612, National Library of Israel"
      "Judeo-Arabic manuscript, 12th–13th century, Oriental script, NLI"
    """
    langs = record.get("languages") or []
    # Map the first MARC language code to a readable English name
    primary_lang = str(langs[0]) if langs else "heb"
    lang_str = _LANG_CODE_TO_ENGLISH.get(primary_lang, "Hebrew")

    if _is_printed_facsimile_record(record):
        parts: list[str] = [f"{lang_str} printed facsimile edition"]
    else:
        parts = [f"{lang_str} manuscript"]

    # Date — prefer a readable century string; fall back to exact year.
    dates = record.get("dates") or {}
    date_fragment = _description_date_fragment(dates if isinstance(dates, dict) else {})
    if date_fragment:
        parts.append(date_fragment)

    # Script tradition
    script_type = str(record.get("script_type") or "").strip()
    script_label = _SCRIPT_TYPE_LABELS.get(script_type)
    if script_label:
        parts.append(script_label)

    # Material — first recognised material only (keep description short)
    for mat in list(record.get("materials") or [])[:1]:
        mat_label = _MATERIAL_LABELS.get(str(mat))
        if mat_label:
            parts.append(mat_label)

    holding_name = _holding_institution_name(record)
    if holding_name:
        parts.append(holding_name)
    # A Wikidata description disambiguates; it is not a subject summary. The
    # appended "Subjects include …" clause read as catalog spill — every one of
    # the 26 manuscripts carrying it was judged partial or fail, while the
    # plain "<language> manuscript, <date>, <holder>" form passed (Rule W-137).
    # Unresolved topics remain available as evidence, never as description text.
    return _cap_description(", ".join(parts))


def _extract_century_for_work(source_record: dict[str, object]) -> str | None:
    """Extract a human-readable century string for the work description.

    Pulls from the manuscript's date data when present (e.g. '16th century',
    'מאה ט"ז'). Returns None when no century info is available so the
    description omits the parenthetical.
    """
    dates = source_record.get("dates")
    if not isinstance(dates, dict):
        return None
    original = str(dates.get("original_string") or "").replace('""', '"').strip()
    if not original:
        return None
    eng_match = re.search(r"\d{1,2}(?:th|st|nd|rd)\s*century", original, re.IGNORECASE)
    if eng_match:
        return eng_match.group(0).lower()
    if "מאה" in original:
        # Take just up to the closing date ordinal
        snippet = re.search(r"מאה\s+[א-ת][\u05F4\"\']?[א-ת]?", original)
        if snippet:
            return snippet.group(0)
    return None


# ── Role → occupation QID ────────────────────────────────────────────

_ROLE_TO_OCCUPATION: dict[str, str] = {
    "AUTHOR": Q_AUTHOR_OCCUPATION,
    "author": Q_AUTHOR_OCCUPATION,
    "TRANSCRIBER": Q_SCRIBE,
    "scribe": Q_SCRIBE,
    "copyist": Q_SCRIBE,
    "TRANSLATOR": Q_TRANSLATOR_OCCUPATION,
    "translator": Q_TRANSLATOR_OCCUPATION,
    "COMMENTATOR": Q_COMMENTATOR_OCCUPATION,
    "commentator": Q_COMMENTATOR_OCCUPATION,
}


# ── Work-title / embedded-author splitter ────────────────────────────

_PERSON_NAME_SIGNALS_RE = re.compile(
    r'\bבן\b|\bב"ר\b|\bבר\b|\bibn\b|\bbar\b|\bben\b',
    re.IGNORECASE,
)

_WORK_ATTRIBUTION_RE = re.compile(
    r"\s+(?:חברו|חיברו|חיברוהו|מחברו)\s+",
    re.IGNORECASE,
)
_WORK_AUTHOR_HONORIFICS_RE = re.compile(
    r"^(?:(?:האלוף|הגאון|החכם|הרב|רבי|הר\"ר|"
    r"כמה[\"״]?ר|כמוה[\"״]?ר|מהר[\"״]?ר|ר['׳])\s+)+",
)


def _split_work_title_author(text: str) -> tuple[str, str | None]:
    """Split a catalog work title from a confident author attribution.

    Only splits when the candidate author segment contains a genealogical
    marker (בן/ב"ר/ibn/bar) or a source attribution gives a personal name (W-206).
    Returns (text, None) unchanged when no confident split is found.

    Examples:
      "ספר היראה ליונה בן אברהם גרונדי"  → ("ספר היראה", "יונה בן אברהם גרונדי")
      "סדור מנהג אשכנז לכל השנה"          → ("סדור מנהג אשכנז לכל השנה", None)
      "צוואת יהודה החסיד מרגנשבורג ליהודה בן שמואל החסיד"
                                           → ("צוואת יהודה החסיד מרגנשבורג", "יהודה בן שמואל החסיד")
    """
    attribution = _WORK_ATTRIBUTION_RE.search(text)
    if attribution:
        title_part = text[: attribution.start()].strip()
        author_part = text[attribution.end() :].strip()
        author_part = _WORK_AUTHOR_HONORIFICS_RE.sub("", author_part).strip()
        heb_tokens = [
            token for token in author_part.split()
            if re.search(r"[א-ת]{2,}", token)
        ]
        if title_part and (
            _PERSON_NAME_SIGNALS_RE.search(author_part) or len(heb_tokens) >= 2
        ):
            return title_part, author_part

    candidates = [m.start() for m in re.finditer(r" ל(?=[א-ת])", text)]
    for pos in reversed(candidates):
        author_part = text[pos + 2 :].strip()
        title_part = text[:pos].strip()
        if not title_part:
            continue
        if _PERSON_NAME_SIGNALS_RE.search(author_part):
            return title_part, author_part
        # Hebrew prepositions such as "לכל השנה" are part of a title, not an
        # author introduction. Genealogical markers above remain authoritative
        # even when a real name begins with one of these lexical forms.
        if author_part.split()[0] in {"כל", "פי", "שנה", "שנים", "יום", "ימי"}:
            continue
        heb_tokens = [t for t in author_part.split() if re.search(r"[א-ת]{3,}", t)]
        # Long prose after ל is normally part of the title (W-206). A short
        # name suffix remains eligible when it has no genealogy marker.
        if len(heb_tokens) <= 3 and len(heb_tokens) >= 2:
            return title_part, author_part
    return text, None


# ── Work-title noise detector + cleaner ──────────────────────────────

_FOLIO_RANGE_RE = re.compile(
    r"^(?:דף|folio)\s+\d+[א-ת]?(?:[,\s\-–]+\d+[א-ת]?)*[.,]?\s*$",
    re.IGNORECASE | re.UNICODE,
)

_NOISE_DESCRIPTION_RE = re.compile(
    r"\b(?:ניקוד|טעמים|מסורה|כתובים|פרשיות|פסוקים|הגהות)\b",
    re.UNICODE,
)

_FOLIO_PREFIX_RE = re.compile(
    r"^(?:דף|folio)\s+\d+[א-ת]?(?:[,\s\-–]+\d+[א-ת]?)*\s*[,;:.]?\s*",
    re.IGNORECASE | re.UNICODE,
)


def _is_noise_work_title(title: str) -> bool:
    if not title:
        return True
    stripped = title.strip()
    if _FOLIO_RANGE_RE.match(stripped):
        return True
    heb_count = sum(1 for c in stripped if "א" <= c <= "ת")
    if heb_count < 2:
        return True
    if _NOISE_DESCRIPTION_RE.search(stripped):
        return True
    return False


def _clean_work_title(title: str) -> str:
    return _FOLIO_PREFIX_RE.sub("", title.strip()).strip()


def _associate_item_with_source_record(
    item: WikidataItem,
    source_record: dict[str, object],
) -> None:
    """Keep review metadata tied to the MARC record that created an item."""
    control_number = (
        str(
            source_record.get("_control_number")
            or source_record.get("control_number")
            or source_record.get("controlNumber")
            or ""
        )
        .strip()
        .strip("\"'")
    )
    if control_number:
        item.records = sorted({*item.records, control_number})


# ── Builder ──────────────────────────────────────────────────────────

from converter.wikidata.content_projection import ContentProjectionMixin
from converter.wikidata.manuscript_metadata import ManuscriptMetadataMixin
from converter.wikidata.manuscript_projection import ManuscriptProjectionMixin
from converter.wikidata.person_linking import PersonLinkingMixin
from converter.wikidata.person_projection import PersonProjectionMixin
from converter.wikidata.work_projection import WorkProjectionMixin


class WikidataItemBuilder(
    ManuscriptProjectionMixin,
    ManuscriptMetadataMixin,
    ContentProjectionMixin,
    PersonLinkingMixin,
    PersonProjectionMixin,
    WorkProjectionMixin,
):
    """Build Wikidata item representations from authority-enriched records.

    Covers ALL 53 fields from ExtractedData plus NER entities and
    authority matches. Entity linking uses resolved Wikidata QIDs
    from the reconciliation phase.

    Usage::

        builder = WikidataItemBuilder()
        items = builder.build_all(records)
    """

    def __init__(
        self,
        reconciler: object | None = None,
        hmo_instance_qids: dict[str, str] | None = None,
    ) -> None:
        """Initialize the builder.

        Args:
            reconciler: Optional WikidataReconciler instance. When provided,
                _get_or_create_work() will SPARQL-query Wikidata for an
                existing work item before creating a new one. This catches
                duplicates of classical Hebrew works (Talmud tractates,
                Rashi commentaries, Maimonides, etc.) that already exist
                on Wikidata. Bug fix 2026-04-15 (web audit Fix #2).
                Pass None to disable SPARQL reconciliation (faster offline
                builds; falls back to KNOWN_WORK_QIDS hardcoded mapping).
            hmo_instance_qids: Optional ``control_number -> live HMO
                Wikibase QID`` map (Phase 6 of the HMO Wikibase Studio
                buildout). When a manuscript's control number is present,
                P2888/P973 point at the real ``/wiki/Item:Q<n>`` page
                instead of the static slug URL. Callers with database
                access (``app.pipeline.wikidata_studio``) build this dict
                once per build from ``wikibase_entity_mappings`` — this
                class stays DB-agnostic.
        """
        self._hmo_instance_qids = hmo_instance_qids or {}
        self._person_items: dict[str, WikidataItem] = {}
        self._person_qids: dict[str, str] = {}  # person_key -> resolved Wikidata QID
        # Persons that the notability gate or role-descriptor gate rejected.
        # Their stub WikidataItems are NOT added to _person_items (so they do
        # not flow into build_all's output as empty shells) but we still need
        # to remember the key to avoid re-logging the same warning every time
        # the same MARC 100/700 entry is referenced.
        self._skipped_person_keys: set[str] = set()
        self._skipped_person_stubs: dict[str, WikidataItem] = {}
        self._work_items: dict[str, WikidataItem] = {}
        self._manuscript_items: list[WikidataItem] = []
        self._reconciler = reconciler

    def build_all(
        self,
        records: list[dict[str, object]],
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> list[WikidataItem]:
        """Build all Wikidata items from authority-enriched records.

        Returns manuscripts first, then deduplicated persons.
        """
        self._person_items.clear()
        self._person_qids.clear()
        self._skipped_person_keys.clear()
        self._skipped_person_stubs.clear()
        self._work_items.clear()
        self._manuscript_items.clear()
        total = len(records)

        for idx, record in enumerate(records):
            ms_item = self.build_manuscript_item(record)
            self._manuscript_items.append(ms_item)
            if progress_cb:
                progress_cb(idx + 1, total)

        # Order: works → persons → manuscripts (for __LOCAL: resolution)
        all_items = (
            list(self._work_items.values())
            + list(self._person_items.values())
            + self._manuscript_items
        )
        logger.info(
            "Built %d items: %d manuscripts + %d persons + %d works",
            len(all_items),
            len(self._manuscript_items),
            len(self._person_items),
            len(self._work_items),
        )
        return all_items

    @property
    def person_count(self) -> int:
        """Return the number of unique person items built."""
        return len(self._person_items)

    def apply_reconciliation(self, reconciled: dict[str, str | None]) -> None:
        """Apply reconciliation results — set resolved Wikidata QIDs on persons.

        When a person is found on Wikidata via VIAF/NLI lookup, their
        existing QID is stored so manuscript claims can reference it
        directly (proper LOD wiring instead of local references).

        Also resolves __LOCAL: references in manuscript statements so
        QuickStatements/dry-run exports get proper QIDs too.
        """
        for key, qid in reconciled.items():
            if qid:
                self._person_qids[key] = qid
                if key in self._person_items:
                    self._person_items[key].existing_qid = qid

        # Resolve __LOCAL: references in manuscript statements
        resolved = 0
        for ms_item in self._manuscript_items:
            for stmt in ms_item.statements:
                if isinstance(stmt.value, str) and stmt.value.startswith("__LOCAL:"):
                    local_ref = stmt.value[len("__LOCAL:") :]
                    qid = self._person_qids.get(local_ref)
                    if qid:
                        stmt.value = qid
                        resolved += 1
        if resolved:
            logger.info("Resolved %d __LOCAL: references from reconciliation", resolved)
