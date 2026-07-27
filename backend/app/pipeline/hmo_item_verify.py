"""AI verification stream for HMO Wikibase Studio items."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.pipeline import agent_actions, hmo_item_actions
from app.pipeline.agent_runner import (
    AgentEvent,
    locate_eval_agent,
    persist_session_event,
    read_run_verdicts,
    resolve_verify_session_dir,
    resolve_verify_state_dir,
    spawn_eval_agent_run,
)
from app.pipeline.hmo_item_verdict_cache import (
    hmo_item_verdict_input_fingerprint,
    hmo_item_verdict_query_summary,
)
from app.pipeline.inference_cache import write_to_inference_cache

logger = logging.getLogger(__name__)

_HMO_ITEM_VERIFY_CHANNEL = "hmo-item-verify-sessions"
HMO_ITEM_VERIFY_CHANNEL = _HMO_ITEM_VERIFY_CHANNEL


def write_hmo_item_verify_fixture(
    *,
    dest_dir: Path,
    marc_records: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "marc_extracted.json").write_text(
        json.dumps(marc_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (dest_dir / "hmo_wikibase_items.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# Re-export for callers that still import from hmo_item_verify.
__all__ = [
    "HMO_ITEM_VERIFY_CHANNEL",
    "cached_hmo_item_verdict_event",
    "hmo_item_verdict_query_summary",
    "hmo_item_verify_event_stream",
]


def cached_hmo_item_verdict_event(
    item: dict[str, Any],
    cached_payload: dict[str, Any],
) -> dict[str, Any]:
    local_id = str(item.get("_local_id") or item.get("local_id") or "")
    return {
        "candidate": {**item, "_local_id": local_id, "label": item.get("label")},
        "verdict": cached_payload.get("verdict") or {},
        "judge_id": cached_payload.get("judge_id"),
        "judged_at": cached_payload.get("judged_at"),
        "cache_key": cached_payload.get("cache_key"),
    }


async def _persist_hmo_item_verdicts(
    *,
    run_id: UUID,
    items_by_id: dict[str, dict[str, Any]],
    verdicts: list[dict[str, Any]],
    judge_model: str,
) -> None:
    from app.db import session_scope  # noqa: PLC0415
    from app.models.hmo_studio_item_override import HmoStudioItemOverride  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    async with session_scope() as db:
        for v in verdicts:
            cand = v.get("candidate") if isinstance(v, dict) else None
            local_id = ""
            if isinstance(cand, dict):
                local_id = str(
                    cand.get("_local_id") or cand.get("local_id") or "",
                )
            item = items_by_id.get(local_id)
            if item is None:
                continue
            model = str(v.get("judge_id") or v.get("model") or judge_model)
            evaluator_id = str(
                v.get("evaluator_id") or v.get("evaluator") or "hmo_wikibase_item",
            )
            verdict_body = v.get("verdict") or {}
            fingerprint = hmo_item_verdict_input_fingerprint(
                item,
                model,
                evaluator=evaluator_id,
            )
            summary = {
                "overall": verdict_body.get("overall") or "unknown",
                "name_ok": verdict_body.get("name_ok"),
                "type_ok": verdict_body.get("type_ok"),
                "role_ok": verdict_body.get("role_ok"),
                "reasoning": verdict_body.get("reasoning"),
                "model": model,
                "judged_at": v.get("judged_at"),
                "cache_key": fingerprint,
                "session_id": None,
                "evaluator": evaluator_id,
            }
            if evaluator_id == "hmo_wikibase_item_autofix":
                fixes = verdict_body.get("suggested_fixes") or cand.get("suggested_fixes")
                if fixes:
                    summary["suggested_fixes"] = fixes

            row = (
                await db.execute(
                    select(HmoStudioItemOverride).where(
                        HmoStudioItemOverride.run_id == run_id,
                        HmoStudioItemOverride.local_id == local_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = HmoStudioItemOverride(run_id=run_id, local_id=local_id)
                db.add(row)
            row.ai_verdict = summary
            row.ai_verdict_at = now

            cached_result = {
                "verdict": verdict_body,
                "judge_id": model,
                "judged_at": v.get("judged_at"),
                "cache_key": v.get("cache_key"),
                "evaluator": evaluator_id,
            }
            await write_to_inference_cache(
                db,
                kind="ai_verdict",
                query_summary=hmo_item_verdict_query_summary(
                    item, model, evaluator=evaluator_id,
                ),
                result=cached_result,
            )
        await db.commit()


async def hmo_item_verify_event_stream(
    *,
    run_id: str,
    session_id: str,
    action: agent_actions.AgentAction,
    items: list[dict[str, Any]],
    uncached_items: list[dict[str, Any]],
    pre_cached: list[tuple[dict[str, Any], dict[str, Any]]],
    marc_records: list[dict[str, Any]],
    api_key: str,
    override_cache: bool,
    tier_model: str | None,
) -> AsyncIterator[AgentEvent]:
    state_dir = resolve_verify_state_dir(_HMO_ITEM_VERIFY_CHANNEL, run_id)
    session_dir = resolve_verify_session_dir(_HMO_ITEM_VERIFY_CHANNEL, run_id, session_id)
    pipeline_output = session_dir / "pipeline-output"
    session_dir.mkdir(parents=True, exist_ok=True)
    eval_agent_error: str | None = None
    streamed_fresh_verdicts: list[dict[str, Any]] = []
    streamed_fresh_verdict_keys: set[str] = set()
    runner_error: str | None = None
    runner_exit_code: int | None = None

    if uncached_items:
        try:
            locate_eval_agent()
        except (FileNotFoundError, OSError, PermissionError) as exc:
            eval_agent_error = str(exc)

    start_ev = AgentEvent(
        type="session.start",
        payload={
            "session_id": session_id,
            "run_id": run_id,
            "action_id": action.id,
            "scope_size": len(items),
            "scope_item_ids": sorted(str(i.get("_local_id") or i.get("local_id") or "") for i in items),
            "goal": agent_actions.render_goal(action, n_candidates=len(items)),
            "cache_hits": len(pre_cached),
        },
    )
    persist_session_event(session_dir, start_ev)
    yield start_ev

    for item, cached_payload in pre_cached:
        ev = AgentEvent(
            type="agent.verdict",
            payload=cached_hmo_item_verdict_event(item, cached_payload),
        )
        persist_session_event(session_dir, ev)
        yield ev

    try:
        if eval_agent_error:
            warn_ev = AgentEvent(
                type="runner.warning",
                payload={
                    "message": (
                        f"{len(uncached_items)} HMO items cannot be verified here "
                        "because the eval-agent is not available on this server."
                    ),
                    "uncached_count": len(uncached_items),
                    "eval_agent_error": eval_agent_error,
                },
            )
            persist_session_event(session_dir, warn_ev)
            yield warn_ev
        elif uncached_items:
            write_hmo_item_verify_fixture(
                dest_dir=pipeline_output,
                marc_records=marc_records,
                items=uncached_items,
            )
            async for ev in spawn_eval_agent_run(
                pipeline_output=pipeline_output,
                evaluators=action.evaluators,
                api_key=api_key,
                state_dir=state_dir,
                tier_model=tier_model,
                override_cache=override_cache,
                rpm=action.rate_limit_rpm,
            ):
                persist_session_event(session_dir, ev)
                yield ev
                if ev.type == "agent.verdict":
                    from app.pipeline.verify_outcome import (  # noqa: PLC0415
                        verdict_candidate_local_id,
                    )

                    payload = dict(ev.payload or {})
                    local_id = verdict_candidate_local_id(payload)
                    if local_id:
                        streamed_fresh_verdict_keys.add(local_id)
                        streamed_fresh_verdicts.append(payload)
                elif ev.type == "runner.error":
                    runner_error = str((ev.payload or {}).get("message") or "verify failed")
                elif ev.type == "runner.exit":
                    raw_rc = (ev.payload or {}).get("return_code")
                    try:
                        runner_exit_code = int(raw_rc) if raw_rc is not None else None
                    except (TypeError, ValueError):
                        runner_exit_code = None
    finally:
        from app.pipeline.verify_outcome import (  # noqa: PLC0415
            merge_fresh_verdicts,
            resolve_verify_session_outcome,
            verdict_candidate_local_id,
        )

        on_disk_verdicts = (
            read_run_verdicts(state_dir) if (uncached_items and not eval_agent_error) else []
        )
        fresh_verdicts = merge_fresh_verdicts(
            streamed=streamed_fresh_verdicts,
            on_disk=on_disk_verdicts,
        )
        items_by_id = {
            str(i.get("_local_id") or i.get("local_id") or ""): i
            for i in items
        }
        verdicts_to_persist: list[dict[str, Any]] = [
            cached_hmo_item_verdict_event(item, cached_payload)
            for item, cached_payload in pre_cached
        ]
        for v in fresh_verdicts:
            cand = v.get("candidate") if isinstance(v.get("candidate"), dict) else None
            local_id = verdict_candidate_local_id(v)
            if isinstance(cand, dict):
                item = items_by_id.get(local_id)
                if item is not None and not cand.get("label"):
                    from app.pipeline.hmo_item_views import item_label  # noqa: PLC0415

                    cand["label"] = item_label(item)
            if local_id not in streamed_fresh_verdict_keys:
                ev = AgentEvent(type="agent.verdict", payload=v)
                persist_session_event(session_dir, ev)
                yield ev
            verdicts_to_persist.append(v)

        if verdicts_to_persist:
            try:
                await _persist_hmo_item_verdicts(
                    run_id=UUID(run_id),
                    items_by_id=items_by_id,
                    verdicts=verdicts_to_persist,
                    judge_model=tier_model or "gemini-3.5-flash",
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to persist HMO item verdicts")

        outcome = resolve_verify_session_outcome(
            eval_agent_unavailable=bool(eval_agent_error),
            uncached_count=len(uncached_items),
            fresh_verdict_count=len(fresh_verdicts),
            scope_size=len(items),
            cache_hits=len(pre_cached),
            runner_error=runner_error,
            runner_exit_code=runner_exit_code,
        )
        end_ev = AgentEvent(
            type="session.end",
            payload={
                "session_id": session_id,
                "scope_size": len(items),
                "cache_hits": len(pre_cached),
                "fresh_verdicts": len(fresh_verdicts),
                "uncached_skipped": len(uncached_items) if eval_agent_error else 0,
                "outcome": outcome,
                "runner_error": runner_error,
            },
        )
        persist_session_event(session_dir, end_ev)
        yield end_ev
