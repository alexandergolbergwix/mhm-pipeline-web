"""AI-agent verification endpoints (per run, per scope).

Four endpoints, all nested under ``/runs/{run_id}/ai-verify/`` because
the agent is a verb on a specific run — never a top-level concept:

* ``GET  /runs/{run_id}/ai-verify/actions?scope_kind=...``
* ``POST /runs/{run_id}/ai-verify/start-stream``  (SSE)
* ``GET  /runs/{run_id}/ai-verify/sessions``
* ``GET  /runs/{run_id}/ai-verify/sessions/{session_id}``

The Gemini key is pulled from the per-user encrypted store; passed to
the eval-agent subprocess as an env var only. The browser never sees
secrets and never types a prompt — actions come from a server-side
registry in ``app.pipeline.agent_actions``.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.run import AuthorityMatch, RunRecord
from app.pipeline import agent_actions, agent_runner
from app.pipeline.agent_runner import (
    AgentEvent, build_filtered_fixture, list_sessions, locate_eval_agent,
    new_session_id, persist_session_event, read_session, read_run_verdicts,
    spawn_eval_agent_run, sse_stream,
)
from app.routers.runs import _lookup_run_with_access


logger = logging.getLogger(__name__)
router = APIRouter(tags=["ai-verify"])


# ── Schemas ───────────────────────────────────────────────────────────


class StartRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=64)
    # When None → ALL matches in the run. When set → only these.
    match_ids: list[uuid.UUID] | None = None
    # Gemini verdicts are cached on disk by eval-agent (see
    # state/cache/verdict_cache.jsonl). Repeated runs over the same
    # candidates skip the LLM call entirely. Setting this True forces
    # fresh judgements — the cache is still populated by the new
    # verdicts, so the next session warm-hits.
    override_cache: bool = False
    # Optional override; defaults to the project's gemini-3.5-flash.
    tier_model: str | None = Field(default=None, max_length=64)


# ── GET /runs/{run_id}/ai-verify/actions ──────────────────────────────


@router.get("/runs/{run_id}/ai-verify/actions")
async def list_available_actions(
    run_id: uuid.UUID,
    scope_kind: str = Query("selection", pattern=r"^(single|selection|all)$"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Available actions for the given entry-point scope kind.

    ``find_duplicates`` for example only surfaces when the scope is
    ``selection`` or ``all`` — it's useless on a single row.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return [
        agent_actions.to_dict(a)
        for a in agent_actions.list_actions(scope_kind=scope_kind)  # type: ignore[arg-type]
    ]


# ── POST /runs/{run_id}/ai-verify/start-stream ────────────────────────


@router.post("/runs/{run_id}/ai-verify/start-stream")
async def start_stream(
    run_id: uuid.UUID,
    payload: StartRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Kick off one AI verification session and stream events via SSE.

    The body declares the action + scope; the API key is resolved
    server-side from the encrypted store. The browser disconnect
    cancels the subprocess so we never pay Gemini for an orphan.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)

    action = agent_actions.get_action(payload.action_id)
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown action_id {payload.action_id!r}",
        )

    # Resolve scope → AuthorityMatch rows + their MARC records.
    matches = await _fetch_matches(db, run_id, payload.match_ids)
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no matches in scope",
        )
    if len(matches) < action.min_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"action {action.id!r} requires at least "
                f"{action.min_candidates} candidates; got {len(matches)}"
            ),
        )

    # Resolve Gemini key (always required — no stub path).
    api_key = await _resolve_gemini_key(db, auth)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No Gemini API key configured. Open Settings → "
                "Credentials and add one from "
                "https://aistudio.google.com/app/apikey."
            ),
        )

    # Locate eval-agent early so a missing sibling repo fails the POST
    # rather than mysteriously failing mid-stream.
    try:
        locate_eval_agent()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    session_id = new_session_id()
    return StreamingResponse(
        sse_stream(_session_event_stream(
            run_id=str(run_id),
            session_id=session_id,
            action=action,
            matches=matches,
            api_key=api_key,
            override_cache=payload.override_cache,
            tier_model=payload.tier_model,
        )),
        media_type="text/event-stream",
        headers={
            "Cache-Control":       "no-cache",
            "X-Accel-Buffering":   "no",
            "X-Session-Id":        session_id,
        },
    )


async def _session_event_stream(
    *,
    run_id: str,
    session_id: str,
    action: agent_actions.AgentAction,
    matches: list[tuple[AuthorityMatch, RunRecord]],
    api_key: str,
    override_cache: bool,
    tier_model: str | None,
):
    """Async generator producing the SSE event sequence for one session.

    Wraps the eval-agent runner with synthetic ``session.start`` /
    ``session.end`` framing and persists every event to disk for replay.
    """
    eval_root = locate_eval_agent()
    base = eval_root / "state" / "ai-verify-sessions" / run_id / session_id
    pipeline_output = base / "pipeline-output"

    # Build the filtered fixture.
    by_cn: dict[str, list[dict[str, Any]]] = {}
    marc_by_cn: dict[str, dict[str, Any]] = {}
    for match, record in matches:
        cn = match.control_number
        by_cn.setdefault(cn, []).append(_match_to_desktop_shape(match))
        marc_by_cn.setdefault(cn, dict(record.marc or {}))

    authority_records = []
    for cn, ms in by_cn.items():
        # Merge MARC fields onto the synthetic authority record so the
        # eval-agent's evaluator sees the same shape it does for full
        # pipeline outputs.
        base_marc = dict(marc_by_cn.get(cn) or {"_control_number": cn})
        base_marc.setdefault("_control_number", cn)
        base_marc["marc_authority_matches"] = ms
        authority_records.append(base_marc)

    build_filtered_fixture(
        dest_dir=pipeline_output,
        marc_records=list(marc_by_cn.values()) or [
            {"_control_number": cn} for cn in by_cn
        ],
        authority_records=authority_records,
    )

    start_ev = AgentEvent(
        type="session.start",
        payload={
            "session_id": session_id,
            "run_id":     run_id,
            "action_id":  action.id,
            "scope_size": len(matches),
            "scope_cn":   list(by_cn.keys()),
            "goal":       agent_actions.render_goal(action, n_candidates=len(matches)),
        },
    )
    persist_session_event(base, start_ev)
    yield start_ev

    try:
        async for ev in spawn_eval_agent_run(
            pipeline_output=pipeline_output,
            evaluators=action.evaluators,
            api_key=api_key,
            state_dir=base,
            tier_model=tier_model,
            override_cache=override_cache,
            rpm=action.rate_limit_rpm,
        ):
            persist_session_event(base, ev)
            yield ev
    finally:
        # When the subprocess exits (or is cancelled), synthesise the
        # final per-candidate verdict events from the on-disk results
        # so the UI's verdict table fills in fully even if the live
        # event stream missed some (cache hits don't emit per-cand
        # progress lines).
        for v in read_run_verdicts(base):
            ev = AgentEvent(type="agent.verdict", payload=v)
            persist_session_event(base, ev)
            yield ev

        end_ev = AgentEvent(
            type="session.end",
            payload={
                "session_id": session_id,
                "scope_size": len(matches),
                "outcome":    "complete",
            },
        )
        persist_session_event(base, end_ev)
        yield end_ev


# ── GET /runs/{run_id}/ai-verify/sessions ─────────────────────────────


@router.get("/runs/{run_id}/ai-verify/sessions")
async def list_run_sessions(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return list_sessions(str(run_id))


# ── GET /runs/{run_id}/ai-verify/sessions/{session_id} ────────────────


@router.get("/runs/{run_id}/ai-verify/sessions/{session_id}")
async def get_run_session(
    run_id: uuid.UUID,
    session_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    data = read_session(str(run_id), session_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        )
    return data


# ── Helpers ───────────────────────────────────────────────────────────


async def _fetch_matches(
    db: AsyncSession,
    run_id: uuid.UUID,
    match_ids: list[uuid.UUID] | None,
) -> list[tuple[AuthorityMatch, RunRecord]]:
    """Resolve scope → AuthorityMatch rows joined with their MARC records.

    Order is by ``control_number`` then ``entity_text`` so multi-row
    scopes serialise stably (the eval-agent's RNG-free pipeline then
    produces a reproducible session for the same scope).
    """
    q = select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
    if match_ids:
        q = q.where(AuthorityMatch.id.in_(match_ids))
    q = q.order_by(AuthorityMatch.control_number, AuthorityMatch.entity_text)
    rows = (await db.execute(q)).scalars().all()
    if not rows:
        return []

    cns = sorted({r.control_number for r in rows})
    records = (
        await db.execute(
            select(RunRecord).where(
                RunRecord.run_id == run_id, RunRecord.control_number.in_(cns),
            )
        )
    ).scalars().all()
    rec_by_cn = {r.control_number: r for r in records}
    out: list[tuple[AuthorityMatch, RunRecord]] = []
    for m in rows:
        rec = rec_by_cn.get(m.control_number)
        if rec is None:
            # Stage-1 should have persisted every record; if not, fall
            # back to a synthetic empty MARC record so eval-agent still
            # sees the match (just without rich MARC context).
            rec = RunRecord(
                run_id=run_id, control_number=m.control_number, marc={},
            )
        out.append((m, rec))
    return out


def _match_to_desktop_shape(m: AuthorityMatch) -> dict[str, Any]:
    """Shape one AuthorityMatch row the way eval-agent's authority
    evaluator expects (one entry of ``marc_authority_matches``).

    Mirrors :func:`app.pipeline.wikidata_studio._approved_match_to_desktop_shape`
    but reads directly off the ORM row (we don't need the approved gate).
    """
    payload = dict(m.payload or {})
    cluster = payload.get("cluster_ids") or {}
    return {
        "name":         m.entity_text,
        "role":         m.role or "",
        "field":        "700/710/711",
        "mazal_id":     m.mazal_id or "",
        "viaf_uri":     f"https://viaf.org/viaf/{m.viaf_id}" if m.viaf_id else "",
        "wikidata_qid": m.wikidata_qid or "",
        "confidence":   m.confidence or "low",
        "source":       m.source or "",
        "sources":      payload.get("sources") or [],
        "source_count": payload.get("source_count") or 1,
        "birth_year":   payload.get("birth_year"),
        "death_year":   payload.get("death_year"),
        "preferred_name_lat": payload.get("preferred_name_lat", ""),
        "gnd_id":       cluster.get("gnd", ""),
        "lc_id":        cluster.get("lccn", ""),
        "isni":         cluster.get("isni", ""),
        "bnf_id":       cluster.get("bnf", ""),
        "guard_flags":  payload.get("guard_flags") or [],
        "matched":      1,
        # The match's DB id surfaces so the verdict table can map a
        # candidate result back to a row in the curator's review table.
        "_match_id":    str(m.id),
    }


async def _resolve_gemini_key(
    db: AsyncSession, auth: AuthContext,
) -> str | None:
    """Unwrap the user's encrypted Gemini key.

    Fixed: ``unwrap_user_gemini_key`` takes ``(db, *, user_id, kek)``
    as keyword-only args. The earlier ``unwrap_user_gemini_key(db, auth)``
    call raised TypeError silently (caught by the broad except → DEBUG
    log → return None), which surfaced as 'No Gemini API key configured'
    even when the row was present in the DB.
    """
    import os
    try:
        from app.pipeline.ai_verifier import (  # noqa: PLC0415
            unwrap_user_gemini_key,
        )
        key = await unwrap_user_gemini_key(
            db, user_id=auth.user.id, kek=auth.kek,
        )
        if key:
            return key
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not unwrap stored Gemini key: %s", exc)
    return os.environ.get("GEMINI_API_KEY")
