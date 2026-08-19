"""Background AI verify jobs (NER, authority, Wikidata Studio)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from app.db import session_scope
from app.models.run_job import (
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_HMO_ITEM_VERIFY,
    JOB_KIND_NER_VERIFY,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.agent_runner import AgentEvent
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)
from app.pipeline.verify_session_store import (
    VERIFY_JOB_CHANNELS,
    slim_job_session_snapshot,
    snapshot_from_collected_events,
)
from app.pipeline.verify_resume import resumable_verify_result

logger = logging.getLogger(__name__)


def _terminal_snapshot(
    *,
    run_id: uuid.UUID,
    session_id: str,
    collected_events: list[dict[str, Any]],
    kind: str | None = None,
) -> dict[str, Any] | None:
    from app.pipeline.agent_runner import read_verify_session  # noqa: PLC0415

    channel = VERIFY_JOB_CHANNELS.get(kind or "")
    if channel:
        raw = read_verify_session(channel, str(run_id), session_id)
        if raw and raw.get("verdicts"):
            return slim_job_session_snapshot({
                "session_id": session_id,
                "run_id": str(run_id),
                "verdicts": raw["verdicts"],
                "events": [],
            })
    if not collected_events:
        return None
    return slim_job_session_snapshot(
        snapshot_from_collected_events(
            run_id=str(run_id),
            session_id=session_id,
            events=collected_events,
        ),
    )


def _interrupted_verify_result(
    *,
    run_id: uuid.UUID,
    session_id: str,
    judged: int,
    total: int,
    collected_events: list[dict[str, Any]],
    kind: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return resumable_verify_result(
        session_id=session_id,
        judged=judged,
        total=total,
        session_snapshot=_terminal_snapshot(
            run_id=run_id,
            session_id=session_id,
            collected_events=collected_events,
            kind=kind,
        ),
        interrupted=True,
        extra=extra,
    )


def _requires_gemini_key(tier_model: Any) -> bool:
    from app.pipeline.judge_models import resolve_tier1_model  # noqa: PLC0415

    spec = resolve_tier1_model(str(tier_model) if tier_model else None)
    return spec.provider == "gemini"


def _verdict_identity(ev: AgentEvent) -> str | None:
    """Stable candidate identity for idempotent job progress accounting."""
    payload = ev.payload or {}
    candidate = payload.get("candidate")
    sources = [candidate, payload] if isinstance(candidate, dict) else [payload]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("_local_id", "_item_id", "local_id", "id"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return None


def _progress_from_event(
    ev: AgentEvent,
    *,
    total: int,
    judged: int,
    session_id: str,
    cache_hits: int = 0,
) -> dict[str, Any]:
    payload = ev.payload or {}
    hits = cache_hits
    if ev.type in ("session.start", "session.end", "agent.stats"):
        raw = payload.get("cache_hits", payload.get("hits"))
        if raw is not None:
            try:
                hits = max(hits, int(raw))
            except (TypeError, ValueError):
                pass
    return {
        "phase": "running",
        "processed": judged,
        "total": total,
        "message": str(payload.get("message") or ev.type),
        "session_id": session_id,
        "last_event_type": ev.type,
        "cache_hits": hits,
    }


# Mid-run progress MUST stay counter-only on the 512 MB web dyno (Rule W-128).
# Embedding even throttled snapshots + full TRACE still R14'd (job ecfdcf29:
# H12 on job polls, then a ~1.8 MB terminal GET). Terminal result gets a slim
# verdicts-only snapshot; full evidence stays on disk / overrides / cache.
_PROGRESS_WRITE_INTERVAL_S = 2.0
_KEEP_COLLECTED_EVENT_TYPES = frozenset({
    "session.start",
    "session.end",
    "runner.error",
    "runner.warning",
    "runner.exit",
})


def _should_collect_event(ev: AgentEvent) -> bool:
    return ev.type in _KEEP_COLLECTED_EVENT_TYPES


def _progress_counters(
    ev: AgentEvent,
    *,
    total: int,
    judged: int,
    session_id: str,
    cache_hits: int = 0,
) -> dict[str, Any]:
    return _progress_from_event(
        ev, total=total, judged=judged, session_id=session_id, cache_hits=cache_hits,
    )


def _should_write_progress(
    ev: AgentEvent,
    *,
    last_write_at: list[float],
) -> bool:
    """Throttle mid-run DB progress writes; always flush framing events."""
    import time  # noqa: PLC0415

    if ev.type in (
        "session.start", "session.end", "runner.error", "runner.exit",
    ):
        last_write_at[0] = time.monotonic()
        return True
    now = time.monotonic()
    if (now - last_write_at[0]) >= _PROGRESS_WRITE_INTERVAL_S:
        last_write_at[0] = now
        return True
    return False


# Back-compat name used by unit tests (W-127 → W-128: mid-run never snapshots).
def _progress_with_snapshot(
    ev: AgentEvent,
    *,
    total: int,
    judged: int,
    session_id: str,
    run_id: uuid.UUID,
    collected_events: list[dict[str, Any]],
    kind: str | None = None,
    force_snapshot: bool = False,
    last_snapshot_at: list[float] | None = None,
    cache_hits: int = 0,
) -> dict[str, Any]:
    """Progress row for Postgres — counters only mid-run (Rule W-128)."""
    progress = _progress_counters(
        ev, total=total, judged=judged, session_id=session_id, cache_hits=cache_hits,
    )
    is_terminal = ev.type in ("session.end", "runner.error")
    if not (force_snapshot and is_terminal) and not is_terminal:
        return progress
    snap = _terminal_snapshot(
        run_id=run_id,
        session_id=session_id,
        collected_events=collected_events,
        kind=kind,
    )
    if snap is None:
        return progress
    progress["session_snapshot"] = snap
    if last_snapshot_at is not None:
        import time  # noqa: PLC0415
        last_snapshot_at[0] = time.monotonic()
    return progress


async def run_verify_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        kind = job.kind
        run_id = job.run_id
        params = dict(job.params or {})
        api_key = params.get("_api_key")
        session_id = str(params.get("session_id") or "")
        if _requires_gemini_key(params.get("tier_model")) and not api_key:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="missing Gemini API key")
            return
        if not session_id:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="missing session_id")
            return

    judged = 0
    total = 0
    cache_hits = 0
    error_message: str | None = None
    session_summary: dict[str, Any] = {}
    collected_events: list[dict[str, Any]] = []
    judged_candidate_ids: set[str] = set()
    last_snapshot_at: list[float] = [0.0]
    last_write_at: list[float] = [0.0]
    scope_state: dict[str, Any] = {"phase": "", "done": 0, "total": 0}
    await update_job_progress(
        job_id, _scope_progress(scope_state, session_id),
    )
    publisher = asyncio.create_task(
        _publish_scope_progress(job_id, scope_state, session_id),
    )
    try:
        stream = await _open_verify_stream(
            kind=kind,
            run_id=run_id,
            job_id=job_id,
            session_id=session_id,
            params=params,
            api_key=str(api_key),
            scope_state=scope_state,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("could not open verify job %s stream", job_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return
    finally:
        publisher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publisher
    if stream is None:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error="could not start verify stream")
        return

    await update_job_progress(job_id, {
        "phase": "running",
        "processed": 0,
        "total": 0,
        "message": "Starting verification…",
        "session_id": session_id,
    })

    try:
        async for ev in stream:
            if _should_collect_event(ev):
                collected_events.append({"type": ev.type, **dict(ev.payload or {})})
            if await is_cancel_requested(job_id):
                await stream.aclose()
                await finish_job(
                    job_id,
                    status=JOB_STATUS_CANCELLED,
                    result=_interrupted_verify_result(
                        run_id=run_id,
                        session_id=session_id,
                        judged=judged,
                        total=total,
                        collected_events=collected_events,
                        kind=kind,
                        extra={"cancelled": True},
                    ),
                    progress={
                        "phase": "cancelled",
                        "processed": judged,
                        "total": total,
                        "message": "Cancelled by user",
                        "session_id": session_id,
                    },
                )
                return

            if ev.type == "session.start":
                total = int((ev.payload or {}).get("scope_size") or total)
                try:
                    cache_hits = max(
                        cache_hits,
                        int((ev.payload or {}).get("cache_hits") or 0),
                    )
                except (TypeError, ValueError):
                    pass
            if ev.type == "agent.stats":
                payload = ev.payload or {}
                raw_hits = payload.get("cache_hits", payload.get("hits"))
                if raw_hits is not None:
                    try:
                        cache_hits = max(cache_hits, int(raw_hits))
                    except (TypeError, ValueError):
                        pass
            if ev.type == "runner.error":
                # Keep draining until session.end so TRACE verdicts + partial
                # outcome are recorded (Rule W-126). Only fail hard when the
                # stream dies without a session.end framing event.
                error_message = str((ev.payload or {}).get("message") or "verify failed")
            if ev.type == "agent.verdict":
                candidate_id = _verdict_identity(ev)
                if candidate_id is not None:
                    judged_candidate_ids.add(candidate_id)
                    candidate_count = len(judged_candidate_ids)
                    judged = min(candidate_count, total) if total else candidate_count

            if _should_write_progress(ev, last_write_at=last_write_at):
                await update_job_progress(
                    job_id,
                    _progress_with_snapshot(
                        ev,
                        total=total,
                        judged=judged,
                        session_id=session_id,
                        run_id=run_id,
                        collected_events=collected_events,
                        kind=kind,
                        last_snapshot_at=last_snapshot_at,
                        cache_hits=cache_hits,
                    ),
                )

            if ev.type == "session.end":
                session_summary = dict(ev.payload or {})
                break
    except Exception as exc:  # noqa: BLE001
        logger.exception("verify job %s failed", job_id)
        await finish_job(
            job_id,
            status=JOB_STATUS_FAILED,
            error=str(exc),
            result=_interrupted_verify_result(
                run_id=run_id,
                session_id=session_id,
                judged=judged,
                total=total,
                collected_events=collected_events,
                kind=kind,
                extra={"runner_error": str(exc)},
            ),
            progress={
                "phase": "failed",
                "processed": judged,
                "total": total or judged,
                "message": str(exc),
                "session_id": session_id,
            },
        )
        return

    if error_message and not session_summary:
        await finish_job(
            job_id,
            status=JOB_STATUS_FAILED,
            error=error_message,
            result=_interrupted_verify_result(
                run_id=run_id,
                session_id=session_id,
                judged=judged,
                total=total,
                collected_events=collected_events,
                kind=kind,
                extra={"runner_error": error_message},
            ),
            progress={
                "phase": "failed",
                "processed": judged,
                "total": total or judged,
                "message": error_message,
                "session_id": session_id,
            },
        )
        return

    session_snapshot = _terminal_snapshot(
        run_id=run_id,
        session_id=session_id,
        collected_events=collected_events,
        kind=kind,
    )

    outcome = session_summary.get("outcome")
    result_body: dict[str, Any] = {
        "session_id": session_id,
        "judged": judged,
        "total": total or judged,
        "outcome": outcome,
        "cache_hits": session_summary.get("cache_hits"),
        "fresh_verdicts": session_summary.get("fresh_verdicts"),
        "uncached_skipped": session_summary.get("uncached_skipped"),
        "unverifiable_no_id": session_summary.get("unverifiable_no_id"),
        "runner_error": session_summary.get("runner_error"),
        "session_snapshot": session_snapshot,
    }
    if outcome == "partial" or (total > 0 and judged < total):
        result_body["resumable"] = judged > 0
        result_body["remaining"] = max(0, (total or judged) - judged)

    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result=result_body,
        progress={
            "phase": "done",
            "processed": judged,
            "total": total or judged,
            "message": "Verification complete",
            "session_id": session_id,
        },
    )


SCOPE_PROGRESS_INTERVAL_SECONDS = 1.5


def _scope_progress(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    """1-based step progress for scope preparation (Rules W-112 / W-113).

    A single static "Loading Studio scope…" with `total: 0` made two minutes of
    real work indistinguishable from a hang — which is how the duplicate-probe
    429 stall was first reported, twice.
    """
    from app.routers.wikidata_studio import VERIFY_SCOPE_PHASES  # noqa: PLC0415

    label = str(state.get("phase") or "")
    step = (VERIFY_SCOPE_PHASES.index(label) + 1) if label in VERIFY_SCOPE_PHASES else 1
    total_steps = len(VERIFY_SCOPE_PHASES)
    message = (
        f"Step {step} of {total_steps}: {label}" if label else "Loading Studio scope…"
    )
    progress: dict[str, Any] = {
        "phase": "preparing",
        "processed": 0,
        "total": 0,
        "step": step,
        "step_total": total_steps,
        "message": message,
        "session_id": session_id,
    }
    done, total = int(state.get("done") or 0), int(state.get("total") or 0)
    if total:
        progress.update(
            sub_processed=min(done, total),
            sub_total=total,
            sub_unit="lookups",
            sub_message=f"{min(done, total)} of {total} lookups",
        )
    return progress


async def _publish_scope_progress(
    job_id: uuid.UUID,
    state: dict[str, Any],
    session_id: str,
) -> None:
    """Own every DB write for scope progress; the callbacks only mutate state."""
    last: tuple[Any, int] | None = None
    while True:
        await asyncio.sleep(SCOPE_PROGRESS_INTERVAL_SECONDS)
        fingerprint = (state.get("phase"), int(state.get("done") or 0))
        if fingerprint == last:
            continue
        last = fingerprint
        await update_job_progress(job_id, _scope_progress(state, session_id))


async def _open_verify_stream(
    *,
    kind: str,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    session_id: str,
    params: dict[str, Any],
    api_key: str,
    scope_state: dict[str, Any] | None = None,
):
    override_cache = bool(params.get("override_cache"))
    tier_model = params.get("tier_model")

    async with session_scope() as db:
        if kind == JOB_KIND_AUTHORITY_VERIFY:
            from app.pipeline import agent_actions  # noqa: PLC0415
            from app.routers.ai_verify import (  # noqa: PLC0415
                _fetch_matches,
                _session_event_stream,
            )

            action = agent_actions.get_action(str(params["action_id"]))
            if action is None:
                return None
            raw_ids = params.get("match_ids")
            match_ids = [uuid.UUID(str(x)) for x in raw_ids] if raw_ids else None
            matches = await _fetch_matches(db, run_id, match_ids)
            return _session_event_stream(
                run_id=str(run_id),
                session_id=session_id,
                action=action,
                matches=matches,
                api_key=api_key,
                override_cache=override_cache,
                tier_model=tier_model,
            )

        if kind == JOB_KIND_NER_VERIFY:
            from app.pipeline import extraction_actions  # noqa: PLC0415
            from app.routers.extraction_verify import (  # noqa: PLC0415
                _fetch_entities,
                _session_event_stream,
            )

            action = extraction_actions.get_action(str(params["action_id"]))
            if action is None:
                return None
            entities = await _fetch_entities(db, run_id, params.get("entity_ids"))
            return _session_event_stream(
                run_id=str(run_id),
                session_id=session_id,
                action=action,
                entities=entities,
                api_key=api_key,
                override_cache=override_cache,
                tier_model=tier_model,
            )

        if kind == JOB_KIND_WIKIDATA_VERIFY:
            from types import SimpleNamespace  # noqa: PLC0415

            from app.pipeline import wikidata_actions  # noqa: PLC0415
            from app.pipeline.ai_verifier import GEMINI_MODEL  # noqa: PLC0415
            from app.pipeline.wikidata_verdict_cache import (  # noqa: PLC0415
                attach_wikidata_marc_context,
            )
            from app.routers.wikidata_studio import (  # noqa: PLC0415
                _fetch_wikidata_verify_items,
                _wikidata_verify_event_stream,
            )

            action = wikidata_actions.get_action(str(params["action_id"]))
            if action is None:
                return None
            job = await db.get(RunJob, job_id)
            auth = SimpleNamespace(user=SimpleNamespace(id=job.created_by if job else None))
            state = scope_state if scope_state is not None else {}

            def on_phase(label: str) -> None:
                state["phase"] = label
                state["done"], state["total"] = 0, 0

            def on_lookups(done: int, total: int) -> None:
                state["done"], state["total"] = done, total

            items, marc_records = await _fetch_wikidata_verify_items(
                db, run_id,
                auth,
                item_ids=params.get("item_ids"),
                approved_only=bool(params.get("approved_only", False)),
                source=str(params.get("source") or "canonical"),
                phase_cb=on_phase,
                progress_cb=on_lookups,
            )
            from app.routers.wikidata_studio import _prepare_wikidata_verify_scope  # noqa: PLC0415

            items = await _prepare_wikidata_verify_scope(action, items)
            if not items:
                wanted = params.get("item_ids") or []
                detail = (
                    "no Wikidata Studio items with an existing QID in scope"
                    if action.id == "autofix_from_wikidata"
                    else (
                        "no Wikidata Studio items in scope "
                        f"(source={params.get('source') or 'canonical'!r}, "
                        f"requested={len(wanted) if isinstance(wanted, list) else 0})"
                    )
                )
                raise ValueError(detail)
            if len(items) < action.min_candidates:
                raise ValueError(
                    f"action requires at least {action.min_candidates} candidates",
                )
            attach_wikidata_marc_context(items, marc_records)
            judge_model = tier_model or GEMINI_MODEL
            evaluator_id = action.evaluators[0] if action.evaluators else "wikidata_item"
            from app.pipeline.wikidata_verify_scope import (  # noqa: PLC0415
                partition_wikidata_verify_cache,
            )

            pre_cached, uncached, _cache_stats = await partition_wikidata_verify_cache(
                db, items,
                judge_model=judge_model,
                evaluator_id=evaluator_id,
                override_cache=override_cache,
            )
            return _wikidata_verify_event_stream(
                run_id=str(run_id),
                session_id=session_id,
                action=action,
                items=items,
                uncached_items=uncached,
                pre_cached=pre_cached,
                marc_records=marc_records,
                api_key=api_key,
                override_cache=override_cache,
                tier_model=tier_model,
            )

        if kind == JOB_KIND_HMO_ITEM_VERIFY:
            from app.pipeline import hmo_item_actions  # noqa: PLC0415
            from app.pipeline.ai_verifier import GEMINI_MODEL  # noqa: PLC0415
            from app.pipeline.hmo_item_verdict_cache import (
                hmo_item_verdict_query_summary,  # noqa: PLC0415
            )
            from app.pipeline.hmo_item_verify import hmo_item_verify_event_stream  # noqa: PLC0415
            from app.pipeline.inference_cache import read_from_inference_cache  # noqa: PLC0415
            from app.pipeline.marc_verify_context import attach_marc_context  # noqa: PLC0415
            from app.routers.hmo_studio_items import (  # noqa: PLC0415
                _fetch_verify_items,
                _load_marc_records,
                _prepare_verify_scope,
            )

            action = hmo_item_actions.get_action(str(params["action_id"]))
            if action is None:
                return None
            items = await _fetch_verify_items(db, run_id, item_ids=params.get("item_ids"))
            items = await _prepare_verify_scope(action, items)
            if not items:
                return None
            judge_model = tier_model or GEMINI_MODEL
            evaluator_id = action.evaluators[0] if action.evaluators else "hmo_wikibase_item"
            pre_cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
            uncached: list[dict[str, Any]] = []
            if not override_cache:
                attach_marc_context(items, await _load_marc_records(db, run_id))
                for item in items:
                    hit = await read_from_inference_cache(
                        db,
                        kind="ai_verdict",
                        query_summary=hmo_item_verdict_query_summary(
                            item, judge_model, evaluator=evaluator_id,
                        ),
                    )
                    if hit is not None:
                        pre_cached.append((item, hit))
                    else:
                        uncached.append(item)
            else:
                uncached = list(items)
            marc_records = await _load_marc_records(db, run_id)
            return hmo_item_verify_event_stream(
                run_id=str(run_id),
                session_id=session_id,
                action=action,
                items=items,
                uncached_items=uncached,
                pre_cached=pre_cached,
                marc_records=marc_records,
                api_key=api_key,
                override_cache=override_cache,
                tier_model=tier_model,
            )

    return None
