"""Canonical MARC 6XX subject / 655 genre record shape for pipeline consumers."""

from __future__ import annotations

from typing import Any

from converter.rdf.rdf_helpers import clean_marc_label


def subject_term(subj: dict[str, Any] | None) -> str:
    """Return the display term from a subject dict (``term`` or legacy ``name``)."""
    if not isinstance(subj, dict):
        return ""
    raw = subj.get("term") or subj.get("name") or ""
    return clean_marc_label(str(raw).strip())


def normalize_subject_entry(subj: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one subject row; return ``None`` when the term is empty."""
    if not isinstance(subj, dict):
        return None
    term = subject_term(subj)
    if not term:
        return None
    out = dict(subj)
    out["term"] = term
    out["name"] = term
    stype = str(out.get("type") or out.get("kind") or "topic").strip().lower()
    if stype == "corporate":
        stype = "organization"
    if stype == "geographic":
        stype = "place"
    out["type"] = stype
    field = str(out.get("field") or "").strip()
    if not field:
        field = {
            "person": "600",
            "organization": "610",
            "meeting": "611",
            "topic": "650",
            "place": "651",
            "genre": "655",
        }.get(stype, "650")
        out["field"] = field
    for key in ("authority_id", "source", "wikidata_id", "mazal_id", "dates"):
        if key in subj and subj[key]:
            out[key] = subj[key]
    return out


def normalize_subjects_list(subjects: list[Any] | None) -> list[dict[str, Any]]:
    """Drop empty shells and dedupe by (term, type, field)."""
    if not subjects:
        return []
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for raw in subjects:
        if isinstance(raw, str):
            raw = {"name": raw.strip(), "type": "topic", "field": "650"}
        norm = normalize_subject_entry(raw) if isinstance(raw, dict) else None
        if norm is None:
            continue
        key = (
            norm["term"].casefold(),
            str(norm.get("type") or ""),
            str(norm.get("field") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


def normalize_genre_entry(entry: dict[str, Any] | str) -> dict[str, Any] | None:
    """Normalize a 655 genre/form row."""
    if isinstance(entry, str):
        term = clean_marc_label(entry.strip())
        if not term:
            return None
        return {"term": term, "type": "genre", "field": "655"}
    if not isinstance(entry, dict):
        return None
    term = clean_marc_label(str(entry.get("term") or entry.get("name") or "").strip())
    if not term:
        return None
    out = dict(entry)
    out["term"] = term
    out["type"] = "genre"
    out["field"] = "655"
    return out


def genre_terms(genre_entries: list[Any] | None) -> list[str]:
    """Flat genre labels for GraphBuilder / legacy ``genres`` list."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw in genre_entries or []:
        if isinstance(raw, str):
            norm = normalize_genre_entry(raw)
        else:
            norm = normalize_genre_entry(raw)
        if norm is None:
            continue
        t = norm["term"]
        if t.casefold() in seen:
            continue
        seen.add(t.casefold())
        terms.append(t)
    return terms


def normalize_genre_entries(
    genres: list[Any] | None,
    *,
    genre_entries: list[Any] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return ``(flat labels, normalized genre entry dicts)``."""
    merged: list[Any] = list(genre_entries or [])
    for g in genres or []:
        merged.append(g)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in merged:
        norm = normalize_genre_entry(raw) if not isinstance(raw, str) else normalize_genre_entry(raw)
        if norm is None:
            continue
        key = norm["term"].casefold()
        if key in seen:
            continue
        seen.add(key)
        entries.append(norm)
    return genre_terms(entries), entries
