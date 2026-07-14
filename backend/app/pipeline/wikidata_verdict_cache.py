"""Content-addressed cache keys for Wikidata Studio item AI verdicts."""

from __future__ import annotations

from typing import Any

from app.pipeline.ai_verdict_cache_common import (
    normalise_shacl_issues,
    sanitise_stored_verdict,
)
from app.pipeline.inference_cache import canonical_hash
from app.pipeline.marc_verify_context import (
    index_marc_records,
    marc_context_for_item,
)

WIKIDATA_VERDICT_SCHEMA = "w71_v1"
WIKIDATA_VERDICT_KEY_VERSION = "records_marc_v5"


def _normalise_prompt_statements(statements: Any) -> list[dict[str, Any]]:
    """Keep every statement field that is rendered into the judge prompt."""
    if not isinstance(statements, list):
        return []
    rows: list[dict[str, Any]] = []
    for statement in statements:
        if not isinstance(statement, dict):
            continue
        rows.append({
            "property": str(
                statement.get("property") or statement.get("property_id") or ""
            ),
            "property_label": str(statement.get("property_label") or ""),
            "value_type": str(
                statement.get("value_type") or statement.get("datatype") or ""
            ),
            "value": statement.get("value"),
            "value_label": str(statement.get("value_label") or ""),
            "qualifiers": statement.get("qualifiers") or [],
            "references": statement.get("references") or [],
        })
    return sorted(
        rows,
        key=lambda row: (row["property"], row["value_type"], str(row["value"])),
    )


def record_ids_for_wikidata_item(item: dict[str, Any]) -> list[str]:
    """Return explicit source records, or recover them from P3959 references."""
    record_ids: set[str] = set()
    for key in ("record_ids", "records"):
        stored = item.get(key)
        if isinstance(stored, list):
            record_ids.update(str(value).strip().strip("\"") for value in stored if value)
    for key in ("control_number", "_control_number", "record_id"):
        value = str(item.get(key) or "").strip().strip("\"")
        if value:
            record_ids.add(value)
    for statement in item.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        for reference in statement.get("references") or []:
            if not isinstance(reference, dict):
                continue
            snaks = reference.get("snaks")
            rows = snaks if isinstance(snaks, list) else [reference]
            for snak in rows:
                if not isinstance(snak, dict):
                    continue
                prop = snak.get("property") or snak.get("property_id")
                value = str(snak.get("value") or "").strip().strip("\"")
                if prop == "P3959" and value:
                    record_ids.add(value)
    return sorted(record_ids)


def _record_ids(item: dict[str, Any]) -> list[str]:
    return record_ids_for_wikidata_item(item)


def attach_local_reference_targets(items: list[dict[str, Any]]) -> None:
    """Attach evidence for every ``__LOCAL`` statement target in one item set."""
    by_local_id = {
        str(item.get("_local_id") or item.get("local_id") or ""): item
        for item in items
    }
    for item in items:
        targets: dict[str, dict[str, Any]] = {}
        for statement in item.get("statements") or []:
            if not isinstance(statement, dict):
                continue
            values: list[Any] = [statement.get("value"), statement.get("value_id")]
            values.extend(
                qualifier.get("value")
                for qualifier in statement.get("qualifiers") or []
                if isinstance(qualifier, dict)
            )
            for value in values:
                text = str(value or "")
                if not text.startswith("__LOCAL:"):
                    continue
                target_id = text.removeprefix("__LOCAL:")
                target = by_local_id.get(target_id)
                if target is None:
                    continue
                labels = target.get("labels")
                targets[target_id] = {
                    "entity_type": target.get("entity_type"),
                    "labels": labels if isinstance(labels, dict) else {},
                    "existing_qid": target.get("existing_qid"),
                    "authority_evidence": target.get("authority_evidence") or [],
                }
        if targets:
            item["local_reference_targets"] = targets
        else:
            item.pop("local_reference_targets", None)


def marc_context_for_wikidata_item(
    item: dict[str, Any],
    marc_records: list[dict[str, Any]],
) -> dict[str, str]:
    index = index_marc_records(marc_records)
    return marc_context_for_item(
        {"control_numbers": _record_ids(item), "record_ids": _record_ids(item)},
        index,
    )


def attach_wikidata_marc_context(
    items: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> None:
    """Attach the same MARC slice used by Wikidata verdict cache keys."""
    for item in items:
        item["_marc_context"] = marc_context_for_wikidata_item(item, marc_records)


def wikidata_verdict_query_summary(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "wikidata_item",
    marc_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    marc_slice = marc_context
    if marc_slice is None:
        raw = item.get("_marc_context")
        marc_slice = raw if isinstance(raw, dict) else {}

    summary: dict[str, Any] = {
        "local_id": str(item.get("_local_id") or item.get("local_id") or ""),
        "entity_type": str(item.get("entity_type") or ""),
        "record_ids": _record_ids(item),
        "labels": item.get("labels") or {},
        "descriptions": item.get("descriptions") or {},
        "aliases": item.get("aliases") or {},
        "statements": _normalise_prompt_statements(item.get("statements") or []),
        "existing_qid": item.get("existing_qid"),
        "validation_issues": normalise_shacl_issues(item.get("validation_issues") or []),
        "authority_evidence": item.get("authority_evidence") or [],
        "work_candidate_evidence": item.get("work_candidate_evidence") or {},
        "local_reference_targets": item.get("local_reference_targets") or {},
        "marc_context": marc_slice,
        "judge_model": judge_model,
        "evaluator": evaluator,
        "wikidata_verdict_schema": WIKIDATA_VERDICT_SCHEMA,
    }
    if evaluator == "wikidata_autofix":
        live = item.get("wikidata_live")
        if isinstance(live, dict):
            summary["wikidata_live_fingerprint"] = {
                "conflict_count": live.get("conflict_count"),
                "row_count": len(live.get("rows") or []),
                "qid": live.get("qid"),
            }
    return summary


def wikidata_verdict_input_fingerprint(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "wikidata_item",
    marc_context: dict[str, str] | None = None,
) -> str:
    return canonical_hash(
        wikidata_verdict_query_summary(
            item,
            judge_model,
            evaluator=evaluator,
            marc_context=marc_context,
        ),
    )


def wikidata_verdict_judge_model(ai_verdict: dict[str, Any] | None) -> str:
    if not ai_verdict:
        return "gemini-3.5-flash"
    return str(ai_verdict.get("model") or "gemini-3.5-flash")


def sanitise_stale_wikidata_verdict(
    item: dict[str, Any],
    stored: dict[str, Any] | None,
    *,
    judge_model: str | None = None,
    evaluator: str = "wikidata_item",
    marc_context: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(stored, dict) or not stored:
        return None
    model = judge_model or wikidata_verdict_judge_model(stored)
    eval_id = str(stored.get("evaluator") or evaluator)
    expected = wikidata_verdict_input_fingerprint(
        item,
        model,
        evaluator=eval_id,
        marc_context=marc_context,
    )
    current = sanitise_stored_verdict(stored, expected_fingerprint=expected)
    if current is not None:
        return current
    if stored.get("cache_key_version"):
        return None

    legacy_item = {**item, "record_ids": _record_ids(item)}
    legacy = sanitise_stored_verdict(
        stored,
        expected_fingerprint=wikidata_verdict_input_fingerprint(
            legacy_item,
            model,
            evaluator=eval_id,
            marc_context={},
        ),
    )
    if legacy is None:
        return None
    return {
        **legacy,
        "cache_key": expected,
        "cache_key_version": WIKIDATA_VERDICT_KEY_VERSION,
    }
