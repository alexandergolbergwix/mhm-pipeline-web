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
    "canonical_references",
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
    # P921 (main subject) is derived from these as well as from 650/600 — a
    # canonical Bible/Talmud citation is a different source from a subject
    # heading, and a claim must cite the channel it actually came from
    # (Rule W-162).
    "canonical_references",
]

AUTHORITY_MARC_KEYS = [
    "title", "authors", "contributors", "subjects", "provenance",
    "notes", "dates", "place", "related_places", "colophon_text",
]

# Raw collapsed MARC tags that back the claims we project, keyed by the slice
# name the judge sees. Older runs (and every TSV/collapsed-key ingest) store
# ONLY raw `NNN$x` keys plus a handful of normalised ones, so a slice built
# from normalised names alone shows the judge title/authors/contributors/
# subjects and nothing else — every date, extent, shelfmark, note, holder and
# rights claim then reads as "unsupported by MARC" (Rule W-137).
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
    "rights": ("540$a", "540$u", "939$a", "939$u", "952$a", "952$b"),
    "provenance": ("541$a", "541$b", "561$a", "563$a", "583$a"),
    "subjects": ("650$a", "651$a", "600$a", "610$a"),
    "canonical_references": ("630$a", "730$a", "830$a"),
    "genres": ("655$a",),
    "authors": ("100$a", "100$d", "110$a", "111$a"),
    "contributors": ("700$a", "700$d", "710$a", "711$a"),
    # 090/099 carry the NLI shelfmark ("F 3238"); 852 is the standard holdings
    # field. 952$a–d are NOT shelfmark data in this corpus — they hold rights,
    # audit and export notes ("Public domain; Contract", a cataloguer name),
    # and mapping them here made the judge see rights text where it expected a
    # shelfmark, so every work's cited attestation looked unverifiable
    # (Rule W-138 follow-up).
    "shelfmark": ("090$a", "099$a", "852$j", "852$h", "852$c"),
    "digital_access": ("856$u", "966$a", "966$9"),
    "related_records": ("773$a", "774$a", "787$a"),
}


def canonical_control_number(value: Any) -> str:
    """Strip surrounding quotes/whitespace so a persisted ``"990…"`` joins a
    clean ``990…`` control number (mirror of eval-agent marc_extract)."""
    return str(value or "").strip().strip("\"'").strip()


def index_marc_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in records:
        rid = canonical_control_number(rec.get("_control_number") or rec.get("001") or "")
        if rid:
            out[rid] = rec
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


def _raw_tag_values(record: dict[str, Any], tags: tuple[str, ...]) -> str:
    """Join the non-empty raw `NNN$x` values behind one slice name."""
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
    """Project raw collapsed MARC tags for the slice names in ``RAW_TAG_FALLBACK``.

    Only fills names the normalised projection could not supply, so a modern
    run (normalised keys present) is byte-identical to before.
    """
    out: dict[str, str] = {}
    for name, tags in RAW_TAG_FALLBACK.items():
        if name in skip:
            continue
        rendered = _raw_tag_values(record, tags)
        if rendered:
            out[name] = rendered
    return out


def project_marc_slice(
    record: dict[str, Any],
    keys: list[str],
    *,
    include_raw_tags: bool = True,
) -> dict[str, str]:
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
    if include_raw_tags:
        for name, rendered in raw_tag_slice(record, skip=set(out)).items():
            out[name] = rendered
    return out


def primary_control_number_for(
    control_numbers: list[str] | tuple[str, ...],
    *identity_fields: Any,
) -> str:
    """The control number this entity *is about*, not merely linked to.

    HMO propagates linked control numbers onto derived nodes (Rule W-48), so a
    manuscript can carry several. Identity — labels, shelfmark, dates, the
    legacy MARC join — must use the CN embedded in its own source URI / local
    id; the rest are context only (Rule W-137).
    """
    cns = [canonical_control_number(cn) for cn in control_numbers or []]
    cns = [cn for cn in cns if cn]
    if not cns:
        return ""
    for field in identity_fields:
        text = str(field or "")
        for cn in cns:
            if cn and cn in text:
                return cn
    return cns[0]


def _primary_control_number(item: dict[str, Any], control_numbers: list[str]) -> str:
    return primary_control_number_for(
        control_numbers,
        item.get("source_uri"),
        item.get("local_id"),
        item.get("_local_id"),
    )


def marc_context_for_item(
    item: dict[str, Any],
    marc_index: dict[str, dict[str, Any]],
    *,
    keys: list[str] | None = None,
) -> dict[str, str]:
    field_keys = keys or HMO_ITEM_MARC_KEYS
    stored = item.get("control_numbers")
    if isinstance(stored, list) and stored:
        control_numbers = sorted({canonical_control_number(x) for x in stored if x})
    else:
        cn = canonical_control_number(item.get("_control_number") or item.get("control_number") or "")
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


async def load_run_control_numbers(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> set[str]:
    """Lightweight CN set for Studio joins — no MARC JSONB."""
    rows = (
        await db.execute(
            select(RunRecord.control_number).where(RunRecord.run_id == run_id),
        )
    ).scalars().all()
    out = {canonical_control_number(cn) for cn in rows}
    out.discard("")
    return out


async def load_run_marc_records_scoped(
    db: AsyncSession,
    run_id: uuid.UUID,
    control_numbers: set[str] | frozenset[str],
) -> list[dict[str, Any]]:
    """Load MARC only for control numbers in scope (quoted DB keys normalised)."""
    wanted = {canonical_control_number(cn) for cn in control_numbers}
    wanted.discard("")
    if not wanted:
        return []
    rows = (
        await db.execute(
            select(RunRecord.control_number, RunRecord.marc).where(
                RunRecord.run_id == run_id,
            ).order_by(RunRecord.control_number.asc()),
        )
    ).all()
    out: list[dict[str, Any]] = []
    for cn, marc in rows:
        canon = canonical_control_number(cn)
        if canon not in wanted:
            continue
        rec = dict(marc or {})
        rec["_control_number"] = canon
        out.append(rec)
    return out


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
