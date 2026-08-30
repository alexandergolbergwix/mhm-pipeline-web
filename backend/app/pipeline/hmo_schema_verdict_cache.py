"""Content-addressed cache keys for HMO Wikibase schema AI verdicts."""

from __future__ import annotations

from typing import Any

from app.pipeline.ai_verdict_cache_common import (
    normalise_public_verdict,
    normalise_string_list,
    sanitise_stored_verdict,
)
from app.pipeline.inference_cache import canonical_hash

HMO_SCHEMA_VERDICT_SCHEMA = "w50_v1"


def schema_verdict_query_summary(
    entry: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "hmo_wikibase_schema",
) -> dict[str, Any]:
    return {
        "ontology_uri": str(entry.get("ontology_uri") or ""),
        "entity_kind": str(entry.get("entity_kind") or ""),
        "label": str(entry.get("label") or ""),
        "description": str(entry.get("description") or ""),
        "datatype": entry.get("datatype"),
        "property_kind": entry.get("property_kind"),
        "range_uri": entry.get("range_uri"),
        "parent_uri": entry.get("parent_uri"),
        "aliases": normalise_string_list(entry.get("aliases")),
        "wikibase_id": entry.get("wikibase_id"),
        "status": str(entry.get("status") or ""),
        "judge_model": judge_model,
        "evaluator": evaluator,
        "hmo_schema_verdict_schema": HMO_SCHEMA_VERDICT_SCHEMA,
    }


def schema_verdict_input_fingerprint(
    entry: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "hmo_wikibase_schema",
) -> str:
    return canonical_hash(
        schema_verdict_query_summary(entry, judge_model, evaluator=evaluator),
    )


def sanitise_stale_schema_verdict(
    entry: dict[str, Any],
    stored: dict[str, Any] | None,
    *,
    judge_model: str | None = None,
    evaluator: str = "hmo_wikibase_schema",
) -> dict[str, Any] | None:
    if not isinstance(stored, dict) or not stored:
        return None
    model = judge_model or str(stored.get("model") or "gemini-3.5-flash")
    eval_id = str(stored.get("evaluator") or evaluator)
    expected = schema_verdict_input_fingerprint(entry, model, evaluator=eval_id)
    current = sanitise_stored_verdict(stored, expected_fingerprint=expected)
    return normalise_public_verdict(current) if current is not None else None
