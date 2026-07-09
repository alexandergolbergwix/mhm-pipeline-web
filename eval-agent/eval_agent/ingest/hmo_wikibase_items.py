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
        items = [_normalise_item(x, i) for i, x in enumerate(raw) if isinstance(x, dict)]
        return enrich_control_numbers(items)
    if isinstance(raw, dict):
        items = raw.get("items")
        if isinstance(items, list):
            normalised = [
                _normalise_item(x, i)
                for i, x in enumerate(items)
                if isinstance(x, dict)
            ]
            return enrich_control_numbers(normalised)
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
    numbers = item.get("control_numbers")
    if isinstance(numbers, list) and numbers:
        return str(numbers[0])
    for field in (item.get("source_uri"), item.get("local_id"), item.get("_local_id")):
        text = str(field or "")
        match = _HMO_CONTROL_NUMBER_RE.search(text)
        if match:
            return match.group(1)
    for link in item.get("deferred_links") or []:
        if not isinstance(link, dict):
            continue
        for key in ("source_local_id", "target_local_id"):
            match = _HMO_CONTROL_NUMBER_RE.search(str(link.get(key) or ""))
            if match:
                return match.group(1)
    return ""


def control_numbers(item: dict[str, Any]) -> list[str]:
    stored = item.get("control_numbers")
    if isinstance(stored, list) and stored:
        return sorted({str(x) for x in stored if x})
    cn = control_number(item)
    return [cn] if cn else []


def primary_control_number(item: dict[str, Any]) -> str:
    """Pick the CN most specific to this item (URI/local_id match), else first."""
    numbers = control_numbers(item)
    if not numbers:
        return ""
    for field in (item.get("source_uri"), item.get("local_id"), item.get("_local_id")):
        text = str(field or "")
        for cn in numbers:
            if cn in text:
                return cn
    return numbers[0]


def enrich_control_numbers(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Propagate manuscript control numbers across deferred-link graphs."""
    cn_by_local_id: dict[str, str] = {}
    for item in items:
        lid = str(item.get("local_id") or item.get("_local_id") or "")
        if not lid:
            continue
        numbers = control_numbers(item)
        if numbers:
            cn_by_local_id[lid] = numbers[0]

    changed = True
    while changed:
        changed = False
        for item in items:
            lid = str(item.get("local_id") or item.get("_local_id") or "")
            if not lid or lid in cn_by_local_id:
                continue
            for link in item.get("deferred_links") or []:
                if not isinstance(link, dict):
                    continue
                src = str(link.get("source_local_id") or "")
                tgt = str(link.get("target_local_id") or "")
                if src in cn_by_local_id:
                    cn_by_local_id[lid] = cn_by_local_id[src]
                    changed = True
                    break
                if tgt in cn_by_local_id:
                    cn_by_local_id[lid] = cn_by_local_id[tgt]
                    changed = True
                    break

    out: list[dict[str, Any]] = []
    for item in items:
        enriched = dict(item)
        lid = str(enriched.get("local_id") or enriched.get("_local_id") or "")
        existing = control_numbers(enriched)
        if not existing and lid in cn_by_local_id:
            existing = [cn_by_local_id[lid]]
        if existing:
            enriched["control_numbers"] = existing
            enriched["_control_number"] = existing[0]
        out.append(enriched)
    return out


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
