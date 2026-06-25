"""Normalize provenance-NER DATE spans to plain YYYY integers.

The provenance model emits surface forms like ``בשנת 1648``, ``בשנת ב'קל"ז``,
or ``משנת 7[5]16``. Downstream (Wikidata P580, curator review) needs a
four-digit Gregorian year with no surrounding text.

When a prepared MARC record is available, cataloguer brackets (``[=1826]``)
and production dates anchor opaque NLI notations (``7[5]16`` on censored MSS).
"""
from __future__ import annotations

import re
from typing import Any

from app.pipeline.marc_date_sources import _parse_year_scalar, manuscript_production_year
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
# NLI uncertain-digit bracket in catalog / censorship notes: 7[5]16
_NLI_UNCERTAIN_RE = re.compile(r"(?P<left>\d)\[(?P<mid>\d)\](?P<right>\d{2})")
_CENSORSHIP_RE = re.compile(r"צנזור")
# Thousands geresh (ב'קל"ז) — opaque without catalog [=YYYY].
_THOUSANDS_PREFIX_RE = re.compile(r"^[במל]['\u05F3]")


def _strip_year_prefix(text: str) -> str:
    return _YEAR_PREFIX_RE.sub("", text).strip()


def _loosen_marc_quotes(text: str) -> str:
    """Normalise doubled MARC quotes and gershayim marks for substring search."""
    out = text.replace('""', '"')
    out = re.sub(r"[״\u05F4]", '"', out)
    out = re.sub(r"['\u05F3]", "'", out)
    return out


def _marc_text_corpus(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("provenance", "561$a", "500$a", "notes"):
        raw = record.get(key)
        if isinstance(raw, str) and raw.strip():
            parts.append(raw.strip())
        elif isinstance(raw, list):
            parts.extend(str(p).strip() for p in raw if p)
    return " | ".join(parts)


def _marc_bracket_equiv_for_span(
    record: dict[str, Any],
    date_text: str,
) -> int | None:
    """Return ``[=YYYY]`` when the cataloguer annotated the NER span in MARC 561."""
    corpus = _loosen_marc_quotes(_marc_text_corpus(record))
    if not corpus:
        return None
    core = _loosen_marc_quotes(_strip_year_prefix(date_text.strip()))
    if not core:
        return None
    idx = corpus.find(core)
    if idx < 0:
        return None
    window = corpus[idx: idx + len(core) + 24]
    m = _MARC_EQUIV_RE.search(window)
    if m:
        return int(m.group("year"))
    return None


def _marc_censorship_catalog_year(
    record: dict[str, Any],
    date_text: str,
) -> int | None:
    """Anchor NLI ``d[d]dd`` censorship years to catalog production date."""
    if not _NLI_UNCERTAIN_RE.search(date_text):
        return None
    corpus = _marc_text_corpus(record)
    if not _CENSORSHIP_RE.search(corpus):
        return None
    if not _NLI_UNCERTAIN_RE.search(corpus):
        return None
    return manuscript_production_year(record)


def normalize_date_entity_year(
    text: str,
    *,
    marc_record: dict[str, Any] | None = None,
) -> int | None:
    """Return a four-digit Gregorian year for a DATE entity span, or None."""
    raw = str(text or "").strip().strip(".,;:")
    if not raw:
        return None

    m = _MARC_EQUIV_RE.search(raw)
    if m:
        return int(m.group("year"))

    if marc_record is not None:
        bracket = _marc_bracket_equiv_for_span(marc_record, raw)
        if bracket is not None:
            return bracket
        censorship = _marc_censorship_catalog_year(marc_record, raw)
        if censorship is not None:
            return censorship

    core = _strip_year_prefix(raw)

    scalar = _parse_year_scalar(core)
    if scalar is not None:
        return scalar

    gem = _GERSHAYIM_RE.search(core)
    if gem:
        token = gem.group(0)
        if _THOUSANDS_PREFIX_RE.match(token):
            return None
        year = letters_to_gregorian_year(token)
        if year is not None and 100 < year < 2100:
            return year
        return None

    if _NLI_UNCERTAIN_RE.search(core):
        return None

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
    *,
    marc_record: dict[str, Any] | None = None,
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
        year = normalize_date_entity_year(raw, marc_record=marc_record)
        if year is None:
            continue
        normalized = dict(ent)
        normalized["date_text_raw"] = raw
        normalized["text"] = str(year)
        normalized["year"] = year
        out.append(normalized)
    return out
