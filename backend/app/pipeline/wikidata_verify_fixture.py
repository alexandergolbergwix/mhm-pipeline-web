"""Lean Wikidata verify fixtures for eval-agent (Rule W-131)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.pipeline.marc_verify_context import canonical_control_number
from app.pipeline.wikidata_verdict_cache import record_ids_for_wikidata_item

_STATEMENT_KEYS = (
    "property", "property_id", "property_label",
    "value", "value_id", "value_type", "value_label", "rank",
)
_FIXTURE_ITEM_KEYS = (
    "_local_id", "local_id", "entity_type", "semantic_type",
    "labels", "descriptions", "aliases", "statements", "statement_count",
    "existing_qid", "hmo_wikibase_id", "source_uri", "validation_issues",
    "record_ids", "records", "verify_evidence",
)


def _compact_statement(stmt: dict[str, Any]) -> dict[str, Any]:
    return {
        key: stmt.get(key)
        for key in _STATEMENT_KEYS
        if stmt.get(key) not in (None, "")
    }


def compact_statements(item: dict[str, Any], *, limit: int = 40) -> list[dict[str, Any]]:
    statements = item.get("statements")
    if not isinstance(statements, list):
        return []
    out: list[dict[str, Any]] = []
    for stmt in statements[:limit]:
        if isinstance(stmt, dict):
            compact = _compact_statement(stmt)
            if compact:
                out.append(compact)
    return out


def _slim_verify_evidence(pack: dict[str, Any]) -> dict[str, Any]:
    slim = dict(pack)
    slim.pop("marc", None)
    return slim


def compact_wikidata_verify_fixture_item(item: dict[str, Any]) -> dict[str, Any]:
    local_id = str(item.get("_local_id") or item.get("local_id") or "")
    row: dict[str, Any] = {
        "_local_id": local_id,
        "local_id": local_id,
        "entity_type": item.get("entity_type"),
        "semantic_type": item.get("semantic_type"),
        "labels": item.get("labels") or {},
        "descriptions": item.get("descriptions") or {},
        "aliases": item.get("aliases") or {},
        "statements": compact_statements(item),
        "statement_count": len(item.get("statements") or []),
        "existing_qid": item.get("existing_qid"),
        "hmo_wikibase_id": item.get("hmo_wikibase_id"),
        "source_uri": item.get("source_uri"),
        "validation_issues": item.get("validation_issues") or [],
        "record_ids": record_ids_for_wikidata_item(item),
    }
    evidence = item.get("verify_evidence")
    if isinstance(evidence, dict):
        row["verify_evidence"] = _slim_verify_evidence(evidence)
    return {key: row[key] for key in _FIXTURE_ITEM_KEYS if key in row}


def scope_marc_records_for_items(
    items: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    wanted: set[str] = set()
    for item in items:
        for cn in record_ids_for_wikidata_item(item):
            canon = canonical_control_number(cn)
            if canon:
                wanted.add(canon)
    if not wanted:
        return []
    scoped: list[dict[str, Any]] = []
    for record in marc_records:
        cn = canonical_control_number(
            record.get("_control_number") or record.get("control_number"),
        )
        if cn and cn in wanted:
            scoped.append(record)
    return scoped


def write_wikidata_verify_fixture(
    *,
    dest_dir: Path,
    marc_records: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    compact_items = [compact_wikidata_verify_fixture_item(item) for item in items]
    scoped_marc = scope_marc_records_for_items(compact_items, marc_records)
    (dest_dir / "marc_extracted.json").write_text(
        json.dumps(scoped_marc, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (dest_dir / "wikidata_items.json").write_text(
        json.dumps(compact_items, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def compact_wikidata_verdict_candidate(item: dict[str, Any], *, label: str) -> dict[str, Any]:
    local_id = str(item.get("_local_id") or item.get("local_id") or "")
    cand: dict[str, Any] = {
        "_local_id": local_id,
        "_item_id": local_id,
        "local_id": local_id,
        "label": label,
        "entity_type": item.get("entity_type"),
        "existing_qid": item.get("existing_qid"),
    }
    return {
        key: value
        for key, value in cand.items()
        if value not in (None, "")
    }
