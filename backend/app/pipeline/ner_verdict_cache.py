"""Shared cache-key helpers for NER AI verdict inference (``kind=ai_verdict``)."""

from __future__ import annotations

from typing import Any

from app.models.extraction_approval import ExtractionApproval
from app.pipeline.ai_verdict_cache_common import normalise_public_verdict
from app.pipeline.inference_cache import canonical_hash


def ner_verdict_query_summary(
    ext: ExtractionApproval,
    judge_model: str = "gemini-3.5-flash",
) -> dict[str, Any]:
    """Stable content key for caching an NER verdict across users/runs."""
    return {
        "control_number": ext.control_number,
        "source":         ext.source,
        "start":          int(ext.start or 0),
        "end":            int(ext.end or 0),
        "text":           (ext.override_text or ext.text or "").strip(),
        "type":           (ext.override_type or ext.type or "").strip(),
        "role":           (ext.override_role or ext.role or "").strip(),
        "judge_model":    judge_model,
        "ai_extraction_verdict_schema": "v2",
        "suggested_fix_policy":         "text_high_confidence_v1",
    }


def entity_dict_query_summary(ent: dict[str, Any]) -> dict[str, Any]:
    av = ent.get("ai_verdict")
    judge = ner_verdict_judge_model(av if isinstance(av, dict) else None)
    return {
        "control_number": str(ent.get("control_number") or ""),
        "source":         str(ent.get("source") or ""),
        "start":          int(ent.get("start") or 0),
        "end":            int(ent.get("end") or 0),
        "text":           str((ent.get("override_text") or ent.get("text") or "")).strip(),
        "type":           str((ent.get("override_type") or ent.get("type") or "")).strip(),
        "role":           str((ent.get("override_role") or ent.get("role") or "")).strip(),
        "judge_model":    judge,
        "ai_extraction_verdict_schema": "v2",
        "suggested_fix_policy":         "text_high_confidence_v1",
    }


def ner_verdict_input_fingerprint(
    ext: ExtractionApproval,
    judge_model: str = "gemini-3.5-flash",
) -> str:
    return canonical_hash(ner_verdict_query_summary(ext, judge_model))


def entity_dict_verdict_fingerprint(ent: dict[str, Any]) -> str:
    return canonical_hash(entity_dict_query_summary(ent))


def ner_verdict_judge_model(ai_verdict: dict[str, Any] | None) -> str:
    if not ai_verdict:
        return "gemini-3.5-flash"
    return str(ai_verdict.get("model") or "gemini-3.5-flash")


def sanitise_stale_ai_verdict(ent: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``ai_verdict`` only when ``cache_key`` matches current input."""
    av = ent.get("ai_verdict")
    if not isinstance(av, dict) or not av:
        return None
    stored = av.get("cache_key")
    if not stored:
        return normalise_public_verdict(av)
    if str(stored) == entity_dict_verdict_fingerprint(ent):
        return normalise_public_verdict(av)
    return None
