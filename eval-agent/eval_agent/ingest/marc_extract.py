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


def index_by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return ``{_control_number: record}``. Empty-id records skipped."""
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        rid = r.get("_control_number") or r.get("001") or ""
        if rid:
            out[str(rid)] = r
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


def project(record: dict[str, Any], keys: list[str]) -> dict[str, str]:
    """Pick keys from a record and coerce values to single-line strings.

    Reusable across evaluators. Each evaluator declares its own ``keys``
    list — that's the per-evaluator MARC slice (context engineering).
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
    return out
