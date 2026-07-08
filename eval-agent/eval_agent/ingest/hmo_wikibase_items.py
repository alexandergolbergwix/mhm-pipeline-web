"""Read HMO Wikibase Studio ``hmo_wikibase_items.json`` from disk."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_HMO_CONTROL_NUMBER_RE = re.compile(r"(\d{8,})")


def load(path: Path) -> list[dict[str, Any]]:
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
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    label = labels.get("en") or labels.get("he") or next(iter(labels.values()), "")
    return f"item::{label or index}"


def control_number(item: dict[str, Any]) -> str:
    for key in ("_control_number", "control_number", "record_id", "source_record_id"):
        value = item.get(key)
        if value:
            return str(value)
    for field in (item.get("source_uri"), item.get("local_id"), item.get("_local_id")):
        text = str(field or "")
        match = _HMO_CONTROL_NUMBER_RE.search(text)
        if match:
            return match.group(1)
    return ""


def confidence(item: dict[str, Any]) -> float:
    value = item.get("confidence")
    if isinstance(value, (int, float)):
        return float(value)
    return 1.0


def compact_statements(item: dict[str, Any]) -> list[dict[str, Any]]:
    claims = item.get("claims") or item.get("statements") or []
    out: list[dict[str, Any]] = []
    for stmt in claims:
        if not isinstance(stmt, dict):
            continue
        out.append({
            "property_id": stmt.get("property_id") or stmt.get("property"),
            "datatype": stmt.get("datatype"),
            "value": stmt.get("value"),
        })
    return out[:40]


def _normalise_item(item: dict[str, Any], index: int) -> dict[str, Any]:
    out = dict(item)
    out["_local_id"] = local_id(out, index)
    out.setdefault("local_id", out["_local_id"])
    return out
