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
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session, session_scope
from app.models.extraction_approval import ExtractionApproval
from app.models.run import RunRecord
from app.models.run_job import JOB_KIND_NER_VERIFY
from app.pipeline import agent_actions, extraction_actions
from app.pipeline.agent_runner import (
    AgentEvent, build_filtered_fixture, list_sessions, list_verify_sessions,
    locate_eval_agent, new_session_id, persist_session_event, read_session,
    read_run_verdicts, read_verify_session, resolve_verify_session_dir,
    resolve_verify_state_dir, spawn_eval_agent_run, sse_stream,
)
from app.pipeline.inference_cache import read_from_inference_cache, write_to_inference_cache
from app.pipeline.extraction_entities_cache import invalidate_entities_cache
from app.pipeline.verify_session_store import load_verify_session
from app.pipeline.ner_verdict_cache import (
    ner_verdict_input_fingerprint,
    ner_verdict_query_summary,
)
from app.routers.runs import _lookup_run_with_access


logger = logging.getLogger(__name__)
router = APIRouter(tags=["extraction-verify"])


def _ext_content_id(ext: ExtractionApproval) -> str:
    from app.routers.extraction import _entity_id  # noqa: PLC0415
    return _entity_id(
        control_number=ext.control_number,
        source=ext.source,
        text=ext.text,
        start=int(ext.start or 0),
        end=int(ext.end or 0),
    )


class StartRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=64)
    # Content-hash base64 ids from the frontend's entity table — NOT
    # UUIDs. They decode via ``extraction._parse_entity_id`` to a
    # content-tuple (control_number, source, text, start, end) that
    # uniquely identifies the entity even when no ExtractionApproval
    # row has been created yet.
    entity_ids: list[str] | None = None
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
) -> StreamingResponse:
    """Kick off one AI verification session and stream events via SSE.

    Deliberately does NOT take ``db: AsyncSession = Depends(get_session)``:
    FastAPI only closes yield-dependencies once the whole response — for a
    ``StreamingResponse``, that means once ``_session_event_stream`` below
    is fully exhausted — has been sent. A verification session can run for
    many minutes (or hang forever if the eval-agent subprocess call never
    returns), so holding the request-scoped session open for that whole
    window pins one Postgres connection per in-flight session and, if the
    generator hangs, leaks it for good (see 2026-07-04 outage — a handful
    of hung sessions exhausted the whole ``pool_size=5 + max_overflow=10``
    budget and turned unrelated requests like ``/auth/login`` into 503s).
    All of the setup below runs inside its own short-lived
    ``session_scope()`` that commits and returns its connection to the
    pool before the streaming response is even constructed.
    """
    async with session_scope() as db:
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

    # locate_eval_agent() is intentionally NOT checked here. The cache
    # pre-check inside _session_event_stream may satisfy the entire
    # request without ever spawning a subprocess. Requiring the eval-agent
    # to be present even for fully-cached runs would break Heroku where
    # only the web dyno (not the sibling eval-agent repo) is deployed.
    # If uncached entities exist, the generator raises runner.error which
    # the SSE stream forwards to the client via sse_stream's producer().

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
    import json as _json  # noqa: PLC0415

    from app.db import session_scope  # noqa: PLC0415
    from app.pipeline.ai_verifier import GEMINI_MODEL  # noqa: PLC0415

    _judge_model = tier_model or GEMINI_MODEL

    # ── Pre-check inference cache ──────────────────────────────────────
    # Done BEFORE locate_eval_agent() so a fully-cached run never
    # requires the eval-agent subprocess to be present (e.g. Heroku).
    pre_cached: list[tuple[ExtractionApproval, RunRecord, dict[str, Any]]] = []
    uncached: list[tuple[ExtractionApproval, RunRecord]] = []
    if not override_cache:
        async with session_scope() as pre_db:
            for ext, record in entities:
                qs = ner_verdict_query_summary(ext, _judge_model)
                hit = await read_from_inference_cache(
                    pre_db, kind="ai_verdict", query_summary=qs,
                )
                if hit is not None:
                    pre_cached.append((ext, record, hit))
                else:
                    uncached.append((ext, record))
    else:
        uncached = list(entities)

    # ── Resolve eval-agent root (only needed for uncached entities) ────
    _ner_channel = "extraction-verify-sessions"
    state_dir = resolve_verify_state_dir(_ner_channel, run_id)
    session_dir = resolve_verify_session_dir(_ner_channel, run_id, session_id)
    pipeline_output = session_dir / "pipeline-output"
    base = session_dir
    session_dir.mkdir(parents=True, exist_ok=True)
    eval_agent_error: str | None = None

    if uncached:
        try:
            locate_eval_agent()
        except (FileNotFoundError, OSError, PermissionError) as exc:
            logger.warning(
                "ai-verify: eval-agent NOT located (run=%s): %s", run_id, exc,
            )
            eval_agent_error = str(exc)

    # ── Build fixture (only uncached entities) ─────────────────────────
    by_cn_ents: dict[str, list[dict[str, Any]]] = {}
    by_cn_genres: dict[str, list[dict[str, Any]]] = {}
    marc_by_cn: dict[str, dict[str, Any]] = {}
    for ext, record in uncached:
        cn = ext.control_number
        marc_by_cn.setdefault(cn, dict(record.marc or {}))
        if ext.source in ("genre", "genre_ml"):
            by_cn_genres.setdefault(cn, []).append({
                "label":      ext.text,
                "confidence": float(ext.model_confidence or ext.confidence or 0.0),
                "_entity_id": str(ext.id),
            })
        else:
            by_cn_ents.setdefault(cn, []).append(_approval_to_ner_shape(ext))

    if marc_by_cn or by_cn_ents or by_cn_genres:
        assert pipeline_output is not None
        ner_records: list[dict[str, Any]] = []
        for cn in sorted(set(marc_by_cn) | set(by_cn_ents) | set(by_cn_genres)):
            rec_marc = dict(marc_by_cn.get(cn) or {"_control_number": cn})
            rec_marc.setdefault("_control_number", cn)
            ner_records.append({
                "_control_number": cn,
                "text":       str(rec_marc.get("text") or ""),
                "entities":   by_cn_ents.get(cn, []),
                "ml_genres":  by_cn_genres.get(cn, []),
            })
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

    all_cns = sorted(
        {e.control_number for e, _ in entities}
    )
    start_ev = AgentEvent(
        type="session.start",
        payload={
            "session_id":  session_id,
            "run_id":      run_id,
            "action_id":   action.id,
            "scope_size":  len(entities),
            "scope_cn":    all_cns,
            "goal":        agent_actions.render_goal(action, n_candidates=len(entities)),
            "cache_hits":  len(pre_cached),
        },
    )
    persist_session_event(base, start_ev)
    yield start_ev

    # ── Emit pre-cached verdicts immediately ──────────────────────────
    for ext, _rec, cached_payload in pre_cached:
        synthetic = {
            "candidate": {
                "_entity_id": _ext_content_id(ext),
                "control_number": ext.control_number or "",
                "start":          int(ext.start or 0),
                "end":            int(ext.end or 0),
                "text":       ext.override_text or ext.text or "",
                "type":       ext.override_type or ext.type or "",
                "role":       ext.override_role or ext.role or "",
                "source":     ext.source or "",
            },
            "verdict":     cached_payload.get("verdict") or {},
            "judge_id":    cached_payload.get("judge_id"),
            "judged_at":   cached_payload.get("judged_at"),
            "cache_key":   cached_payload.get("cache_key"),
            "evaluator_id": cached_payload.get("evaluator"),
            "confidence":  cached_payload.get("confidence"),
            "record_id":   cached_payload.get("record_id") or ext.control_number or "",
            "sub_type":    cached_payload.get("sub_type") or ext.type or "",
            "from_inference_cache": True,
        }
        ev = AgentEvent(type="agent.verdict", payload=synthetic)
        persist_session_event(base, ev)
        yield ev

    # ── Subprocess for uncached entities ──────────────────────────────
    try:
        if uncached and eval_agent_error:
            # eval-agent missing (Heroku). Emit a warning so the UI
            # shows which entities couldn't be verified rather than
            # silently dropping them.
            warn_ev = AgentEvent(
                type="runner.warning",
                payload={
                    "message": (
                        f"{len(uncached)} entities are not in the verdict cache and "
                        "cannot be verified here — the eval-agent is not available on "
                        "this server. Run the verification locally to process them."
                    ),
                    "uncached_count": len(uncached),
                    "eval_agent_error": eval_agent_error,
                },
            )
            persist_session_event(base, warn_ev)
            yield warn_ev
        elif uncached:
            assert pipeline_output is not None and state_dir is not None
            logger.warning(
                "ai-verify: spawning eval-agent for %d uncached entities (run=%s)",
                len(uncached), run_id,
            )
            async for ev in spawn_eval_agent_run(
                pipeline_output=pipeline_output,
                evaluators=action.evaluators,
                api_key=api_key,
                state_dir=state_dir,
                tier_model=tier_model,
                override_cache=override_cache,
                rpm=action.rate_limit_rpm,
                # The curator hand-selected these entities for review, so
                # judge every one regardless of model confidence. Without
                # this the eval-agent's default 0.85 NER threshold silently
                # drops all low/medium-confidence selections → 0 verdicts.
                # Negative (not 0.0) to survive eval-agent's truthy-or guard.
                threshold=-1.0,
            ):
                persist_session_event(base, ev)
                yield ev
    finally:
        on_disk_verdicts = read_run_verdicts(state_dir) if (uncached and state_dir) else []
        for v in on_disk_verdicts:
            ev = AgentEvent(type="agent.verdict", payload=v)
            persist_session_event(base, ev)
            yield ev

        # ── Persist verdicts to ExtractionApproval rows ────────────
        # Two sources: freshly-produced on-disk verdicts AND verdicts
        # served from the inference cache.  The latter are persisted
        # here because an entity row may be newly-created (synthetic
        # upsert in _fetch_entities) or somehow missing its ai_verdict
        # column even though the cache has the result.
        pre_cached_as_verdicts = [
            {
                "candidate": {
                    "_entity_id": _ext_content_id(ext),
                    "control_number": ext.control_number or "",
                    "start":          int(ext.start or 0),
                    "end":            int(ext.end or 0),
                    "text":       ext.override_text or ext.text or "",
                    "type":       ext.override_type or ext.type or "",
                    "role":       ext.override_role or ext.role or "",
                    "source":     ext.source or "",
                },
                "verdict":      cached_payload.get("verdict") or {},
                "judge_id":     cached_payload.get("judge_id"),
                "judged_at":    cached_payload.get("judged_at"),
                "cache_key":    cached_payload.get("cache_key"),
                "evaluator_id": cached_payload.get("evaluator"),
                "confidence":   cached_payload.get("confidence"),
                "record_id":    cached_payload.get("record_id") or ext.control_number or "",
                "sub_type":     cached_payload.get("sub_type") or ext.type or "",
                "from_inference_cache": True,
            }
            for ext, _rec, cached_payload in pre_cached
        ]
        all_verdicts_to_persist = pre_cached_as_verdicts + on_disk_verdicts
        if all_verdicts_to_persist:
            try:
                await _persist_ai_verdicts_to_entities(
                    run_id=run_id,
                    session_id=session_id,
                    verdicts=all_verdicts_to_persist,
                    entities=[e for e, _ in entities],
                )
            except Exception:
                logger.exception("failed to persist ai verdicts to entities")

        if on_disk_verdicts:
            # Write new verdicts to the shared inference cache.
            try:
                await _write_ner_verdicts_to_cache(
                    entities=[e for e, _ in entities],
                    verdicts=on_disk_verdicts,
                )
            except Exception:
                logger.exception("failed to write ner verdicts to inference cache")

        end_ev = AgentEvent(
            type="session.end",
            payload={
                "session_id":    session_id,
                "scope_size":    len(entities),
                "cache_hits":    len(pre_cached),
                "fresh_verdicts": len(on_disk_verdicts),
                "uncached_skipped": len(uncached) if eval_agent_error else 0,
                "outcome":       "partial" if eval_agent_error and uncached else "complete",
            },
        )
        persist_session_event(base, end_ev)
        yield end_ev


async def _write_ner_verdicts_to_cache(
    *,
    entities: list[ExtractionApproval],
    verdicts: list[dict[str, Any]],
) -> None:
    """Persist newly-judged NER verdicts into the shared inference cache.

    The ``verdict`` sub-dict is stored verbatim, including
    ``suggested_fix`` (null or a fix object) so the frontend can
    display the fix without re-running the eval-agent.
    """
    from app.db import session_scope  # noqa: PLC0415

    by_uuid = {str(e.id): e for e in entities}
    by_hash = {_ext_content_id(e): e for e in entities}

    async with session_scope() as db:
        for v in verdicts:
            cand = v.get("candidate") if isinstance(v, dict) else None
            entity_id = cand.get("_entity_id") if isinstance(cand, dict) else None
            ext = (
                by_uuid.get(str(entity_id))
                or by_hash.get(str(entity_id))
                if entity_id
                else None
            )
            if ext is None:
                continue
            _jm = v.get("judge_id") or v.get("model") or "gemini-3.5-flash"
            qs = ner_verdict_query_summary(ext, _jm)
            fingerprint = ner_verdict_input_fingerprint(ext, _jm)
            verdict_dict = v.get("verdict") or {}
            cached_result = {
                "verdict":    verdict_dict,
                "judge_id":   v.get("judge_id") or v.get("model"),
                "judged_at":  v.get("judged_at"),
                "cache_key":  fingerprint,
                "evaluator":  v.get("evaluator_id") or v.get("evaluator"),
                "confidence": v.get("confidence"),
                "sub_type":   v.get("sub_type"),
                "record_id":  v.get("record_id"),
            }
            await write_to_inference_cache(
                db, kind="ai_verdict", query_summary=qs, result=cached_result,
            )


async def _persist_ai_verdicts_to_entities(
    *,
    run_id: str,
    session_id: str,
    verdicts: list[dict[str, Any]],
    entities: list[ExtractionApproval],
) -> None:
    from app.db import session_scope  # noqa: PLC0415

    by_hash = {_ext_content_id(e): e for e in entities}

    summaries: dict[uuid.UUID, dict[str, Any]] = {}
    for v in verdicts:
        cand = (v.get("candidate") or {}) if isinstance(v, dict) else {}
        raw = cand.get("_entity_id") if isinstance(cand, dict) else None
        if not raw:
            continue
        try:
            eid = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            ext = by_hash.get(str(raw))
            if ext is None:
                continue
            eid = ext.id
        vd = (v.get("verdict") or {}) if isinstance(v, dict) else {}
        suggested_fix = vd.get("suggested_fix")
        if suggested_fix is None and isinstance(cand, dict):
            suggested_fix = cand.get("suggested_fix")
        _jm = v.get("judge_id") or v.get("model") or "gemini-3.5-flash"
        ext_row = next((e for e in entities if e.id == eid), None)
        # Always normalise to our content fingerprint — eval-agent's
        # prompt-hash cache_key would fail sanitise_stale_ai_verdict on GET.
        if ext_row is not None:
            cache_key = ner_verdict_input_fingerprint(ext_row, str(_jm))
        else:
            cache_key = v.get("cache_key")
        summary = {
            "overall":       vd.get("overall"),
            "name_ok":       vd.get("name_ok"),
            "type_ok":       vd.get("type_ok"),
            "role_ok":       vd.get("role_ok"),
            "reasoning":     vd.get("reasoning"),
            "suggested_fix": suggested_fix,
            "model":         _jm,
            "judged_at":     v.get("judged_at"),
            "cache_key":     cache_key,
            "session_id":    session_id,
            "evaluator":     v.get("evaluator_id") or v.get("evaluator"),
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
    await invalidate_entities_cache(uuid.UUID(run_id))


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
    data = await load_verify_session(
        db,
        run_id=run_id,
        session_id=session_id,
        channel="extraction-verify-sessions",
        job_kind=JOB_KIND_NER_VERIFY,
    )
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        )
    return data


def _list_extraction_sessions(run_id: str) -> list[dict[str, Any]]:
    return list_verify_sessions("extraction-verify-sessions", run_id)


def _read_extraction_session(
    run_id: str, session_id: str,
) -> dict[str, Any] | None:
    return read_verify_session("extraction-verify-sessions", run_id, session_id)


async def _fetch_entities(
    db: AsyncSession,
    run_id: uuid.UUID,
    entity_ids: list[str] | None,
) -> list[tuple[ExtractionApproval, RunRecord]]:
    """Resolve scope → ``(ExtractionApproval, RunRecord)`` pairs.

    The frontend sends content-hash ids (the same key
    ``app.routers.extraction._entity_id`` emits on every row of
    ``GET /entities``). We:
      1. Decode each id → ``(control_number, source, text, start, end)``.
      2. Look up any existing ``ExtractionApproval`` rows by that
         5-tuple; create a synthetic in-memory row for any entity
         that doesn't have a persisted approval yet (curator hasn't
         clicked anything for it). The synthetic row is NOT committed
         — it just carries the fields the eval-agent fixture needs.
      3. Pull every needed ``RunRecord`` in one query for MARC context.

    When ``entity_ids`` is None → return every ``ExtractionApproval``
    row for the run + the matching RunRecord. (Existing-rows-only —
    a curator who scopes by "all" presumably wants whatever has been
    touched.)
    """
    from app.routers.extraction import (   # noqa: PLC0415
        _parse_entity_id, _flatten_records, _results_path,
    )
    import json as _json

    # — Branch A: ``entity_ids`` provided → decode + upsert-synthetic
    if entity_ids:
        # Dedupe + decode in one pass so the same content-hash sent
        # twice doesn't trigger a duplicate-key INSERT.
        seen_keys: set[tuple[str, str, str, int, int]] = set()
        decoded: list[tuple[str, str, str, int, int]] = []
        for eid in entity_ids:
            try:
                k = _parse_entity_id(eid)
            except Exception:  # noqa: BLE001
                continue
            if k in seen_keys:
                continue
            seen_keys.add(k)
            decoded.append(k)
        if not decoded:
            return []

        cns = sorted({k[0] for k in decoded})

        existing_rows = (
            await db.execute(
                select(ExtractionApproval).where(
                    ExtractionApproval.run_id == run_id,
                    ExtractionApproval.control_number.in_(cns),
                )
            )
        ).scalars().all()
        existing_by_key = {
            (r.control_number, r.source, r.text, r.start or 0, r.end or 0): r
            for r in existing_rows
        }

        # Snapshot the original prediction (type/role/confidence) from
        # ner_results.json so synthetic rows carry the prediction
        # context the eval-agent's fixture needs.
        snapshot: dict[tuple[str, str, str, int, int], dict] = {}
        path = _results_path(run_id)
        if path.exists():
            try:
                rec_data = _json.loads(path.read_text(encoding="utf-8"))
                if isinstance(rec_data, list):
                    for ent in _flatten_records(rec_data):
                        sk = (ent["control_number"], ent["source"], ent["text"],
                              int(ent.get("start") or 0), int(ent.get("end") or 0))
                        snapshot[sk] = ent
            except _json.JSONDecodeError:
                pass

        # Build a batch UPSERT for entities that don't have a row
        # yet. ``on_conflict_do_nothing`` handles the race where
        # another in-flight request just inserted the same key.
        from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

        missing = [k for k in decoded if k not in existing_by_key]
        if missing:
            rows_to_insert = []
            for (cn, src, text, start, end) in missing:
                snap = snapshot.get((cn, src, text, start, end), {})
                rows_to_insert.append({
                    "run_id":           run_id,
                    "control_number":   cn,
                    "source":           src,
                    "text":             text,
                    "start":            start,
                    "end":              end,
                    "type":             snap.get("type") or "",
                    "role":             snap.get("role") or "",
                    "confidence":       snap.get("confidence"),
                    "model_confidence": snap.get("model_confidence"),
                    "approved":         False,
                })
            stmt = pg_insert(ExtractionApproval).values(rows_to_insert)
            stmt = stmt.on_conflict_do_nothing(
                constraint="uq_extraction_approval_key",
            )
            await db.execute(stmt)
            await db.commit()

            # Re-read so we have the persisted UUID ids for the rows
            # we just inserted.
            refreshed = (
                await db.execute(
                    select(ExtractionApproval).where(
                        ExtractionApproval.run_id == run_id,
                        ExtractionApproval.control_number.in_(cns),
                    )
                )
            ).scalars().all()
            existing_by_key = {
                (r.control_number, r.source, r.text, r.start or 0, r.end or 0): r
                for r in refreshed
            }

        rec_rows = (
            await db.execute(
                select(RunRecord).where(
                    RunRecord.run_id == run_id,
                    RunRecord.control_number.in_(cns),
                )
            )
        ).scalars().all()
        rec_by_cn = {r.control_number: r for r in rec_rows}

        out: list[tuple[ExtractionApproval, RunRecord]] = []
        for key in decoded:
            ext = existing_by_key.get(key)
            if ext is None:
                # The upsert silently dropped (on_conflict_do_nothing)
                # AND the row wasn't there before — should be very
                # rare; skip to keep the rest of the scope alive.
                continue
            rec = rec_by_cn.get(key[0]) or RunRecord(
                run_id=run_id, control_number=key[0], marc={},
            )
            out.append((ext, rec))
        return out

    # — Branch B: ``entity_ids`` omitted → every existing row.
    q = (select(ExtractionApproval)
         .where(ExtractionApproval.run_id == run_id)
         .order_by(
             ExtractionApproval.control_number,
             ExtractionApproval.source,
             ExtractionApproval.text,
         ))
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
    return [(ext, rec_by_cn.get(ext.control_number) or RunRecord(
                 run_id=run_id, control_number=ext.control_number, marc={}))
            for ext in rows]


def _approval_to_ner_shape(ext: ExtractionApproval) -> dict[str, Any]:
    """Convert an ExtractionApproval row to the ner_results.json entity shape.

    The ``grounded`` field is derived from the DB-snapshotted ``exists_in``
    status so the eval-agent's grounding-signal prompt block is populated
    rather than falling back to the “no grounding signal” placeholder.
    This is the guardrail fix for the provenance/contents MARC-context bug:
    without this, verifiers running on DB-loaded entities had no F8 signal
    and could not substantiate suggested fixes.
    """
    # Judge the curator's effective text — the override when present (e.g.
    # after an Auto-fix), else the original NER span. This is what makes a
    # post-fix re-verification meaningful: the eval-agent scores the
    # corrected value, not the stale original.
    judged_text = ext.override_text or ext.text
    text_overridden = bool(ext.override_text and ext.override_text != ext.text)

    # Map DB exists_in status to a boolean grounded hint for the eval-agent.
    # ``exists_in`` was snapshotted for the ORIGINAL span; once the curator
    # overrides the text it no longer describes the judged value, so drop the
    # hint and let the judge search the MARC context fresh.
    _exists_in = ext.exists_in or ""
    if text_overridden:
        grounded_hint: bool | None = None
    elif _exists_in == "grounded":
        grounded_hint = True
    elif _exists_in in ("wrong_field", "novel"):
        grounded_hint = False
    else:
        grounded_hint = None  # unknown / not computed

    shape: dict[str, Any] = {
        "source":           ext.source,
        "text":             judged_text,
        "type":             ext.override_type or ext.type,
        "role":             ext.override_role or ext.role,
        "start":            int(ext.start or 0),
        "end":              int(ext.end or 0),
        "confidence":       ext.confidence,
        "model_confidence": ext.model_confidence,
        "_entity_id":       str(ext.id),
    }
    if grounded_hint is not None:
        shape["grounded"] = grounded_hint
    return shape


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
