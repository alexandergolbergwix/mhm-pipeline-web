"""Fail-closed DeepSeek confirm for uncertain Wikidata duplicates (Rule W-195).

Called only when a live QID survived identity gates but the match is not
identifier-certain (work label+author, or a person QID with no remaining
identity PID). Never overrides a W-190 heading clash. ``same_item`` without a
shared trusted identifier is not enough to UPDATE.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

CONFIRM_MODEL = "deepseek-ai/DeepSeek-V4-Flash"
SAME_ITEM = "same_item"
DIFFERENT_ITEM = "different_item"
UNSURE = "unsure"
_ALLOWED = frozenset({SAME_ITEM, DIFFERENT_ITEM, UNSURE})
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_CONFIRM_MEMO: dict[str, str] = {}

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
    live_p31: list[str] | None = None,
    catalog_description: str = "",
    catalog_author: str = "",
    live_description_en: str = "",
    live_description_he: str = "",
    live_authors: list[str] | None = None,
    complete: CompleteFn | None = None,
) -> str:
    """Return ``same_item`` / ``different_item`` / ``unsure``. Fail-closed."""
    prompt = _build_prompt(
        local_id=local_id,
        entity_type=entity_type,
        heading=heading,
        candidate_qid=candidate_qid,
        method=method,
        has_trusted_identifier=has_trusted_identifier,
        live_en=live_en,
        live_he=live_he,
        live_p31=live_p31 or [],
        catalog_description=catalog_description,
        catalog_author=catalog_author,
        live_description_en=live_description_en,
        live_description_he=live_description_he,
        live_authors=live_authors or [],
    )
    memo_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if complete is None and memo_key in _CONFIRM_MEMO:
        verdict = _CONFIRM_MEMO[memo_key]
    else:
        try:
            raw = (complete or _complete_deepseek)(prompt)
            verdict = _parse_verdict(raw)
        except Exception:
            logger.exception(
                "Duplicate confirm failed; treating as unsure (local_id=%s qid=%s)",
                local_id, candidate_qid,
            )
            return UNSURE
        if complete is None and verdict in _ALLOWED:
            _CONFIRM_MEMO[memo_key] = verdict
    if verdict not in _ALLOWED:
        return UNSURE
    if verdict == SAME_ITEM and not has_trusted_identifier:
        from app.pipeline.wikidata_live_native_hygiene import (  # noqa: PLC0415
            work_p31_allows_link_only,
        )

        if (
            str(entity_type or "").lower() == "work"
            and work_p31_allows_link_only(live_p31 or [])
        ):
            return SAME_ITEM
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
    live_p31: list[str],
    catalog_description: str = "",
    catalog_author: str = "",
    live_description_en: str = "",
    live_description_he: str = "",
    live_authors: list[str] | None = None,
) -> str:
    extra_lines: list[str] = []
    if catalog_author:
        extra_lines.append(f"catalog_author: {catalog_author}")
    if catalog_description:
        extra_lines.append(f"catalog_description: {catalog_description}")
    if live_description_en:
        extra_lines.append(f"live_description_en: {live_description_en}")
    if live_description_he:
        extra_lines.append(f"live_description_he: {live_description_he}")
    if live_authors:
        extra_lines.append(f"live_authors: {','.join(live_authors)}")
    extra_block = ("\n".join(extra_lines) + "\n") if extra_lines else ""
    return (
        "You confirm whether a pre-modern Hebrew manuscript catalog entity is the same Wikidata item.\n"
        "Reply with JSON only: {\"verdict\": \"same_item\"|\"different_item\"|\"unsure\"}.\n"
        "Use same_item when the title and author/topic refer to the same canonical work, prayer, tractate, or person.\n"
        "Use different_item when the candidate has a different author, is a modern work/novel/printed book by another author, or is a different work sharing only a generic title.\n"
        f"local_id: {local_id}\n"
        f"entity_type: {entity_type}\n"
        f"catalog_heading: {heading}\n"
        f"candidate_qid: {candidate_qid}\n"
        f"match_method: {method}\n"
        f"has_trusted_identifier: {has_trusted_identifier}\n"
        f"live_label_en: {live_en}\n"
        f"live_label_he: {live_he}\n"
        f"live_p31: {','.join(live_p31)}\n"
        f"{extra_block}"
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
    last_exc: Exception | None = None
    for attempt in range(2):
        if attempt:
            time.sleep(2.0)
        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {judge_auth}"},
                timeout=20.0,
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            return str((choices[0].get("message") or {}).get("content") or "")
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning(
                "Duplicate confirm LLM attempt %d/2 failed: %s",
                attempt + 1, exc,
            )
    assert last_exc is not None
    raise last_exc
