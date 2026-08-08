"""Lean Wikidata verify fixtures for eval-agent (Rule W-131)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.pipeline.marc_verify_context import canonical_control_number
from app.pipeline.wikidata_duplicate_probe import duplicate_status_for_item
from app.pipeline.wikidata_verdict_cache import (
    FINGERPRINT_STATEMENT_LIMIT,
    fingerprint_verify_evidence,
    fixture_statements,
    judge_evidence_projection,
    record_ids_for_wikidata_item,
)
_FIXTURE_ITEM_KEYS = (
    "_local_id", "local_id", "entity_type", "semantic_type",
    "labels", "descriptions", "aliases", "statements", "statement_count",
    "existing_qid", "hmo_wikibase_id", "source_uri", "validation_issues",
    "record_ids", "records", "verify_evidence",
)


def compact_statements(
    item: dict[str, Any],
    *,
    limit: int = FINGERPRINT_STATEMENT_LIMIT,
) -> list[dict[str, Any]]:
    """Judge-facing statement rows (keeps value_label; Rule W-80 / W-175)."""
    return fixture_statements(item, limit=limit)


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
        # The judge projection, not the fingerprint one — the rubric asks about
        # `duplicate_check` and `llm_proposals`, so it must be shown them
        # (Rule W-156).
        row["verify_evidence"] = judge_evidence_projection(item)
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


def slim_item_for_verdict_persist(item: dict[str, Any]) -> dict[str, Any]:
    """Retain only fields needed for override/cache fingerprint writes."""
    local_id = str(item.get("_local_id") or item.get("local_id") or "")
    marc_ctx = item.get("_marc_context")
    slim_evidence = fingerprint_verify_evidence(item)
    row: dict[str, Any] = {
        "_local_id": local_id,
        "local_id": local_id,
        "entity_type": item.get("entity_type"),
        "semantic_type": item.get("semantic_type"),
        "labels": item.get("labels") or {},
        "descriptions": item.get("descriptions") or {},
        "aliases": item.get("aliases") or {},
        "statements": compact_statements(item, limit=40),
        "existing_qid": item.get("existing_qid"),
        "hmo_wikibase_id": item.get("hmo_wikibase_id"),
        "source_uri": item.get("source_uri"),
        "validation_issues": item.get("validation_issues") or [],
        "record_ids": record_ids_for_wikidata_item(item),
        "authority_evidence": item.get("authority_evidence") or [],
        "work_candidate_evidence": item.get("work_candidate_evidence") or {},
        "local_reference_targets": item.get("local_reference_targets") or {},
        "verify_evidence": slim_evidence,
        "_marc_context": marc_ctx if isinstance(marc_ctx, dict) else {},
        # `fingerprint_verify_evidence` strips `duplicate_check`, so the slim item
        # cannot answer "what did the probe say?" any more. Persist keys off this
        # instead of the raw payload (Rule W-157).
        "_duplicate_status": duplicate_status_for_item(item),
    }
    live = item.get("wikidata_live")
    if isinstance(live, dict):
        row["wikidata_live"] = live
    return row


def release_wikidata_verify_heap(
    *,
    items: list[dict[str, Any]],
    items_by_id: dict[str, dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> None:
    """Drop full Studio payloads after the eval-agent fixture is on disk."""
    slimmed = {
        lid: slim_item_for_verdict_persist(it)
        for lid, it in items_by_id.items()
        if lid
    }
    items_by_id.clear()
    items_by_id.update(slimmed)
    for idx, item in enumerate(items):
        lid = str(item.get("_local_id") or item.get("local_id") or "")
        if lid in slimmed:
            items[idx] = slimmed[lid]
    marc_records.clear()


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
