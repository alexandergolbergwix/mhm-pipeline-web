"""AI verification stream for HMO Wikibase schema bootstrap entries."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.pipeline import agent_actions, hmo_schema_actions
from app.pipeline.agent_runner import (
    AgentEvent,
    locate_eval_agent,
    persist_session_event,
    read_run_verdicts,
    resolve_verify_session_dir,
    resolve_verify_state_dir,
    spawn_eval_agent_run,
)
from app.pipeline.hmo_schema_bootstrap import (
    SchemaBootstrapResult,
    serialise_bootstrap_result,
)

logger = logging.getLogger(__name__)

_HMO_SCHEMA_VERIFY_CHANNEL = "hmo-schema-verify-sessions"
HMO_SCHEMA_VERIFY_CHANNEL = _HMO_SCHEMA_VERIFY_CHANNEL
_JUDGEABLE_STATUSES = frozenset({"created", "would_create", "skipped", "failed"})


def schema_entry_local_id(entry: dict[str, Any]) -> str:
    uri = str(entry.get("ontology_uri") or "")
    kind = str(entry.get("entity_kind") or "entity")
    return f"{kind}::{uri}" if uri else f"{kind}::{entry.get('label', '')}"


def filter_schema_entries(
    report: SchemaBootstrapResult,
    *,
    ontology_uris: list[str] | None,
    statuses: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    wanted = set(ontology_uris or [])
    allowed = statuses or _JUDGEABLE_STATUSES
    items: list[dict[str, Any]] = []
    for entry in report.entries:
        if entry.status not in allowed:
            continue
        if wanted and entry.ontology_uri not in wanted:
            continue
        row = {
            "ontology_uri": entry.ontology_uri,
            "entity_kind": entry.entity_kind,
            "label": entry.label,
            "datatype": None,
            "wikibase_id": entry.wikibase_id,
            "status": entry.status,
            "message": entry.message,
            "_local_id": schema_entry_local_id({
                "ontology_uri": entry.ontology_uri,
                "entity_kind": entry.entity_kind,
                "label": entry.label,
            }),
        }
        items.append(row)
    return items


def write_schema_verify_fixture(*, dest_dir: Path, report: SchemaBootstrapResult) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "hmo_wikibase_schema.json").write_text(
        json.dumps(serialise_bootstrap_result(report), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    marc_path = dest_dir / "marc_extracted.json"
    if not marc_path.exists():
        marc_path.write_text("[]\n", encoding="utf-8")


def schema_verdict_query_summary(
    entry: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "hmo_wikibase_schema",
) -> dict[str, Any]:
    return {
        "ontology_uri": str(entry.get("ontology_uri") or ""),
        "entity_kind": str(entry.get("entity_kind") or ""),
        "label": str(entry.get("label") or ""),
        "wikibase_id": entry.get("wikibase_id"),
        "status": str(entry.get("status") or ""),
        "judge_model": judge_model,
        "evaluator": evaluator,
    }


def cached_schema_verdict_event(
    entry: dict[str, Any],
    cached_payload: dict[str, Any],
) -> dict[str, Any]:
    local_id = str(entry.get("_local_id") or schema_entry_local_id(entry))
    return {
        "candidate": {**entry, "_local_id": local_id, "label": entry.get("label")},
        "verdict": cached_payload.get("verdict") or {},
        "judge_id": cached_payload.get("judge_id"),
        "judged_at": cached_payload.get("judged_at"),
        "cache_key": cached_payload.get("cache_key"),
    }


async def hmo_schema_verify_event_stream(
    *,
    run_id: str,
    session_id: str,
    action: agent_actions.AgentAction,
    items: list[dict[str, Any]],
    uncached_items: list[dict[str, Any]],
    pre_cached: list[tuple[dict[str, Any], dict[str, Any]]],
    report: SchemaBootstrapResult,
    api_key: str,
    override_cache: bool,
    tier_model: str | None,
) -> AsyncIterator[AgentEvent]:
    state_dir = resolve_verify_state_dir(_HMO_SCHEMA_VERIFY_CHANNEL, run_id)
    session_dir = resolve_verify_session_dir(_HMO_SCHEMA_VERIFY_CHANNEL, run_id, session_id)
    pipeline_output = session_dir / "pipeline-output"
    session_dir.mkdir(parents=True, exist_ok=True)
    eval_agent_error: str | None = None

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
            "scope_entry_ids": sorted(str(i.get("_local_id") or "") for i in items),
            "goal": agent_actions.render_goal(action, n_candidates=len(items)),
            "cache_hits": len(pre_cached),
        },
    )
    persist_session_event(session_dir, start_ev)
    yield start_ev

    for entry, cached_payload in pre_cached:
        ev = AgentEvent(
            type="agent.verdict",
            payload=cached_schema_verdict_event(entry, cached_payload),
        )
        persist_session_event(session_dir, ev)
        yield ev

    try:
        if eval_agent_error:
            warn_ev = AgentEvent(
                type="runner.warning",
                payload={
                    "message": (
                        f"{len(uncached_items)} schema entries cannot be verified here "
                        "because the eval-agent is not available on this server."
                    ),
                    "uncached_count": len(uncached_items),
                    "eval_agent_error": eval_agent_error,
                },
            )
            persist_session_event(session_dir, warn_ev)
            yield warn_ev
        else:
            write_schema_verify_fixture(dest_dir=pipeline_output, report=report)
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
    finally:
        on_disk_verdicts = (
            read_run_verdicts(state_dir) if (uncached_items and not eval_agent_error) else []
        )
        for v in on_disk_verdicts:
            ev = AgentEvent(type="agent.verdict", payload=v)
            persist_session_event(session_dir, ev)
            yield ev

        end_ev = AgentEvent(
            type="session.end",
            payload={
                "session_id": session_id,
                "scope_size": len(items),
                "cache_hits": len(pre_cached),
                "fresh_verdicts": len(on_disk_verdicts),
                "uncached_skipped": len(uncached_items) if eval_agent_error else 0,
                "outcome": "partial" if eval_agent_error else "complete",
            },
        )
        persist_session_event(session_dir, end_ev)
        yield end_ev
