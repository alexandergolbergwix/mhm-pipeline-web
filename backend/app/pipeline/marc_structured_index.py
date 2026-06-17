"""Index of MARC structured-field values, per record.

Used by the AI-verification dialog AND the entity-listing endpoint to
classify AI Extraction NER candidates against the manuscript's catalogued
MARC fields:

* ``is_novel(record_id, candidate_text)`` — boolean novelty check.
  Kept for backward compatibility with the desktop dialog port.
* ``classify(control_number, candidate_text, candidate_type=None)`` —
  returns ``{status, fields, note}`` where ``status`` is one of
  ``grounded`` (candidate appears in a MARC field of the *same* kind
  as the candidate's type), ``wrong_field`` (candidate appears in
  MARC but in a different structured field than expected),
  ``novel`` (candidate is not in any MARC field — AI Extraction surfaced
  genuinely new info), or ``unknown`` (we have no MARC record for
  this control_number so we can't decide).

Ported from the desktop pipeline's marc_structured_index.py (CLAUDE.md
Rule 52). The desktop module is the canonical reference for the
normalisation + bidirectional-substring contract; the only new
surface here is :meth:`classify`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


_PUNCT_RE = re.compile(r"[\s,.;:\"'()\[\]{}<>!?\-—\/\\]+")


def _normalise(text: str) -> str:
    """Casefold + collapse punctuation so substring matches are tolerant.

    Hebrew script is preserved verbatim — only Latin case + whitespace +
    common ISBD punctuation are normalised away. The result is suitable
    for substring containment checks.
    """
    if not text:
        return ""
    out = text.strip()
    if not out:
        return ""
    out = out.casefold()
    out = _PUNCT_RE.sub(" ", out).strip()
    return out


def _yield_strings(value: Any) -> Iterable[str]:
    """Walk an arbitrary structure yielding every non-empty string leaf."""
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, (int, float, bool)):
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _yield_strings(v)
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _yield_strings(v)
        return


_STRUCTURED_KEYS: tuple[str, ...] = (
    "contributors",
    "authors",
    "subjects",
    "title",
    "title_variants",
    "uniform_title",
    "alternate_titles",
    "genre_form",
    "genres",
    "acquisition_source",
    "former_owners",
    "ownership_history",
    "series",
    "contents",
    "works",
    "places",
    "related_places",
    # Note-sourced keys (Phase 4 — notes / colophon / work titles searchable)
    "notes",
    "colophon_text",
    "colophon_year",
    "colophon_scribe",
    "work_mentions",
)


_TYPE_TO_FIELDS: dict[str, tuple[str, ...]] = {
    "person":          ("contributors", "authors", "colophon_text", "colophon_scribe"),
    "person_ner":      ("contributors", "authors", "colophon_text", "colophon_scribe"),
    "owner":           ("former_owners", "ownership_history", "acquisition_source"),
    "provenance":      ("former_owners", "ownership_history", "acquisition_source"),
    "provenance_ner":  ("former_owners", "ownership_history", "acquisition_source"),
    "work":            ("title", "title_variants", "uniform_title",
                        "alternate_titles", "contents", "works", "work_mentions",
                        "notes"),
    "work_author":     ("contributors", "authors"),
    "contents_ner":    ("title", "title_variants", "uniform_title",
                        "alternate_titles", "contents", "works", "work_mentions",
                        "notes"),
    "genre":           ("genre_form", "genres"),
    "genre_classifier": ("genre_form", "genres"),
    "place":           ("places", "related_places", "subjects"),
    "collection":      ("former_owners", "ownership_history", "acquisition_source"),
    "date":            ("colophon_year",),
}


def _expected_fields_for(candidate_type: str | None) -> tuple[str, ...]:
    if not candidate_type:
        return ()
    key = str(candidate_type).strip().casefold()
    if not key:
        return ()
    return _TYPE_TO_FIELDS.get(key, ())


def _record_key(record_id: str) -> str:
    raw = str(record_id or "").strip()
    if not raw:
        return ""
    return raw.split("/")[-1]


class MarcStructuredIndex:
    """Per-record bag of normalised structured-field strings."""

    def __init__(self) -> None:
        self._by_id: dict[str, set[str]] = {}
        self._by_id_field: dict[str, dict[str, set[str]]] = {}

    @classmethod
    def load(cls, marc_extracted_path: Path) -> "MarcStructuredIndex":
        index = cls()
        path = Path(marc_extracted_path)
        if not path.exists():
            return index
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return index
        index._ingest_data(data)
        return index

    @classmethod
    def from_records(cls, records: Iterable[dict[str, Any]]) -> "MarcStructuredIndex":
        index = cls()
        index._ingest_data(list(records))
        return index

    def _ingest_data(self, data: Any) -> None:
        records: list[dict[str, Any]]
        if isinstance(data, list):
            records = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            records = [r for r in data.values() if isinstance(r, dict)]
        else:
            return

        for record in records:
            key = _record_key(str(record.get("_control_number") or ""))
            if not key:
                continue
            bag: set[str] = set()
            by_field: dict[str, set[str]] = {}
            for field_key in _STRUCTURED_KEYS:
                if field_key not in record:
                    continue
                field_bag: set[str] = set()
                for raw_str in _yield_strings(record[field_key]):
                    norm = _normalise(raw_str)
                    if not norm:
                        continue
                    bag.add(norm)
                    field_bag.add(norm)
                    if "," in raw_str:
                        for part in raw_str.split(","):
                            part_norm = _normalise(part)
                            if part_norm and len(part_norm) >= 2:
                                bag.add(part_norm)
                                field_bag.add(part_norm)
                if field_bag:
                    by_field[field_key] = field_bag
            if bag:
                self._by_id[key] = bag
                self._by_id_field[key] = by_field

    def __len__(self) -> int:
        return len(self._by_id)

    def has(self, record_id: str) -> bool:
        return _record_key(record_id) in self._by_id

    def is_novel(self, record_id: str, candidate_text: str) -> bool:
        key = _record_key(record_id)
        bag = self._by_id.get(key)
        if not bag:
            return False
        needle = _normalise(candidate_text)
        if not needle:
            return False
        for entry in bag:
            if not entry:
                continue
            if needle in entry or entry in needle:
                return False
        return True

    def classify(
        self,
        control_number: str,
        candidate_text: str,
        candidate_type: str | None = None,
    ) -> dict[str, Any]:
        key = _record_key(control_number)
        if not key or key not in self._by_id_field:
            return {
                "status": "unknown",
                "fields": [],
                "note":   "no MARC record indexed for this control number",
            }
        needle = _normalise(candidate_text)
        if not needle:
            return {
                "status": "unknown",
                "fields": [],
                "note":   "empty candidate text",
            }

        matched_fields: list[str] = []
        for field_key, field_bag in self._by_id_field[key].items():
            for entry in field_bag:
                if not entry:
                    continue
                if needle in entry or entry in needle:
                    matched_fields.append(field_key)
                    break

        if not matched_fields:
            return {
                "status": "novel",
                "fields": [],
                "note":   "not present in any catalogued MARC field",
            }

        expected = set(_expected_fields_for(candidate_type))
        if not expected:
            return {
                "status": "grounded",
                "fields": matched_fields,
                "note":   f"matched in {', '.join(matched_fields)}",
            }

        in_expected = [f for f in matched_fields if f in expected]
        if in_expected:
            return {
                "status": "grounded",
                "fields": in_expected,
                "note":   f"matched in expected field(s): {', '.join(in_expected)}",
            }
        return {
            "status": "wrong_field",
            "fields": matched_fields,
            "note":   (
                f"matched in {', '.join(matched_fields)} but expected "
                f"one of {', '.join(sorted(expected))}"
            ),
        }


__all__ = ["MarcStructuredIndex"]
