"""AI-agent verification endpoints for AI Extraction (NER + classifier).

Sibling of :mod:`app.routers.ai_verify`. Where ``ai_verify`` audits
Authority Enrichment authority matches (one row per ``AuthorityMatch``), this
router audits AI Extraction extracted entities (one row per
``ExtractionApproval``). The shape is intentionally parallel:

* ``GET  /runs/{run_id}/extraction/ai-verify/actions?scope_kind=...``
* ``POST /runs/{run_id}/extraction/ai-verify/start-stream``  (SSE)
* ``GET  /runs/{run_id}/extraction/ai-verify/sessions``
* ``GET  /runs/{run_id}/extraction/ai-verify/sessions/{session_id}``

The per-run state-dir + per-session session-dir split mirrors
``ai_verify`` (commit beb3c2c, Rule W-14): verdict cache + accumulated
``runs/<ts>/`` artefacts live at the per-run root so opening the modal
again warm-hits prior Gemini judgements; the per-session subdir holds
only the filtered fixture and the SSE event log.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.extraction_approval import ExtractionApproval
from app.models.run import RunRecord
from app.pipeline import agent_actions, extraction_actions
from app.pipeline.agent_runner import (
    AgentEvent, build_filtered_fixture, list_sessions, locate_eval_agent,
    new_session_id, persist_session_event, read_session, read_run_verdicts,
    spawn_eval_agent_run, sse_stream,
)
from app.routers.runs import _lookup_run_with_access


logger = logging.getLogger(__name__)
router = APIRouter(tags=["extraction-verify"])


class StartRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=64)
    entity_ids: list[uuid.UUID] | None = None
    override_cache: bool = False
    tier_model: str | None = Field(default=None, max_length=64)


@router.get("/runs/{run_id}/extraction/ai-verify/actions")
async def list_available_actions(
    run_id: uuid.UUID,
    scope_kind: str = Query("selection", pattern=r"^(single|selection|all)$"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return [
        agent_actions.to_dict(a)
        for a in extraction_actions.list_actions(scope_kind=scope_kind)  # type: ignore[arg-type]
    ]


@router.post("/runs/{run_id}/extraction/ai-verify/start-stream")
async def start_stream(
    run_id: uuid.UUID,
    payload: StartRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _lookup_run_with_access(db, run_id, auth, write=False)

    action = extraction_actions.get_action(payload.action_id)
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown action_id {payload.action_id!r}",
        )

    entities = await _fetch_entities(db, run_id, payload.entity_ids)
    if not entities:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no extracted entities in scope",
        )
    if len(entities) < action.min_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"action {action.id!r} requires at least "
                f"{action.min_candidates} candidates; got {len(entities)}"
            ),
        )

    api_key = await _resolve_gemini_key(db, auth)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No Gemini API key configured. Open Settings -> "
                "Credentials and add one from "
                "https://aistudio.google.com/app/apikey."
            ),
        )

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
            entities=entities,
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
    entities: list[tuple[ExtractionApproval, RunRecord]],
    api_key: str,
    override_cache: bool,
    tier_model: str | None,
):
    eval_root = locate_eval_agent()
    state_dir = eval_root / "state" / "extraction-verify-sessions" / run_id
    session_dir = state_dir / "sessions" / session_id
    pipeline_output = session_dir / "pipeline-output"
    base = session_dir

    by_cn_ents: dict[str, list[dict[str, Any]]] = {}
    by_cn_genres: dict[str, list[dict[str, Any]]] = {}
    marc_by_cn: dict[str, dict[str, Any]] = {}
    for ext, record in entities:
        cn = ext.control_number
        marc_by_cn.setdefault(cn, dict(record.marc or {}))
        if ext.source == "genre":
            by_cn_genres.setdefault(cn, []).append({
                "label":      ext.text,
                "confidence": float(ext.model_confidence or ext.confidence or 0.0),
                "_entity_id": str(ext.id),
            })
        else:
            by_cn_ents.setdefault(cn, []).append(_approval_to_ner_shape(ext))

    ner_records: list[dict[str, Any]] = []
    for cn in sorted(set(marc_by_cn) | set(by_cn_ents) | set(by_cn_genres)):
        rec_marc = dict(marc_by_cn.get(cn) or {"_control_number": cn})
        rec_marc.setdefault("_control_number", cn)
        ner_records.append({
            "_control_number": cn,
            "text":            str(rec_marc.get("text") or ""),
            "entities":        by_cn_ents.get(cn, []),
            "ml_genres":       by_cn_genres.get(cn, []),
        })

    import json as _json

    build_filtered_fixture(
        dest_dir=pipeline_output,
        marc_records=list(marc_by_cn.values()) or [
            {"_control_number": cn} for cn in marc_by_cn
        ],
        authority_records=[],
    )
    (pipeline_output / "ner_results.json").write_text(
        _json.dumps(ner_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    start_ev = AgentEvent(
        type="session.start",
        payload={
            "session_id": session_id,
            "run_id":     run_id,
            "action_id":  action.id,
            "scope_size": len(entities),
            "scope_cn":   sorted(set(marc_by_cn) | set(by_cn_ents) | set(by_cn_genres)),
            "goal":       agent_actions.render_goal(action, n_candidates=len(entities)),
        },
    )
    persist_session_event(base, start_ev)
    yield start_ev

    try:
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
        on_disk_verdicts = read_run_verdicts(state_dir)
        for v in on_disk_verdicts:
            ev = AgentEvent(type="agent.verdict", payload=v)
            persist_session_event(base, ev)
            yield ev

        if on_disk_verdicts:
            try:
                await _persist_ai_verdicts_to_entities(
                    run_id=run_id,
                    session_id=session_id,
                    verdicts=on_disk_verdicts,
                )
            except Exception:
                logger.exception("failed to persist ai verdicts to entities")

        end_ev = AgentEvent(
            type="session.end",
            payload={
                "session_id": session_id,
                "scope_size": len(entities),
                "outcome":    "complete",
            },
        )
        persist_session_event(base, end_ev)
        yield end_ev


async def _persist_ai_verdicts_to_entities(
    *,
    run_id: str,
    session_id: str,
    verdicts: list[dict[str, Any]],
) -> None:
    from app.db import session_scope  # noqa: PLC0415

    summaries: dict[uuid.UUID, dict[str, Any]] = {}
    for v in verdicts:
        cand = (v.get("candidate") or {}) if isinstance(v, dict) else {}
        raw = cand.get("_entity_id") if isinstance(cand, dict) else None
        if not raw:
            continue
        try:
            eid = uuid.UUID(str(raw))
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
        summaries[eid] = summary

    if not summaries:
        return

    now = datetime.now(timezone.utc)
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(ExtractionApproval).where(
                    ExtractionApproval.run_id == uuid.UUID(run_id),
                    ExtractionApproval.id.in_(list(summaries.keys())),
                )
            )
        ).scalars().all()
        for ext in rows:
            ext.ai_verdict = summaries[ext.id]
            ext.ai_verdict_at = now
        await db.commit()


@router.get("/runs/{run_id}/extraction/ai-verify/sessions")
async def list_run_sessions(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return _list_extraction_sessions(str(run_id))


@router.get("/runs/{run_id}/extraction/ai-verify/sessions/{session_id}")
async def get_run_session(
    run_id: uuid.UUID,
    session_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    data = _read_extraction_session(str(run_id), session_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        )
    return data


def _list_extraction_sessions(run_id: str) -> list[dict[str, Any]]:
    try:
        root = locate_eval_agent()
    except FileNotFoundError:
        return []
    run_root = root / "state" / "extraction-verify-sessions" / run_id
    if not run_root.exists():
        return []
    new_sessions = run_root / "sessions"
    candidates = []
    if new_sessions.exists():
        candidates.extend(p for p in new_sessions.iterdir() if p.is_dir())
    out: list[dict[str, Any]] = []
    for child in sorted(candidates, key=lambda p: p.name, reverse=True):
        out.append({"session_id": child.name})
    out.extend(list_sessions(run_id))
    return out


def _read_extraction_session(
    run_id: str, session_id: str,
) -> dict[str, Any] | None:
    try:
        root = locate_eval_agent()
    except FileNotFoundError:
        return read_session(run_id, session_id)
    base = root / "state" / "extraction-verify-sessions" / run_id / "sessions" / session_id
    if not base.exists():
        return read_session(run_id, session_id)
    import json as _json

    events: list[dict[str, Any]] = []
    trace = base / "trace.jsonl"
    if trace.exists():
        with trace.open(encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue
    state_dir = base.parent.parent
    verdicts = read_run_verdicts(state_dir) or read_run_verdicts(base)
    return {
        "session_id": session_id,
        "run_id":     run_id,
        "events":     events,
        "verdicts":   verdicts,
    }


async def _fetch_entities(
    db: AsyncSession,
    run_id: uuid.UUID,
    entity_ids: list[uuid.UUID] | None,
) -> list[tuple[ExtractionApproval, RunRecord]]:
    q = select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
    if entity_ids:
        q = q.where(ExtractionApproval.id.in_(entity_ids))
    q = q.order_by(
        ExtractionApproval.control_number,
        ExtractionApproval.source,
        ExtractionApproval.text,
    )
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

    out: list[tuple[ExtractionApproval, RunRecord]] = []
    for ext in rows:
        rec = rec_by_cn.get(ext.control_number)
        if rec is None:
            rec = RunRecord(
                run_id=run_id, control_number=ext.control_number, marc={},
            )
        out.append((ext, rec))
    return out


def _approval_to_ner_shape(ext: ExtractionApproval) -> dict[str, Any]:
    return {
        "source":           ext.source,
        "text":             ext.text,
        "type":             ext.override_type or ext.type,
        "role":             ext.override_role or ext.role,
        "start":            int(ext.start or 0),
        "end":              int(ext.end or 0),
        "confidence":       ext.confidence,
        "model_confidence": ext.model_confidence,
        "_entity_id":       str(ext.id),
    }


async def _resolve_gemini_key(
    db: AsyncSession, auth: AuthContext,
) -> str | None:
    import os
    try:
        from app.pipeline.ai_verifier import (
            unwrap_user_gemini_key,
        )
        key = await unwrap_user_gemini_key(
            db, user_id=auth.user.id, kek=auth.kek,
        )
        if key:
            return key
    except Exception as exc:
        logger.warning("could not unwrap stored Gemini key: %s", exc)
    return os.environ.get("GEMINI_API_KEY")
