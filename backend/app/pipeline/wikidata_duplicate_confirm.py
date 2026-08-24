"""Fail-closed DeepSeek confirm for uncertain Wikidata duplicates (Rule W-195).

Called only when a live QID survived identity gates but the match is not
identifier-certain (work label+author, or a person QID with no remaining
identity PID). Never overrides a W-190 heading clash. ``same_item`` without a
shared trusted identifier is not enough to UPDATE.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

CONFIRM_MODEL = "deepseek-ai/DeepSeek-V4-Flash"
SAME_ITEM = "same_item"
DIFFERENT_ITEM = "different_item"
UNSURE = "unsure"
_ALLOWED = frozenset({SAME_ITEM, DIFFERENT_ITEM, UNSURE})
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

CompleteFn = Callable[[str], str]


def confirm_uncertain_duplicate(
    *,
    local_id: str,
    entity_type: str,
    heading: str,
    candidate_qid: str,
    method: str,
    has_trusted_identifier: bool,
    live_en: str = "",
    live_he: str = "",
    complete: CompleteFn | None = None,
) -> str:
    """Return ``same_item`` / ``different_item`` / ``unsure``. Fail-closed."""
    try:
        raw = (complete or _complete_deepseek)(_build_prompt(
            local_id=local_id,
            entity_type=entity_type,
            heading=heading,
            candidate_qid=candidate_qid,
            method=method,
            has_trusted_identifier=has_trusted_identifier,
            live_en=live_en,
            live_he=live_he,
        ))
        verdict = _parse_verdict(raw)
    except Exception:
        logger.exception(
            "Duplicate confirm failed; treating as unsure (local_id=%s qid=%s)",
            local_id, candidate_qid,
        )
        return UNSURE
    if verdict not in _ALLOWED:
        return UNSURE
    if verdict == SAME_ITEM and not has_trusted_identifier:
        return UNSURE
    return verdict


def _build_prompt(
    *,
    local_id: str,
    entity_type: str,
    heading: str,
    candidate_qid: str,
    method: str,
    has_trusted_identifier: bool,
    live_en: str,
    live_he: str,
) -> str:
    return (
        "You confirm whether a catalog entity is the same Wikidata item.\n"
        "Reply with JSON only: {\"verdict\": \"same_item\"|\"different_item\"|\"unsure\"}.\n"
        "Use same_item only when identity is certain. If unsure, say unsure.\n"
        "A heading clash or a different person/work is different_item.\n"
        f"local_id: {local_id}\n"
        f"entity_type: {entity_type}\n"
        f"catalog_heading: {heading}\n"
        f"candidate_qid: {candidate_qid}\n"
        f"match_method: {method}\n"
        f"has_trusted_identifier: {has_trusted_identifier}\n"
        f"live_label_en: {live_en}\n"
        f"live_label_he: {live_he}\n"
    )


def _parse_verdict(raw: str) -> str:
    text = (raw or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return UNSURE
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return UNSURE
    if not isinstance(payload, dict):
        return UNSURE
    return str(payload.get("verdict") or "").strip().lower()


def _complete_deepseek(prompt: str) -> str:
    import httpx  # noqa: PLC0415

    from app.pipeline.judge_models import (  # noqa: PLC0415
        resolve_tier1_model,
        tier1_api_key_for_spec as load_tier1_auth,
    )

    spec = resolve_tier1_model(CONFIRM_MODEL)
    judge_auth = load_tier1_auth(spec, gemini_key=None)
    if not judge_auth:
        raise RuntimeError(f"{spec.label} is not configured (missing {spec.api_key_env})")
    base_url = (spec.base_url or "").rstrip("/")
    if not base_url:
        raise RuntimeError(f"{spec.label} has no base_url")
    payload: dict[str, Any] = {
        "model": spec.id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if spec.extra_body:
        payload.update(spec.extra_body)
    response = httpx.post(
        f"{base_url}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {judge_auth}"},
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    return str((choices[0].get("message") or {}).get("content") or "")
