"""Background AI verify jobs (NER, authority, Wikidata Studio)."""

from __future__ import annotations

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
from app.pipeline.verify_session_store import snapshot_from_collected_events

logger = logging.getLogger(__name__)


def _progress_from_event(
    ev: AgentEvent,
    *,
    total: int,
    judged: int,
    session_id: str,
) -> dict[str, Any]:
    payload = ev.payload or {}
    if ev.type == "agent.stats":
        judged = int(payload.get("judged") or judged)
    if ev.type == "agent.verdict":
        judged += 1
    return {
        "phase": "running",
        "processed": judged,
        "total": total,
        "message": str(payload.get("message") or ev.type),
        "session_id": session_id,
        "last_event_type": ev.type,
    }


def _progress_with_snapshot(
    ev: AgentEvent,
    *,
    total: int,
    judged: int,
    session_id: str,
    run_id: uuid.UUID,
    collected_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Progress row for Postgres — includes partial session_snapshot for live UI."""
    progress = _progress_from_event(
        ev, total=total, judged=judged, session_id=session_id,
    )
    if collected_events:
        progress["session_snapshot"] = snapshot_from_collected_events(
            run_id=str(run_id),
            session_id=session_id,
            events=collected_events,
        )
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
        if not api_key:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="missing Gemini API key")
            return
        if not session_id:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="missing session_id")
            return

    judged = 0
    total = 0
    error_message: str | None = None
    session_summary: dict[str, Any] = {}
    collected_events: list[dict[str, Any]] = []
    stream = await _open_verify_stream(
        kind=kind,
        run_id=run_id,
        job_id=job_id,
        session_id=session_id,
        params=params,
        api_key=str(api_key),
    )
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
            collected_events.append({"type": ev.type, **(ev.payload or {})})
            if await is_cancel_requested(job_id):
                await stream.aclose()
                await finish_job(
                    job_id,
                    status=JOB_STATUS_CANCELLED,
                    result={"session_id": session_id, "judged": judged},
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
            if ev.type == "runner.error":
                error_message = str((ev.payload or {}).get("message") or "verify failed")
                break
            if ev.type == "agent.stats":
                judged = int((ev.payload or {}).get("judged") or judged)
            elif ev.type == "agent.verdict":
                judged += 1

            await update_job_progress(
                job_id,
                _progress_with_snapshot(
                    ev,
                    total=total,
                    judged=judged,
                    session_id=session_id,
                    run_id=run_id,
                    collected_events=collected_events,
                ),
            )

            if ev.type == "session.end":
                session_summary = dict(ev.payload or {})
                break
    except Exception as exc:  # noqa: BLE001
        logger.exception("verify job %s failed", job_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    if error_message:
        await finish_job(
            job_id,
            status=JOB_STATUS_FAILED,
            error=error_message,
            result={"session_id": session_id, "judged": judged},
        )
        return

    session_snapshot = snapshot_from_collected_events(
        run_id=str(run_id),
        session_id=session_id,
        events=collected_events,
    )

    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={
            "session_id": session_id,
            "judged": judged,
            "total": total or judged,
            "outcome": session_summary.get("outcome"),
            "cache_hits": session_summary.get("cache_hits"),
            "fresh_verdicts": session_summary.get("fresh_verdicts"),
            "uncached_skipped": session_summary.get("uncached_skipped"),
            "unverifiable_no_id": session_summary.get("unverifiable_no_id"),
            "session_snapshot": session_snapshot,
        },
        progress={
            "phase": "done",
            "processed": judged,
            "total": total or judged,
            "message": "Verification complete",
            "session_id": session_id,
        },
    )


async def _open_verify_stream(
    *,
    kind: str,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    session_id: str,
    params: dict[str, Any],
    api_key: str,
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
            from app.pipeline.inference_cache import read_from_inference_cache  # noqa: PLC0415
            from app.pipeline.marc_verify_context import attach_marc_context  # noqa: PLC0415
            from app.pipeline.wikidata_verdict_cache import wikidata_verdict_query_summary  # noqa: PLC0415
            from app.routers.wikidata_studio import (  # noqa: PLC0415
                _fetch_wikidata_verify_items,
                _wikidata_verify_event_stream,
            )

            action = wikidata_actions.get_action(str(params["action_id"]))
            if action is None:
                return None
            job = await db.get(RunJob, job_id)
            auth = SimpleNamespace(user=SimpleNamespace(id=job.created_by if job else None))
            items, marc_records = await _fetch_wikidata_verify_items(
                db, run_id,
                auth,
                item_ids=params.get("item_ids"),
                approved_only=bool(params.get("approved_only", True)),
            )
            from app.routers.wikidata_studio import _prepare_wikidata_verify_scope  # noqa: PLC0415

            items = await _prepare_wikidata_verify_scope(action, items)
            if not items:
                return None
            attach_marc_context(items, marc_records)
            judge_model = tier_model or GEMINI_MODEL
            evaluator_id = action.evaluators[0] if action.evaluators else "wikidata_item"
            pre_cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
            uncached: list[dict[str, Any]] = []
            if not override_cache:
                for item in items:
                    hit = await read_from_inference_cache(
                        db,
                        kind="ai_verdict",
                        query_summary=wikidata_verdict_query_summary(
                            item, judge_model, evaluator=evaluator_id,
                        ),
                    )
                    if hit is not None:
                        pre_cached.append((item, hit))
                    else:
                        uncached.append(item)
            else:
                uncached = list(items)
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
            from app.pipeline.hmo_item_verdict_cache import hmo_item_verdict_query_summary  # noqa: PLC0415
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
