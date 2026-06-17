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

import asyncio
import json
import logging
import uuid
from pathlib import Path
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
from app.pipeline.inference_cache import read_from_inference_cache, write_to_inference_cache
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

    # locate_eval_agent() is intentionally NOT checked here — a
    # fully-cached run never needs the eval-agent subprocess (e.g. Heroku).
    # If uncached matches exist, the generator raises runner.error which
    # sse_stream's producer forwards to the client.

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

    Global inference-cache integration
    -----------------------------------
    Before spawning the subprocess, each match is checked against the
    shared ``inference_cache`` (Redis L1 → Postgres L2, kind ``ai_verdict``).
    Matches with a cached verdict are streamed immediately as
    ``agent.verdict`` events without calling Gemini. Only uncached matches
    go through the eval-agent subprocess. After the subprocess completes,
    each new verdict is written back to both cache tiers so subsequent
    users warm-hit on the same entity.

    ``override_cache=True`` skips the pre-check (forces fresh Gemini calls)
    but still writes new verdicts to the cache afterwards.
    """
    from app.db import session_scope  # noqa: PLC0415
    from app.pipeline.ai_verifier import GEMINI_MODEL  # noqa: PLC0415

    _judge_model = tier_model or GEMINI_MODEL

    # ── Pre-check inference cache ──────────────────────────────────────
    # Done BEFORE locate_eval_agent() so a fully-cached run never
    # requires the eval-agent subprocess to be present (e.g. Heroku).
    # override_cache bypasses this check so every match goes through
    # Gemini fresh — but we still write the new verdicts back afterwards.
    pre_cached: list[tuple[AuthorityMatch, RunRecord, dict[str, Any]]] = []
    uncached: list[tuple[AuthorityMatch, RunRecord]] = []
    if not override_cache:
        async with session_scope() as pre_db:
            for match, record in matches:
                qs = _authority_verdict_query_summary(match, _judge_model)
                hit = await read_from_inference_cache(
                    pre_db, kind="ai_verdict", query_summary=qs,
                )
                if hit is not None:
                    pre_cached.append((match, record, hit))
                else:
                    uncached.append((match, record))
    else:
        uncached = list(matches)

    # ── Resolve eval-agent root (only needed for uncached matches) ─────
    eval_root: Path | None = None
    state_dir: Path | None = None
    session_dir: Path | None = None
    pipeline_output: Path | None = None
    base: Path
    if uncached:
        eval_root = locate_eval_agent()
        state_dir = eval_root / "state" / "ai-verify-sessions" / run_id
        session_dir = state_dir / "sessions" / session_id
        pipeline_output = session_dir / "pipeline-output"
        base = session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
    else:
        import tempfile  # noqa: PLC0415
        base = Path(tempfile.mkdtemp(prefix=f"mhm-auth-{session_id}-"))

    # ── Build fixture (only uncached matches need to go to eval-agent) ─
    by_cn: dict[str, list[dict[str, Any]]] = {}
    marc_by_cn: dict[str, dict[str, Any]] = {}
    for match, record in uncached:
        cn = match.control_number
        by_cn.setdefault(cn, []).append(_match_to_desktop_shape(match))
        marc_by_cn.setdefault(cn, dict(record.marc or {}))

    if by_cn:
        assert pipeline_output is not None
        authority_records = []
        for cn, ms in by_cn.items():
            base_marc = dict(marc_by_cn.get(cn) or {"_control_number": cn})
            base_marc.setdefault("_control_number", cn)
            base_marc["marc_authority_matches"] = ms
            authority_records.append(base_marc)
        build_filtered_fixture(
            dest_dir=pipeline_output,
            marc_records=list(marc_by_cn.values()),
            authority_records=authority_records,
        )

    start_ev = AgentEvent(
        type="session.start",
        payload={
            "session_id":  session_id,
            "run_id":      run_id,
            "action_id":   action.id,
            "scope_size":  len(matches),
            "scope_cn":    sorted({m.control_number for m, _ in matches}),
            "goal":        agent_actions.render_goal(action, n_candidates=len(matches)),
            "cache_hits":  len(pre_cached),
        },
    )
    persist_session_event(base, start_ev)
    yield start_ev

    # ── Emit pre-cached verdicts immediately ──────────────────────────
    for match, _rec, cached_payload in pre_cached:
        synthetic = {
            "candidate":   {**_match_to_desktop_shape(match), "_match_id": str(match.id)},
            "verdict":     cached_payload.get("verdict") or {},
            "judge_id":    cached_payload.get("judge_id"),
            "judged_at":   cached_payload.get("judged_at"),
            "cache_key":   cached_payload.get("cache_key"),
            "evaluator_id": cached_payload.get("evaluator"),
            "confidence":  cached_payload.get("confidence"),
            "record_id":   match.control_number,
            "sub_type":    cached_payload.get("sub_type") or match.role or "",
            "from_inference_cache": True,
        }
        ev = AgentEvent(type="agent.verdict", payload=synthetic)
        persist_session_event(base, ev)
        yield ev

    # ── Subprocess for uncached matches ───────────────────────────────
    try:
        if uncached:
            assert pipeline_output is not None and state_dir is not None
            async for ev in spawn_eval_agent_run(
                pipeline_output=pipeline_output,
                evaluators=action.evaluators,
                api_key=api_key,
                state_dir=state_dir,
                tier_model=tier_model,
                override_cache=override_cache,
                rpm=action.rate_limit_rpm,
            ):
                persist_session_event(base, ev)
                yield ev
    finally:
        on_disk_verdicts = read_run_verdicts(state_dir) if (uncached and state_dir) else []
        if on_disk_verdicts:
            _enrich_verdict_match_ids(on_disk_verdicts, matches)
        for v in on_disk_verdicts:
            ev = AgentEvent(type="agent.verdict", payload=v)
            persist_session_event(base, ev)
            yield ev

        if on_disk_verdicts:
            try:
                await _persist_ai_verdicts_to_matches(
                    run_id=run_id,
                    session_id=session_id,
                    verdicts=on_disk_verdicts,
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to persist ai verdicts to matches")

            # Write new verdicts to the shared inference cache so future
            # users verifying the same authority entity get a warm hit.
            try:
                await _write_authority_verdicts_to_cache(
                    matches_by_id={str(m.id): m for m, _ in uncached},
                    verdicts=on_disk_verdicts,
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to write authority verdicts to inference cache")

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


def _authority_verdict_query_summary(
    match: AuthorityMatch,
    judge_model: str = "gemini-3.5-flash",
) -> dict[str, Any]:
    """Stable content key for caching an authority verdict across users/runs.

    Keyed by the entity's canonical identity: name + role + authority IDs +
    the judge model. Including the model ensures that a verdict cached under
    an older model is never served after an upgrade.
    """
    return {
        "text":         (match.entity_text or "").strip(),
        "role":         (match.role or "").strip(),
        "mazal_id":     match.mazal_id or "",
        "viaf_id":      match.viaf_id or "",
        "wikidata_qid": match.wikidata_qid or "",
        "judge_model":  judge_model,
    }


async def _write_authority_verdicts_to_cache(
    *,
    matches_by_id: dict[str, AuthorityMatch],
    verdicts: list[dict[str, Any]],
) -> None:
    """Persist newly-judged authority verdicts into the shared inference cache."""
    from app.db import session_scope  # noqa: PLC0415

    async with session_scope() as db:
        for v in verdicts:
            cand = v.get("candidate") if isinstance(v, dict) else None
            match_id = cand.get("_match_id") if isinstance(cand, dict) else None
            match = matches_by_id.get(str(match_id)) if match_id else None
            if match is None:
                continue
            _jm = v.get("judge_id") or v.get("model") or "gemini-3.5-flash"
            qs = _authority_verdict_query_summary(match, _jm)
            cached_result = {
                "verdict":    v.get("verdict") or {},
                "judge_id":   v.get("judge_id") or v.get("model"),
                "judged_at":  v.get("judged_at"),
                "cache_key":  v.get("cache_key"),
                "evaluator":  v.get("evaluator_id") or v.get("evaluator"),
                "confidence": v.get("confidence"),
                "sub_type":   v.get("sub_type"),
            }
            await write_to_inference_cache(
                db, kind="ai_verdict", query_summary=qs, result=cached_result,
            )


async def _persist_ai_verdicts_to_matches(
    *,
    run_id: str,
    session_id: str,
    verdicts: list[dict[str, Any]],
) -> None:
    """Write each verdict back to its AuthorityMatch.payload.ai_verdict.

    Joins on ``candidate._match_id`` (which we set in
    :func:`_match_to_desktop_shape`). Uses a fresh DB session because
    the request-scoped session that started the SSE stream is closed
    by the time this finally-block runs.
    """
    from app.db import session_scope  # noqa: PLC0415

    # Build {match_id_uuid: verdict_summary} for one bulk UPDATE pass.
    summaries: dict[uuid.UUID, dict[str, Any]] = {}
    for v in verdicts:
        cand = (v.get("candidate") or {}) if isinstance(v, dict) else {}
        raw = cand.get("_match_id") if isinstance(cand, dict) else None
        if not raw:
            continue
        try:
            mid = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            continue
        vd = (v.get("verdict") or {}) if isinstance(v, dict) else {}
        summary = {
            "overall":     vd.get("overall"),
            "name_ok":     vd.get("name_ok"),
            "type_ok":     vd.get("type_ok"),
            "role_ok":     vd.get("role_ok"),
            "reasoning":   vd.get("reasoning"),
            "model":       v.get("judge_id") or v.get("model"),
            "judged_at":   v.get("judged_at"),
            "cache_key":   v.get("cache_key"),
            "session_id":  session_id,
            "evaluator":   v.get("evaluator_id") or v.get("evaluator"),
        }
        summaries[mid] = summary

    if not summaries:
        return

    async with session_scope() as db:
        rows = (
            await db.execute(
                select(AuthorityMatch).where(
                    AuthorityMatch.run_id == uuid.UUID(run_id),
                    AuthorityMatch.id.in_(list(summaries.keys())),
                )
            )
        ).scalars().all()
        for m in rows:
            payload = dict(m.payload or {})
            payload["ai_verdict"] = summaries[m.id]
            m.payload = payload
        await db.commit()


# ── GET /runs/{run_id}/ai-verify/sessions ─────────────────────────────


@router.get("/runs/{run_id}/ai-verify/sessions")
async def list_run_sessions(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return list_sessions(str(run_id))


# ── GET /runs/{run_id}/ai-verify/results ─────────────────────────────


@router.get("/runs/{run_id}/ai-verify/results")
async def list_run_verdicts_endpoint(
    run_id: uuid.UUID,
    q: str | None = Query(
        default=None,
        max_length=256,
        description=(
            "Substring search over candidate name, record_id, evaluator_id, "
            "sub_type, and reasoning (case-insensitive)."
        ),
    ),
    overall: str | None = Query(
        default=None,
        pattern=r"^(pass|full|partial|fail|abstain)$",
        description="Filter by verdict overall value.",
    ),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Return filtered + paginated verdicts for a run.

    Reads the on-disk ``results.jsonl`` from the per-run state dir and
    applies ``q`` (substring) and ``overall`` filters server-side so
    the browser never receives megabytes of LLM reasoning prose for a
    simple keyword search.

    Returns:

    .. code-block:: json

        {
          "total": <int>,          // total after filters
          "offset": <int>,
          "limit": <int>,
          "verdicts": [<AgentEvent>, ...],  // page
          "counts": {               // over the whole filtered set
            "pass": 0, "partial": 0, "fail": 0, "abstain": 0, "unknown": 0
          }
        }
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)

    all_verdicts = await asyncio.to_thread(_collect_run_verdicts, str(run_id))

    # Server-side filter.
    filtered = _filter_verdicts(all_verdicts, q=q, overall=overall)

    counts = _count_by_overall(filtered)
    page = filtered[offset : offset + limit]

    return {
        "total":    len(filtered),
        "offset":   offset,
        "limit":    limit,
        "verdicts": page,
        "counts":   counts,
    }


# ── GET /runs/{run_id}/ai-verify/export ───────────────────────────────


@router.get("/runs/{run_id}/ai-verify/export")
async def export_run_verdicts(
    run_id: uuid.UUID,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    q: str | None = Query(default=None, max_length=256),
    overall: str | None = Query(
        default=None, pattern=r"^(pass|full|partial|fail|abstain)$",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Download all filtered verdicts as CSV or JSON.

    Mirrors the section-export pattern: runs the same server-side
    filter as ``/results`` and streams the output with a
    ``Content-Disposition: attachment`` header so the browser saves
    the file without loading it into the JS heap.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)

    all_verdicts = await asyncio.to_thread(_collect_run_verdicts, str(run_id))
    filtered = _filter_verdicts(all_verdicts, q=q, overall=overall)

    suffix = f"run-{run_id}-ai-verify"
    if format == "csv":
        import io  # noqa: PLC0415
        import csv as _csv  # noqa: PLC0415

        def _csv_gen():
            buf = io.StringIO()
            writer = _csv.writer(buf)
            headers = [
                "record_id", "evaluator_id", "sub_type", "candidate",
                "overall", "name_ok", "type_ok", "role_ok",
                "judge_id", "judged_at", "cache_key", "reasoning",
            ]
            writer.writerow(headers)
            yield buf.getvalue()
            for ev in filtered:
                buf = io.StringIO()
                writer = _csv.writer(buf)
                cand = (ev.get("candidate") or {}) if isinstance(ev, dict) else {}
                v = (ev.get("verdict") or {}) if isinstance(ev, dict) else {}
                writer.writerow([
                    str(ev.get("record_id") or ""),
                    str(ev.get("evaluator_id") or ""),
                    str(ev.get("sub_type") or ""),
                    str(
                        cand.get("person") or cand.get("text") or
                        cand.get("entity_text") or cand.get("name") or ""
                        if isinstance(cand, dict) else ""
                    ),
                    str(v.get("overall") or "" if isinstance(v, dict) else ""),
                    str(v.get("name_ok") or "" if isinstance(v, dict) else ""),
                    str(v.get("type_ok") or "" if isinstance(v, dict) else ""),
                    str(v.get("role_ok") or "" if isinstance(v, dict) else ""),
                    str(ev.get("judge_id") or ""),
                    str(ev.get("judged_at") or ""),
                    str(ev.get("cache_key") or ""),
                    str(v.get("reasoning") or "" if isinstance(v, dict) else ""),
                ])
                yield buf.getvalue()

        return StreamingResponse(
            _csv_gen(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{suffix}.csv"',
            },
        )

    import json as _json  # noqa: PLC0415

    def _json_gen():
        yield '{"run_id":' + _json.dumps(str(run_id)) + ',"verdicts":['
        first = True
        for ev in filtered:
            if not first:
                yield ","
            first = False
            yield _json.dumps(ev, ensure_ascii=False)
        yield "]}"

    return StreamingResponse(
        _json_gen(),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{suffix}.json"',
        },
    )


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


def _enrich_verdict_match_ids(
    verdicts: list[dict[str, Any]],
    matches: list[tuple[AuthorityMatch, RunRecord]],
) -> None:
    """Attach ``candidate._match_id`` when eval-agent omitted it from results.jsonl."""
    lookup: dict[tuple[str, str, str], str] = {}
    for m, _ in matches:
        lookup[
            (
                m.control_number,
                (m.entity_text or "").strip(),
                (m.role or "").strip(),
            )
        ] = str(m.id)

    for v in verdicts:
        cand = v.get("candidate")
        if not isinstance(cand, dict):
            v["candidate"] = cand = {}
        if cand.get("_match_id"):
            continue
        rid = str(v.get("record_id") or "")
        name = str(
            cand.get("name") or cand.get("person") or cand.get("text") or ""
        ).strip()
        role = str(cand.get("role") or "").strip()
        mid = lookup.get((rid, name, role))
        if mid:
            cand["_match_id"] = mid


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
            # MARC Parsing should have persisted every record; if not, fall
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


# ── Verdict helpers (results + export) ────────────────────────────────


def _collect_run_verdicts(run_id: str) -> list[dict[str, Any]]:
    """Collect all verdicts on disk for a run (authority verify path).

    Reads ``results.jsonl`` from the per-run state dir without
    locking so it's safe to call concurrently with a live SSE stream
    (the worst case is a partial last line which is silently skipped
    by ``read_run_verdicts``'s JSON decoder).

    Returns an empty list when the eval-agent root is not present
    (Heroku dyno without local eval-agent) — no verdicts were produced
    on disk in that case (they came from the inference cache and were
    serialised as SSE events only, not written to disk by this path).
    """
    try:
        eval_root = locate_eval_agent()
    except FileNotFoundError:
        return []
    state_dir = eval_root / "state" / "ai-verify-sessions" / run_id
    return read_run_verdicts(state_dir)


def _filter_verdicts(
    verdicts: list[dict[str, Any]],
    *,
    q: str | None,
    overall: str | None,
) -> list[dict[str, Any]]:
    """Apply ``q`` (full-text substring) and ``overall`` filters."""
    result = verdicts
    if overall:
        _norm = overall.lower()
        def _matches_overall(ev: dict[str, Any]) -> bool:
            v = (ev.get("verdict") or {}) if isinstance(ev, dict) else {}
            raw = str(v.get("overall") or "").lower() if isinstance(v, dict) else ""
            # treat "full" and "pass" as the same bucket (mirrors frontend)
            if _norm == "pass":
                return raw in ("pass", "full")
            return raw == _norm
        result = [ev for ev in result if _matches_overall(ev)]
    if q:
        needle = q.strip().lower()
        def _matches_q(ev: dict[str, Any]) -> bool:
            if not isinstance(ev, dict):
                return False
            cand = ev.get("candidate") or {}
            v = ev.get("verdict") or {}
            parts = [
                str(cand.get("person") or cand.get("text") or
                    cand.get("entity_text") or cand.get("name") or ""),
                str(ev.get("record_id") or ""),
                str(ev.get("evaluator_id") or ""),
                str(ev.get("sub_type") or ""),
                str(v.get("reasoning") or "" if isinstance(v, dict) else ""),
            ]
            return needle in " ".join(parts).lower()
        result = [ev for ev in result if _matches_q(ev)]
    return result


def _count_by_overall(verdicts: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {
        "pass": 0, "partial": 0, "fail": 0, "abstain": 0, "unknown": 0,
    }
    for ev in verdicts:
        if not isinstance(ev, dict):
            continue
        v = ev.get("verdict") or {}
        raw = str(v.get("overall") or "").lower() if isinstance(v, dict) else ""
        if raw in ("pass", "full"):
            counts["pass"] += 1
        elif raw in ("partial", "fail", "abstain"):
            counts[raw] += 1
        else:
            counts["unknown"] += 1
    return counts
