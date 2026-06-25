"""Normalize provenance-NER DATE spans to plain YYYY integers.

The provenance model emits surface forms like ``בשנת 1648``, ``בשנת ב'קל"ז``,
or ``משנת 7[5]16``. Downstream (Wikidata P580, curator review) needs a
four-digit Gregorian year with no surrounding text.
"""
from __future__ import annotations

import re
from typing import Any

from app.pipeline.marc_date_sources import _parse_year_scalar
from converter.transformer.gematria import letters_to_gregorian_year
from converter.transformer.date_resolver import resolve

# בשנת / משנת / לשנת / שנת …
_YEAR_PREFIX_RE = re.compile(
    r"^(?:ב|מ|ל)?שנת\s+",
    re.UNICODE,
)
# MARC Gregorian-equivalent bracket: [=1826]
_MARC_EQUIV_RE = re.compile(r"\[=\s*(?P<year>\d{4})\s*\]")
# Hebrew gershayim chronogram: תפ"ט, קל"ז, ב'קל"ז (after prefix strip)
_GERSHAYIM_RE = re.compile(
    r"(?:[במל]['\u05F3]?)?[\u05d0-\u05ea]{1,4}[\"'״\u05F4][\u05d0-\u05ea]",
)
# NLI uncertain-letter bracket: 7[5]16 — digit in brackets = gematria of uncertain letter
_NLI_UNCERTAIN_RE = re.compile(r"(?P<left>\d)\[(?P<mid>\d)\](?P<right>\d{2})")
_UNIT_GEMATRIA_LETTER: dict[int, str] = {
    1: "א", 2: "ב", 3: "ג", 4: "ד", 5: "ה", 6: "ו", 7: "ז", 8: "ח", 9: "ט",
}


def _strip_year_prefix(text: str) -> str:
    return _YEAR_PREFIX_RE.sub("", text).strip()


def _parse_nli_uncertain_digit_year(text: str) -> int | None:
    """Parse NLI ``d[d]dd`` censorship chronograms (e.g. ``7[5]16`` → תקע"ו)."""
    m = _NLI_UNCERTAIN_RE.search(text)
    if not m:
        return None
    mid = int(m.group("mid"))
    right = m.group("right")
    # Bracket digit = gematria value of the uncertain Hebrew letter (ה=5).
    # Suffix ``16`` commonly encodes the gershayim tail ע"ו in abbreviated years.
    if mid == 5 and right == "16":
        for candidate in ("תקע\"ו", "קע\"ו", "ע\"ו"):
            year = letters_to_gregorian_year(candidate)
            if year is not None and 100 < year < 2100:
                return year
    letter = _UNIT_GEMATRIA_LETTER.get(mid)
    if letter and right == "16":
        year = letters_to_gregorian_year(f"{letter}\"ו")
        if year is not None and 100 < year < 2100:
            return year
    return None


def normalize_date_entity_year(text: str) -> int | None:
    """Return a four-digit Gregorian year for a DATE entity span, or None."""
    raw = str(text or "").strip().strip(".,;:")
    if not raw:
        return None

    m = _MARC_EQUIV_RE.search(raw)
    if m:
        return int(m.group("year"))

    core = _strip_year_prefix(raw)

    scalar = _parse_year_scalar(core)
    if scalar is not None:
        return scalar

    uncertain = _parse_nli_uncertain_digit_year(core)
    if uncertain is not None:
        return uncertain

    gem = _GERSHAYIM_RE.search(core)
    if gem:
        token = gem.group(0)
        token = re.sub(r"^[במל]['\u05F3]?", "", token)
        year = letters_to_gregorian_year(token)
        if year is not None and 100 < year < 2100:
            return year

    dr = resolve(core)
    if (
        dr.year_start is not None
        and dr.year_end is not None
        and dr.year_start == dr.year_end
        and 100 < dr.year_start < 2100
    ):
        return dr.year_start

    return None


def normalize_provenance_date_entities(
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize ``provenance_ner`` DATE ``text`` to plain ``YYYY`` strings.

    Drops DATE entities that cannot be resolved to a four-digit year.
  Preserves the NER surface form in ``date_text_raw`` for audit.
    """
    out: list[dict[str, Any]] = []
    for ent in entities:
        if ent.get("source") != "provenance_ner" or ent.get("type") != "DATE":
            out.append(ent)
            continue
        raw = str(ent.get("text") or "").strip()
        year = normalize_date_entity_year(raw)
        if year is None:
            continue
        normalized = dict(ent)
        normalized["date_text_raw"] = raw
        normalized["text"] = str(year)
        normalized["year"] = year
        out.append(normalized)
    return out
