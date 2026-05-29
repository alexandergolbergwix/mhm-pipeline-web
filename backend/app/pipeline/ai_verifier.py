"""AI verification of authority matches.

Mirrors the desktop pipeline's eval-agent authority evaluator: given a
match, ask Gemini "is this the right authority record for this entity?"
and return a structured verdict (full / partial / fail / abstain) + a
short reasoning string.

Phase-9 stored Gemini key path: when the calling user has saved a
Gemini API key, we call the live API. When they haven't, we fall back
to a deterministic heuristic verdict so the surface is always
functional (and the UI displays which path was taken).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidTag

from app.crypto import secrets as secrets_mod
from app.models.api_key import ApiKey
from app.models.run import AuthorityMatch

logger = logging.getLogger(__name__)

VERDICT_FULL = "full"
VERDICT_PARTIAL = "partial"
VERDICT_FAIL = "fail"
VERDICT_ABSTAIN = "abstain"


@dataclass
class AiVerdict:
    overall: str            # full | partial | fail | abstain
    reasoning: str
    model: str              # "gemini-2.5-flash" | "heuristic"
    judged_at: str          # ISO 8601


GEMINI_MODEL = "gemini-2.5-flash"


async def verify_match(
    match: AuthorityMatch, *, marc_record: dict[str, Any] | None,
    gemini_key: str | None,
) -> AiVerdict:
    """Return a verdict for *match* — Gemini if a key is present, otherwise
    a deterministic heuristic verdict that uses the same signals the AI
    would (sources, guards, dates, role).
    """
    if gemini_key:
        try:
            return await _gemini_verdict(match, marc_record, gemini_key)
        except Exception as exc:  # noqa: BLE001 — never let an upstream blip kill the call
            logger.warning("Gemini call failed (%s); falling back to heuristic", exc)
    return _heuristic_verdict(match)


# ── Heuristic verdict ───────────────────────────────────────────────────


def _heuristic_verdict(m: AuthorityMatch) -> AiVerdict:
    """Use the same signals the agentic judge would weigh: guard_flags,
    source agreement, confidence band, role/date interaction.
    """
    payload: dict[str, Any] = m.payload or {}
    guards: list[str] = list(payload.get("guard_flags") or [])
    sources: list[str] = list(payload.get("sources") or [])
    source_count = int(payload.get("source_count") or 0)
    role_kind = payload.get("role_kind", "other")
    birth = payload.get("birth_year")
    death = payload.get("death_year")
    ms_year = payload.get("ms_year")

    notes: list[str] = []

    if "date_conflict" in guards:
        notes.append(
            f"The MARC record dates the manuscript to {ms_year}, but the "
            f"candidate's lifespan ({birth or '?'}–{death or '?'}) cannot "
            f"reconcile with a {role_kind} role."
        )
        return AiVerdict(
            overall=VERDICT_FAIL,
            reasoning=" ".join(notes),
            model="heuristic",
            judged_at=_now(),
        )

    if "weaker_alternative" in guards:
        notes.append(
            "This is the secondary candidate. Prefer the primary unless "
            "you have specific evidence pointing at this one."
        )
        return AiVerdict(
            overall=VERDICT_ABSTAIN,
            reasoning=" ".join(notes),
            model="heuristic",
            judged_at=_now(),
        )

    if m.confidence == "high" and source_count >= 2 and not guards:
        notes.append(
            f"Cross-sourced across {len(sources)} authorities "
            f"({', '.join(sources)}) with no guard violations. Strong match."
        )
        return AiVerdict(
            overall=VERDICT_FULL,
            reasoning=" ".join(notes),
            model="heuristic",
            judged_at=_now(),
        )

    if "name_drift" in guards:
        notes.append(
            "The matched authority's preferred form drifts from the MARC "
            "heading — the name is likely the same person under a variant "
            "spelling, but a curator should confirm."
        )
        return AiVerdict(
            overall=VERDICT_PARTIAL,
            reasoning=" ".join(notes),
            model="heuristic",
            judged_at=_now(),
        )

    if m.confidence == "low":
        notes.append(
            f"Single-source ({sources[0] if sources else 'unknown'}) match "
            "with a short / common surface form. Manual confirmation strongly "
            "recommended before accepting."
        )
        return AiVerdict(
            overall=VERDICT_ABSTAIN,
            reasoning=" ".join(notes),
            model="heuristic",
            judged_at=_now(),
        )

    notes.append(
        f"Medium confidence — {source_count} source agreement, no fatal "
        "guards. Likely correct but worth a quick check of biographical "
        "details."
    )
    return AiVerdict(
        overall=VERDICT_PARTIAL,
        reasoning=" ".join(notes),
        model="heuristic",
        judged_at=_now(),
    )


# ── Gemini verdict ──────────────────────────────────────────────────────


async def _gemini_verdict(
    m: AuthorityMatch, marc_record: dict[str, Any] | None, api_key: str,
) -> AiVerdict:
    """Call Gemini's generateContent with a structured-output schema."""
    import httpx  # noqa: PLC0415 — heavy import deferred

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    prompt = _build_prompt(m, marc_record)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "overall":   {"type": "string", "enum": ["full", "partial", "fail", "abstain"]},
                    "reasoning": {"type": "string"},
                },
                "required": ["overall", "reasoning"],
            },
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, params={"key": api_key}, json=body)
        r.raise_for_status()
        data = r.json()

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    return AiVerdict(
        overall=str(parsed.get("overall", "abstain")),
        reasoning=str(parsed.get("reasoning", "")),
        model=GEMINI_MODEL,
        judged_at=_now(),
    )


def _build_prompt(m: AuthorityMatch, marc_record: dict[str, Any] | None) -> str:
    payload = m.payload or {}
    marc_context = ""
    if marc_record:
        marc_context = (
            "\nManuscript context:\n"
            f"  title:        {marc_record.get('title','')}\n"
            f"  authors:      {marc_record.get('authors')}\n"
            f"  contributors: {marc_record.get('contributors')}\n"
            f"  dates:        {marc_record.get('dates')}\n"
        )
    return f"""You are an authority-matching reviewer for Hebrew manuscripts.

Decide whether the candidate authority record below is the correct
identity for the entity extracted from the MARC record. Reply with
JSON only — no prose outside the schema.

Verdict values:
  full     — Clear, unambiguous match
  partial  — Probably the same person but worth a manual check
  fail     — Wrong person (e.g. date conflict, different identity)
  abstain  — Not enough evidence to commit

Extracted entity:
  name:        {m.entity_text}
  role:        {m.role}

Candidate:
  matched_name:        {m.matched_name}
  mazal_id:            {m.mazal_id}
  viaf_id:             {m.viaf_id}
  wikidata_qid:        {m.wikidata_qid}
  preferred_name_lat:  {payload.get('preferred_name_lat','')}
  sources:             {payload.get('sources')}
  source_count:        {payload.get('source_count')}
  birth_year:          {payload.get('birth_year')}
  death_year:          {payload.get('death_year')}
  ms_year:             {payload.get('ms_year')}
  guard_flags:         {payload.get('guard_flags')}
  pipeline confidence: {m.confidence}
{marc_context}
Respond with JSON: {{"overall": "...", "reasoning": "<≤ 60 words>"}}.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Helper to fetch the user's decrypted Gemini key ─────────────────────


async def unwrap_user_gemini_key(
    db, *, user_id, kek: bytes,
) -> str | None:
    from sqlalchemy import select  # noqa: PLC0415

    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.key_name == "gemini")
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        wrapped = secrets_mod.WrappedSecret(
            ciphertext=row.ciphertext,
            ciphertext_nonce=row.ciphertext_nonce,
            dek_wrapped=row.dek_wrapped,
            dek_wrap_nonce=row.dek_wrap_nonce,
        )
        return secrets_mod.unwrap_secret(wrapped, kek=kek)
    except InvalidTag:
        logger.warning("Failed to unwrap Gemini key for user %s", user_id)
        return None
