"""Read Wikidata Studio ``wikidata_items.json`` from disk.

The file is the eval-agent boundary for Wikidata Studio verification:
each row is a serialized Wikidata item with labels, descriptions,
statements, optional existing QID, validation issues, and a stable
``local_id``/``_local_id``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load(path: Path) -> list[dict[str, Any]]:
    """Read a list-shaped or response-shaped ``wikidata_items.json``."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [_normalise_item(x, i) for i, x in enumerate(raw) if isinstance(x, dict)]
    if isinstance(raw, dict):
        items = raw.get("items")
        if isinstance(items, list):
            return [
                _normalise_item(x, i)
                for i, x in enumerate(items)
                if isinstance(x, dict)
            ]
    return []


def local_id(item: dict[str, Any], index: int) -> str:
    value = item.get("_local_id") or item.get("local_id") or item.get("id")
    if value:
        return str(value)
    entity_type = str(item.get("entity_type") or "item")
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    label = labels.get("en") or labels.get("he") or next(iter(labels.values()), "")
    return f"{entity_type}::{label or index}"


def control_numbers(item: dict[str, Any]) -> list[str]:
    """All parent MARC control numbers for a Wikidata item (deduped, ordered)."""
    seen: set[str] = set()
    out: list[str] = []

    def _add(value: Any) -> None:
        text = str(value or "").strip().strip("\"'")
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for key in ("record_ids", "records", "control_numbers"):
        stored = item.get(key)
        if isinstance(stored, list):
            for value in stored:
                _add(value)
    for key in (
        "_control_number",
        "control_number",
        "record_id",
        "source_record_id",
        "manuscript_id",
    ):
        _add(item.get(key))

    if not out:
        lid = str(item.get("_local_id") or item.get("local_id") or "")
        if item.get("entity_type") == "manuscript" and lid:
            _add(lid)

    if not out:
        for stmt in item.get("statements") or []:
            if not isinstance(stmt, dict):
                continue
            prop = str(stmt.get("property") or stmt.get("property_id") or "")
            value = str(stmt.get("value") or stmt.get("value_id") or "")
            if prop == "P3959" and value:
                _add(value)
            elif value.startswith("__LOCAL:"):
                _add(value.removeprefix("__LOCAL:"))
    return out


def control_number(item: dict[str, Any]) -> str:
    """Best-effort primary parent MARC id for a Wikidata item."""
    cns = control_numbers(item)
    return cns[0] if cns else ""


def confidence(item: dict[str, Any]) -> float:
    """Wikidata items are deterministic projections, so keep all by default."""
    value = item.get("confidence")
    if isinstance(value, (int, float)):
        return float(value)
    return 1.0


def compact_statements(item: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for stmt in item.get("statements") or []:
        if not isinstance(stmt, dict):
            continue
        out.append(
            {
                "property": stmt.get("property") or stmt.get("property_id"),
                "property_label": stmt.get("property_label"),
                "value": stmt.get("value") or stmt.get("value_id"),
                "value_label": stmt.get("value_label"),
                "qualifiers": stmt.get("qualifiers") or [],
                "references": stmt.get("references") or [],
                "rank": stmt.get("rank"),
            }
        )
        if len(out) >= limit:
            break
    return out


def _normalise_item(item: dict[str, Any], index: int) -> dict[str, Any]:
    out = dict(item)
    out["_local_id"] = local_id(out, index)
    return out
