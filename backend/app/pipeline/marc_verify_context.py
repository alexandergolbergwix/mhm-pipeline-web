"""MARC slice helpers for AI-verify cache keys (mirrors eval-agent marc_extract)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import RunRecord

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

HMO_ITEM_MARC_KEYS = [
    "title", "authors", "contributors", "subjects", "provenance",
    "notes", "dates", "place", "related_places", "languages",
    "material", "extent", "shelfmark", "colophon_text", "contents",
]

AUTHORITY_MARC_KEYS = [
    "title", "authors", "contributors", "subjects", "provenance",
    "notes", "dates", "place", "related_places", "colophon_text",
]


def index_marc_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in records:
        rid = rec.get("_control_number") or rec.get("001") or ""
        if rid:
            out[str(rid)] = rec
    return out


def merge_marc_records(
    records: list[dict[str, Any]],
    *,
    primary: dict[str, Any] | None = None,
) -> dict[str, Any]:
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


def project_marc_slice(record: dict[str, Any], keys: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in keys:
        value = record.get(key)
        if value is None or value == "" or value == []:
            continue
        if key == "notes" and isinstance(value, list):
            real = [str(x) for x in value[1:] if x]
            if not real:
                continue
            out[key] = " | ".join(real)
            continue
        if isinstance(value, list):
            out[key] = " | ".join(
                json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)
                for x in value if x
            )
        elif isinstance(value, dict):
            out[key] = json.dumps(value, ensure_ascii=False)
        else:
            out[key] = str(value)
    return out


def _primary_control_number(item: dict[str, Any], control_numbers: list[str]) -> str:
    if not control_numbers:
        return ""
    for field in (item.get("source_uri"), item.get("local_id"), item.get("_local_id")):
        text = str(field or "")
        for cn in control_numbers:
            if cn in text:
                return cn
    return control_numbers[0]


def marc_context_for_item(
    item: dict[str, Any],
    marc_index: dict[str, dict[str, Any]],
    *,
    keys: list[str] | None = None,
) -> dict[str, str]:
    field_keys = keys or HMO_ITEM_MARC_KEYS
    stored = item.get("control_numbers")
    if isinstance(stored, list) and stored:
        control_numbers = sorted({str(x) for x in stored if x})
    else:
        cn = str(item.get("_control_number") or item.get("control_number") or "")
        control_numbers = [cn] if cn else []
    if not control_numbers:
        return {}
    in_run = [cn for cn in control_numbers if cn in marc_index]
    if not in_run:
        return {}
    recs = [marc_index[cn] for cn in in_run]
    if not recs:
        return {}
    primary_cn = _primary_control_number(item, in_run)
    primary = marc_index.get(primary_cn) if primary_cn else None
    merged = merge_marc_records(recs, primary=primary)
    return project_marc_slice(merged, field_keys)


def attach_marc_context(
    items: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> None:
    """Stamp ``_marc_context`` on each item for cache-key construction."""
    marc_index = index_marc_records(marc_records)
    for item in items:
        item["_marc_context"] = marc_context_for_item(item, marc_index)


async def load_run_marc_records(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    return [dict(r.marc or {"_control_number": r.control_number}) for r in records]
