"""Read MHM-Pipeline ``marc_extracted.json`` from disk.

Stage 1 of the pipeline emits a post-processed, semantic-key dict per
record. We never re-derive these; we treat them as the contract.

Public surface
--------------
``load(path)`` — read + return ``list[dict]``.
``index_by_id(records)`` — return ``{record_id: record}`` for fast lookup.
``project(record, keys)`` — pick a subset of keys, coerced to human-readable
strings (lists of dicts → ``|``-joined JSON, dicts → JSON, etc.).

The ``notes`` key gets special treatment: pipeline Stage 1 prepends
the source filename as ``notes[0]``; we strip it because Gemini
mistakes filenames for content.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load(path: Path) -> list[dict[str, Any]]:
    """Read ``marc_extracted.json``."""
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_control_number(value: Any) -> str:
    """Normalise a control number for joining.

    Stage-1 sometimes persists ``_control_number`` with literal surrounding
    quote characters (``"990…"``) while item/candidate control numbers are the
    clean digit string. Keying and looking up on the raw quoted value silently
    misses the join and leaves the judge with no MARC context. Canonicalising
    both sides (strip surrounding quotes + whitespace) makes the join robust.
    """
    return str(value or "").strip().strip("\"'").strip()


def index_by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return ``{canonical_control_number: record}``. Empty-id records skipped."""
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        rid = canonical_control_number(r.get("_control_number") or r.get("001") or "")
        if rid:
            out[rid] = r
    return out


_LIST_MERGE_KEYS = frozenset({
    "authors",
    "contributors",
    "subjects",
    "contents",
    "notes",
    "related_places",
    "languages",
    "genres",
    "materials",
})

_SCALAR_KEYS = frozenset({
    "title",
    "shelfmark",
    "extent",
    "material",
    "provenance",
    "place",
    "colophon_text",
    "dates",
})


def merge_records(
    records: list[dict[str, Any]],
    *,
    primary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Union list fields across linked manuscripts; scalars from the primary record."""
    if not records:
        return {}
    if len(records) == 1:
        return dict(records[0])
    base = dict(primary or records[0])
    for rec in records:
        for key, value in rec.items():
            if key in _LIST_MERGE_KEYS and isinstance(value, list):
                existing = base.get(key)
                if not isinstance(existing, list):
                    existing = []
                    base[key] = existing
                for item in value:
                    if item not in existing:
                        existing.append(item)
            elif key in _SCALAR_KEYS:
                continue
            elif key != "_control_number" and key not in base:
                base[key] = value
    return base


def project_many(
    index: dict[str, dict[str, Any]],
    control_numbers: list[str],
    keys: list[str],
    *,
    primary_cn: str | None = None,
) -> dict[str, str]:
    """Project a merged MARC view across all linked control numbers."""
    recs = [index[cn] for cn in control_numbers if cn in index]
    if not recs:
        return {}
    primary = index.get(primary_cn or "") if primary_cn else None
    merged = merge_records(recs, primary=primary)
    return project(merged, keys)


# Raw collapsed MARC tags behind each slice name — byte-mirror of
# backend/app/pipeline/marc_verify_context.py::RAW_TAG_FALLBACK.
RAW_TAG_FALLBACK: dict[str, tuple[str, ...]] = {
    "dates": ("008", "260$c", "264$c", "046$a", "046$b"),
    "title": ("245$a", "245$b", "245$c"),
    "variant_titles": ("246$a", "246$b"),
    "place": ("260$a", "264$a", "751$a"),
    "extent": ("300$a", "300$b", "300$c"),
    "material": ("340$a", "340$e"),
    "carrier": ("336$a", "337$a", "338$a"),
    "notes": ("500$a", "590$a", "597$a"),
    "contents": ("505$a", "505$t"),
    "summary": ("520$a",),
    "languages": ("041$a", "546$a"),
    "rights": ("540$a", "540$u", "939$a", "939$u"),
    "provenance": ("541$a", "541$b", "561$a", "563$a", "583$a"),
    "subjects": ("650$a", "651$a", "600$a", "610$a"),
    "genres": ("655$a",),
    "authors": ("100$a", "100$d", "110$a", "111$a"),
    "contributors": ("700$a", "700$d", "710$a", "711$a"),
    "shelfmark": ("852$j", "852$h", "852$c", "952$a", "952$b", "952$c", "952$d"),
    "digital_access": ("856$u", "966$a", "966$9"),
    "related_records": ("773$a", "774$a", "787$a"),
}

def _raw_tag_values(record: dict[str, Any], tags: tuple[str, ...]) -> str:
    parts: list[str] = []
    for tag in tags:
        value = record.get(tag)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            rendered = " ; ".join(
                json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)
                for x in value if x not in (None, "")
            )
        elif isinstance(value, dict):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        rendered = rendered.strip()
        if rendered:
            parts.append(f"{tag}: {rendered}")
    return " | ".join(parts)


def raw_tag_slice(
    record: dict[str, Any],
    *,
    skip: set[str] | frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Project raw collapsed MARC tags (mirror of the backend helper)."""
    out: dict[str, str] = {}
    for name, tags in RAW_TAG_FALLBACK.items():
        if name in skip:
            continue
        rendered = _raw_tag_values(record, tags)
        if rendered:
            out[name] = rendered
    return out


def project(record: dict[str, Any], keys: list[str]) -> dict[str, str]:
    """Pick keys from a record and coerce values to single-line strings.

    Reusable across evaluators. Each evaluator declares its own ``keys``
    list — that's the per-evaluator MARC slice (context engineering).

    Records ingested from TSV/collapsed-key sources carry only raw
    ``NNN$x`` keys for most fields, so after the semantic pass we fill any
    still-missing slice name from ``RAW_TAG_FALLBACK``. Without it the judge
    sees title/authors/contributors/subjects only and every date, extent,
    shelfmark, rights and note claim reads as unsupported (Rule W-137).
    """
    out: dict[str, str] = {}
    for k in keys:
        v = record.get(k)
        if v is None or v == "" or v == []:
            continue
        if k == "notes" and isinstance(v, list):
            # First element is the source filename (a pipeline marker); skip.
            real = [str(x) for x in v[1:] if x]
            if not real:
                continue
            out[k] = " | ".join(real)
            continue
        if isinstance(v, list):
            out[k] = " | ".join(
                json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)
                for x in v if x
            )
        elif isinstance(v, dict):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = str(v)
    for name, rendered in raw_tag_slice(record, skip=set(out)).items():
        out[name] = rendered
    return out
