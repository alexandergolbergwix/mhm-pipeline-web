"""Content-addressed cache keys for authority AI verdicts."""

from __future__ import annotations

from typing import Any

from app.models.run import AuthorityMatch
from app.pipeline.ai_verdict_cache_common import (
    normalise_public_verdict,
    normalise_string_list,
    sanitise_stored_verdict,
)
from app.pipeline.inference_cache import canonical_hash
from app.pipeline.marc_verify_context import (
    AUTHORITY_MARC_KEYS,
    index_marc_records,
    marc_context_for_item,
)

AUTHORITY_VERDICT_SCHEMA = "w50_v1"


def _source_count(payload: dict[str, Any]) -> int:
    raw = payload.get("source_count")
    if isinstance(raw, int) and raw > 0:
        return raw
    sources = payload.get("sources")
    if isinstance(sources, list) and sources:
        return len(sources)
    return 1


def marc_context_for_authority_match(
    match: AuthorityMatch,
    marc_record: dict[str, Any] | None = None,
) -> dict[str, str]:
    cn = str(match.control_number or "")
    rec = dict(marc_record or {"_control_number": cn})
    rec.setdefault("_control_number", cn)
    index = index_marc_records([rec])
    return marc_context_for_item({"control_numbers": [cn]}, index, keys=AUTHORITY_MARC_KEYS)


def authority_verdict_query_summary(
    match: AuthorityMatch,
    judge_model: str = "gemini-3.5-flash",
    *,
    marc_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = dict(match.payload or {})
    return {
        "match_id": str(match.id),
        "control_number": str(match.control_number or ""),
        "entity_text": (match.entity_text or "").strip(),
        "entity_kind": str(match.entity_kind or ""),
        "role": (match.role or "").strip(),
        "matched_name": (match.matched_name or "").strip(),
        "mazal_id": match.mazal_id or "",
        "viaf_id": match.viaf_id or "",
        "wikidata_qid": match.wikidata_qid or "",
        "confidence": match.confidence or "",
        "source": match.source or "",
        "guard_flags": normalise_string_list(payload.get("guard_flags")),
        "sources": normalise_string_list(payload.get("sources")),
        "source_count": _source_count(payload),
        "birth_year": payload.get("birth_year"),
        "death_year": payload.get("death_year"),
        "preferred_name_lat": str(payload.get("preferred_name_lat") or ""),
        "preferred_name_heb": str(payload.get("preferred_name_heb") or ""),
        "homonym_unresolved": bool(payload.get("homonym_candidates")),
        "marc_context": marc_context or {},
        "judge_model": judge_model,
        "authority_verdict_schema": AUTHORITY_VERDICT_SCHEMA,
    }


def authority_verdict_input_fingerprint(
    match: AuthorityMatch,
    judge_model: str = "gemini-3.5-flash",
    *,
    marc_context: dict[str, str] | None = None,
    marc_record: dict[str, Any] | None = None,
) -> str:
    ctx = marc_context
    if ctx is None:
        ctx = marc_context_for_authority_match(match, marc_record)
    return canonical_hash(
        authority_verdict_query_summary(match, judge_model, marc_context=ctx),
    )


def authority_verdict_judge_model(ai_verdict: dict[str, Any] | None) -> str:
    if not ai_verdict:
        return "gemini-3.5-flash"
    return str(ai_verdict.get("model") or "gemini-3.5-flash")


def sanitise_stale_authority_verdict(
    match: AuthorityMatch,
    *,
    marc_context: dict[str, str] | None = None,
    marc_record: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload = dict(match.payload or {})
    stored = payload.get("ai_verdict")
    if not isinstance(stored, dict):
        return None
    model = authority_verdict_judge_model(stored)
    expected = authority_verdict_input_fingerprint(
        match,
        model,
        marc_context=marc_context,
        marc_record=marc_record,
    )
    current = sanitise_stored_verdict(stored, expected_fingerprint=expected)
    return normalise_public_verdict(current) if current is not None else None


def authority_payload_for_api(
    match: AuthorityMatch,
    *,
    marc_context: dict[str, str] | None = None,
    marc_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(match.payload or {})
    cleaned = sanitise_stale_authority_verdict(
        match,
        marc_context=marc_context,
        marc_record=marc_record,
    )
    if cleaned is None:
        payload.pop("ai_verdict", None)
    else:
        payload["ai_verdict"] = normalise_public_verdict(cleaned)
    return payload
