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
from app.db import get_session
from app.models.extraction_approval import ExtractionApproval
from app.models.run import RunRecord
from app.pipeline import agent_actions, extraction_actions
from app.pipeline.agent_runner import (
    AgentEvent, build_filtered_fixture, list_sessions, locate_eval_agent,
    new_session_id, persist_session_event, read_session, read_run_verdicts,
    spawn_eval_agent_run, sse_stream,
)
from app.pipeline.inference_cache import read_from_inference_cache, write_to_inference_cache
from app.routers.runs import _lookup_run_with_access


logger = logging.getLogger(__name__)
router = APIRouter(tags=["extraction-verify"])


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

    # ── Pre-check inference cache ──────────────────────────────────────
    # Done BEFORE locate_eval_agent() so a fully-cached run never
    # requires the eval-agent subprocess to be present (e.g. Heroku).
    pre_cached: list[tuple[ExtractionApproval, RunRecord, dict[str, Any]]] = []
    uncached: list[tuple[ExtractionApproval, RunRecord]] = []
    if not override_cache:
        async with session_scope() as pre_db:
            for ext, record in entities:
                qs = _ner_verdict_query_summary(ext)
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
    eval_root: Path | None = None
    state_dir: Path | None = None
    session_dir: Path | None = None
    pipeline_output: Path | None = None
    base: Path | None = None
    if uncached:
        eval_root = locate_eval_agent()
        state_dir = eval_root / "state" / "extraction-verify-sessions" / run_id
        session_dir = state_dir / "sessions" / session_id
        pipeline_output = session_dir / "pipeline-output"
        base = session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Cache-only run: create a temp session dir for the audit log.
        import tempfile  # noqa: PLC0415
        _tmp = Path(tempfile.mkdtemp(prefix=f"mhm-ner-{session_id}-"))
        base = _tmp

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
                "_entity_id": str(ext.id),
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

            # Write new verdicts to the shared inference cache.
            try:
                await _write_ner_verdicts_to_cache(
                    entities_by_id={str(e.id): e for e, _ in uncached},
                    verdicts=on_disk_verdicts,
                )
            except Exception:
                logger.exception("failed to write ner verdicts to inference cache")

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


def _ner_verdict_query_summary(ext: ExtractionApproval) -> dict[str, Any]:
    """Stable content key for caching an NER verdict across users/runs.

    Keyed by the entity's canonical text + type + role. Two curators
    verifying the same extracted entity on the same MARC record will share
    the cached Gemini verdict.
    """
    return {
        "text": (ext.override_text or ext.text or "").strip(),
        "type": (ext.override_type or ext.type or "").strip(),
        "role": (ext.override_role or ext.role or "").strip(),
    }


async def _write_ner_verdicts_to_cache(
    *,
    entities_by_id: dict[str, ExtractionApproval],
    verdicts: list[dict[str, Any]],
) -> None:
    """Persist newly-judged NER verdicts into the shared inference cache."""
    from app.db import session_scope  # noqa: PLC0415

    async with session_scope() as db:
        for v in verdicts:
            cand = v.get("candidate") if isinstance(v, dict) else None
            entity_id = cand.get("_entity_id") if isinstance(cand, dict) else None
            ext = entities_by_id.get(str(entity_id)) if entity_id else None
            if ext is None:
                continue
            qs = _ner_verdict_query_summary(ext)
            cached_result = {
                "verdict":    v.get("verdict") or {},
                "judge_id":   v.get("judge_id") or v.get("model"),
                "judged_at":  v.get("judged_at"),
                "cache_key":  v.get("cache_key"),
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
