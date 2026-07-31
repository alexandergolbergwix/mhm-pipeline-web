"""Span-grounded LLM extraction from MARC provenance prose (Rule W-140).

MARC 500/541/561/563/583 carry provenance as narrative Hebrew — owners, places
and events that no regex reaches:

    "בדף 241א רשימת הבעלים ״אברהם היכיני״ המזכירה את נשואי הוריו…"

A tier-1 model (DeepSeek V4 Flash on Qubrid by default) reads that prose and
proposes structured values. Because generation is **not** an evidence channel
(Rules W-72 / W-67 / W-138), every proposal must quote the verbatim span it
came from and is dropped when that span is not literally present in the record.
Proposals are never emitted as statements: they are curator-review candidates,
surfaced as evidence so the judge and the curator can see them.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.pipeline.inference_cache import (
    read_from_inference_cache,
    write_to_inference_cache,
)
from app.pipeline.judge_models import (
    Tier1CredentialsError,
    resolve_tier1_model,
    tier1_api_key_for_spec,
)
from converter.wikidata.property_mapping import (
    MATERIAL_TO_QID,
    P_LOCATION_OF_CREATION,
    P_OWNED_BY,
    P_MATERIAL,
)

logger = logging.getLogger(__name__)

EXTRACT_SCHEMA = "marc_llm_extract_v1"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4-Flash"
CACHE_KIND = "marc.llm_extract"

STATUS_OK = "ok"
STATUS_NO_SOURCE = "no_source"
STATUS_DISABLED = "disabled"
STATUS_UNAVAILABLE = "unavailable"

# Only properties that already exist as audited constants — the extractor
# never introduces a new P/Q (Rule W-26).
SUPPORTED_PROPERTIES: dict[str, str] = {
    P_OWNED_BY: "owner named in the provenance note",
    P_LOCATION_OF_CREATION: "place where the manuscript was written",
    P_MATERIAL: "writing support",
}

# MARC slices that carry provenance prose, in the order we show them.
SOURCE_SLICES = ("provenance", "notes", "colophon_text")

_PROMPT = """You extract structured metadata from Hebrew manuscript catalogue records.

Return JSON only: {{"proposals": [...]}}.

Each proposal MUST be:
  {{"property_id": one of {properties},
    "value": the extracted value as it should be recorded,
    "span": the VERBATIM substring of the record that states it,
    "marc_tag": the MARC tag the span came from (e.g. "561$a"),
    "confidence": "high" | "medium" | "low"}}

Hard rules:
- "span" must be copied character-for-character from the record below. If you
  cannot quote it, do not propose it.
- Propose ONLY what the record states. Never infer, complete or translate a
  value that is not written there.
- {material} must be one of: {materials}. If the record does not name the
  writing support, omit it.
- Return an empty list rather than a guess.

Record for {control_number}:
{record}
"""


def extraction_enabled() -> bool:
    return os.getenv("MARC_LLM_EXTRACT", "1").strip().lower() not in ("0", "false", "no")


def _model_id() -> str:
    return os.getenv("MARC_LLM_EXTRACT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _timeout() -> float:
    try:
        return float(os.getenv("MARC_LLM_EXTRACT_TIMEOUT", "45"))
    except ValueError:
        return 45.0


def _budget() -> int:
    try:
        return int(os.getenv("MARC_LLM_EXTRACT_MAX", "200"))
    except ValueError:
        return 200


def source_text(marc_slice: dict[str, Any] | None) -> str:
    """The provenance prose we let the model read, tagged by slice."""
    if not isinstance(marc_slice, dict):
        return ""
    parts: list[str] = []
    for slot in SOURCE_SLICES:
        value = marc_slice.get(slot)
        if not value:
            continue
        text = " ".join(str(v) for v in value) if isinstance(value, list) else str(value)
        text = text.strip()
        if text:
            parts.append(f"[{slot}] {text}")
    return "\n".join(parts)


def build_prompt(control_number: str, record_text: str) -> str:
    return _PROMPT.format(
        properties=sorted(SUPPORTED_PROPERTIES),
        material=P_MATERIAL,
        materials=sorted({term for term in MATERIAL_TO_QID}),
        control_number=control_number or "(unknown)",
        record=record_text,
    )


def validate_proposal(raw: Any, record_text: str) -> dict[str, Any] | None:
    """Keep a proposal only when it is grounded in a verbatim span."""
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("property_id") or "").strip().upper()
    value = str(raw.get("value") or "").strip()
    span = str(raw.get("span") or "").strip()
    if pid not in SUPPORTED_PROPERTIES or not value or not span:
        return None
    # The whole point of the guard: hallucinated spans cannot survive.
    if span not in record_text:
        return None
    if pid == P_MATERIAL:
        qid = MATERIAL_TO_QID.get(value) or MATERIAL_TO_QID.get(value.lower())
        if not qid:
            return None
        value = qid
    confidence = str(raw.get("confidence") or "").strip().lower()
    return {
        "property_id": pid,
        "value": value,
        "span": span,
        "marc_tag": str(raw.get("marc_tag") or "").strip(),
        "confidence": confidence if confidence in ("high", "medium", "low") else "low",
        "channel": "llm_marc_extraction",
        "schema": EXTRACT_SCHEMA,
    }


def parse_response(text: str, record_text: str) -> list[dict[str, Any]]:
    """Validated proposals from a model response, tolerating fenced JSON."""
    body = str(text or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.index("{"):] if "{" in body else ""
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        logger.warning("marc llm extract: unparseable response")
        return []
    proposals = payload.get("proposals") if isinstance(payload, dict) else None
    if not isinstance(proposals, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in proposals:
        proposal = validate_proposal(raw, record_text)
        if proposal is None:
            continue
        key = (proposal["property_id"], proposal["value"])
        if key in seen:
            continue
        seen.add(key)
        out.append(proposal)
    return out


def _call_qubrid(prompt: str, *, model_id: str, timeout: float) -> str:
    """One OpenAI-compatible chat completion against the tier-1 provider."""
    import httpx  # noqa: PLC0415

    spec = resolve_tier1_model(model_id)
    api_key = tier1_api_key_for_spec(spec, gemini_key=None)
    if not api_key:
        raise Tier1CredentialsError(
            f"{spec.label} is not configured (missing env {spec.api_key_env}).",
        )
    base_url = (spec.base_url or "").rstrip("/")
    if not base_url:
        raise Tier1CredentialsError(f"{spec.label} has no base_url configured.")
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
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    return str((choices[0].get("message") or {}).get("content") or "")


async def extract_for_record(
    db_factory: Any = None,
    *,
    control_number: str,
    marc_slice: dict[str, Any] | None,
    model_id: str | None = None,
    call: Any = None,
    skip_cache: bool = False,
) -> dict[str, Any]:
    """Proposals for one record, content-addressed on the MARC prose.

    *db_factory* is a zero-arg async session **context manager factory**, not a
    live session: a model call takes seconds, and holding a session across it
    closes the connection under us (Rule W-40). The cache is read in one short
    session, the call happens with **no** session open, and the result is
    written in another short session.
    """
    record_text = source_text(marc_slice)
    if not record_text:
        return {"status": STATUS_NO_SOURCE, "proposals": []}
    if not extraction_enabled():
        return {"status": STATUS_DISABLED, "proposals": []}

    model = (model_id or _model_id()).strip()
    prompt = build_prompt(control_number, record_text)
    query_summary = {
        "schema": EXTRACT_SCHEMA,
        "model": model,
        "control_number": control_number,
        "record": record_text,
    }

    if db_factory is not None and not skip_cache:
        async with db_factory() as db:
            cached = await read_from_inference_cache(
                db, kind=CACHE_KIND, query_summary=query_summary,
            )
        if isinstance(cached, dict) and cached.get("status") == STATUS_OK:
            return cached

    invoke = call or (
        lambda: _call_qubrid(prompt, model_id=model, timeout=_timeout())
    )
    from fastapi.concurrency import run_in_threadpool  # noqa: PLC0415

    try:
        raw = await run_in_threadpool(invoke)
    except Exception as exc:  # noqa: BLE001
        # An unreachable model must not look like "nothing to extract", and a
        # transient failure must never be cached.
        logger.warning("marc llm extract failed for %s: %s", control_number, exc)
        return {"status": STATUS_UNAVAILABLE, "proposals": [], "error": str(exc)}

    result = {
        "status": STATUS_OK,
        "model": model,
        "proposals": parse_response(raw, record_text),
    }
    if db_factory is not None:
        async with db_factory() as db:
            await write_to_inference_cache(
                db, kind=CACHE_KIND, query_summary=query_summary, result=result,
            )
    return result


async def attach_llm_proposals(
    db_factory: Any,
    items: list[dict[str, Any]],
    *,
    marc_by_cn: dict[str, dict[str, Any]] | None = None,
    call: Any = None,
    budget: int | None = None,
) -> dict[str, int]:
    """Stamp `_llm_proposals` on every manuscript item we can read prose for.

    Takes a session **factory**, never a live session: the caller's session must
    not stay open across dozens of multi-second model calls (Rule W-40).
    """
    remaining = _budget() if budget is None else budget
    stats = {"records": 0, "proposals": 0, "skipped": 0, "unavailable": 0}
    slices = marc_by_cn or {}
    for item in items:
        if str(item.get("entity_type") or "") != "manuscript":
            continue
        control_number = str(item.get("_primary_control_number") or "").strip()
        marc_slice = (
            item.get("_marc_context")
            or slices.get(control_number)
            or (item.get("verify_evidence") or {}).get("marc")
        )
        if not source_text(marc_slice if isinstance(marc_slice, dict) else None):
            continue
        if remaining <= 0:
            stats["skipped"] += 1
            item["_llm_proposals"] = {
                "status": STATUS_DISABLED,
                "proposals": [],
                "note": "extraction budget exhausted for this build",
            }
            continue
        remaining -= 1
        stats["records"] += 1
        result = await extract_for_record(
            db_factory,
            control_number=control_number,
            marc_slice=marc_slice,
            call=call,
        )
        item["_llm_proposals"] = result
        stats["proposals"] += len(result.get("proposals") or [])
        if result.get("status") == STATUS_UNAVAILABLE:
            stats["unavailable"] += 1
    return stats
