"""Content-addressed cache keys for HMO Wikibase item AI verdicts."""

from __future__ import annotations

from typing import Any

from app.pipeline.ai_verdict_cache_common import (
    normalise_claim_rows,
    normalise_public_verdict,
    normalise_shacl_issues,
    sanitise_stored_verdict,
)
from app.pipeline.inference_cache import canonical_hash

# Bumped to w124_v1 with Rule W-124 (shared WPM skill refresh). Prior: w104_v1.
HMO_ITEM_VERDICT_SCHEMA = "w124_v1"


def _sorted_control_numbers(item: dict[str, Any]) -> list[str]:
    stored = item.get("control_numbers")
    if isinstance(stored, list) and stored:
        return sorted({str(x) for x in stored if x})
    cn = str(item.get("_control_number") or item.get("control_number") or "")
    return [cn] if cn else []


def _normalise_claims(claims: Any) -> list[dict[str, Any]]:
    return normalise_claim_rows(claims)


def _normalise_shacl_issues(issues: Any) -> list[dict[str, str]]:
    return normalise_shacl_issues(issues)


def hmo_item_verdict_query_summary(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "hmo_wikibase_item",
    marc_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Stable content key for ``kind=ai_verdict`` HMO item rows."""
    marc_slice = marc_context
    if marc_slice is None:
        raw = item.get("_marc_context")
        marc_slice = raw if isinstance(raw, dict) else {}

    summary: dict[str, Any] = {
        "local_id": str(item.get("_local_id") or item.get("local_id") or ""),
        "entity_type": str(item.get("entity_type") or ""),
        "control_numbers": _sorted_control_numbers(item),
        "class_qid": str(item.get("class_qid") or ""),
        "labels": item.get("labels") or {},
        "descriptions": item.get("descriptions") or {},
        "claims": _normalise_claims(item.get("claims") or []),
        "source_uri": item.get("source_uri"),
        "wikibase_id": item.get("wikibase_id"),
        "shacl_issues": _normalise_shacl_issues(item.get("shacl_issues") or []),
        "marc_context": marc_slice,
        "judge_model": judge_model,
        "evaluator": evaluator,
        "hmo_item_verdict_schema": HMO_ITEM_VERDICT_SCHEMA,
    }
    if evaluator == "hmo_wikibase_item_autofix":
        live = item.get("wikibase_live")
        if isinstance(live, dict):
            summary["wikibase_live_fingerprint"] = {
                "conflict_count": live.get("conflict_count"),
                "row_count": len(live.get("rows") or []),
                "qid": live.get("qid"),
            }
    return summary


def hmo_item_verdict_input_fingerprint(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "hmo_wikibase_item",
    marc_context: dict[str, str] | None = None,
) -> str:
    return canonical_hash(
        hmo_item_verdict_query_summary(
            item,
            judge_model,
            evaluator=evaluator,
            marc_context=marc_context,
        ),
    )


def hmo_item_verdict_judge_model(ai_verdict: dict[str, Any] | None) -> str:
    if not ai_verdict:
        return "gemini-3.5-flash"
    return str(ai_verdict.get("model") or "gemini-3.5-flash")


def sanitise_stale_hmo_item_verdict(
    item: dict[str, Any],
    *,
    judge_model: str | None = None,
    evaluator: str = "hmo_wikibase_item",
    marc_context: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return ``ai_verdict`` only when ``cache_key`` matches current item input."""
    av = item.get("ai_verdict")
    if not isinstance(av, dict) or not av:
        return None
    if not av.get("cache_key"):
        return normalise_public_verdict(av)
    model = judge_model or hmo_item_verdict_judge_model(av)
    eval_id = str(av.get("evaluator") or evaluator)
    expected = hmo_item_verdict_input_fingerprint(
        item,
        model,
        evaluator=eval_id,
        marc_context=marc_context,
    )
    current = sanitise_stored_verdict(av, expected_fingerprint=expected)
    return normalise_public_verdict(current) if current is not None else None
