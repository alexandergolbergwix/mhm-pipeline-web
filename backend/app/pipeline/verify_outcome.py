"""Honest verify-session completion outcomes (Rule W-126)."""

from __future__ import annotations

from typing import Any


def verdict_candidate_local_id(verdict: dict[str, Any]) -> str:
    cand = verdict.get("candidate") if isinstance(verdict.get("candidate"), dict) else None
    if not isinstance(cand, dict):
        return ""
    return str(
        cand.get("_local_id")
        or cand.get("_item_id")
        or cand.get("local_id")
        or "",
    ).strip()


def merge_fresh_verdicts(
    *,
    streamed: list[dict[str, Any]],
    on_disk: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union TRACE + checkpoint verdicts by local_id (on-disk wins)."""
    by_id: dict[str, dict[str, Any]] = {}
    orphans: list[dict[str, Any]] = []
    for verdict in streamed:
        local_id = verdict_candidate_local_id(verdict)
        if local_id:
            by_id[local_id] = verdict
        else:
            orphans.append(verdict)
    for verdict in on_disk:
        local_id = verdict_candidate_local_id(verdict)
        if local_id:
            by_id[local_id] = verdict
        else:
            orphans.append(verdict)
    return list(by_id.values()) + orphans


def resolve_verify_session_outcome(
    *,
    eval_agent_unavailable: bool = False,
    uncached_count: int = 0,
    fresh_verdict_count: int = 0,
    scope_size: int = 0,
    cache_hits: int = 0,
    runner_error: str | None = None,
    runner_exit_code: int | None = None,
) -> str:
    """Return ``complete`` only when the full scope was judged.

    A missing checkpoint, non-zero subprocess exit, idle-kill, or
    judged < scope MUST NOT report ``complete`` — the curator UI treats
    that as a successful full pass.
    """
    if eval_agent_unavailable and uncached_count > 0:
        return "partial"
    if runner_error:
        return "partial"
    if runner_exit_code not in (None, 0):
        return "partial"
    judged = int(cache_hits) + int(fresh_verdict_count)
    if int(scope_size) > 0 and judged < int(scope_size):
        return "partial"
    return "complete"
