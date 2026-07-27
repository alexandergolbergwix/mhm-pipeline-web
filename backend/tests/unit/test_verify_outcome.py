"""Tests for honest verify-session outcomes (Rule W-126)."""

from __future__ import annotations

from app.pipeline.verify_outcome import (
    merge_fresh_verdicts,
    resolve_verify_session_outcome,
    verdict_candidate_local_id,
)


def test_verdict_candidate_local_id_prefers_local_id() -> None:
    assert verdict_candidate_local_id({
        "candidate": {"_item_id": "x", "_local_id": "manuscript::1"},
    }) == "manuscript::1"


def test_merge_fresh_verdicts_disk_wins() -> None:
    streamed = [{"candidate": {"_local_id": "a"}, "overall": "fail"}]
    on_disk = [{"candidate": {"_local_id": "a"}, "overall": "pass"}]
    merged = merge_fresh_verdicts(streamed=streamed, on_disk=on_disk)
    assert len(merged) == 1
    assert merged[0]["overall"] == "pass"


def test_merge_keeps_streamed_when_checkpoint_missing() -> None:
    streamed = [
        {"candidate": {"_local_id": "a"}, "overall": "partial"},
        {"candidate": {"_local_id": "b"}, "overall": "fail"},
    ]
    merged = merge_fresh_verdicts(streamed=streamed, on_disk=[])
    assert {verdict_candidate_local_id(v) for v in merged} == {"a", "b"}


def test_outcome_complete_when_full_scope_judged() -> None:
    assert resolve_verify_session_outcome(
        fresh_verdict_count=10,
        scope_size=12,
        cache_hits=2,
    ) == "complete"


def test_outcome_partial_when_judged_below_scope() -> None:
    assert resolve_verify_session_outcome(
        fresh_verdict_count=54,
        scope_size=313,
        cache_hits=0,
    ) == "partial"


def test_outcome_partial_on_runner_error() -> None:
    assert resolve_verify_session_outcome(
        fresh_verdict_count=54,
        scope_size=54,
        cache_hits=0,
        runner_error="hung",
    ) == "partial"


def test_outcome_partial_on_nonzero_exit() -> None:
    assert resolve_verify_session_outcome(
        fresh_verdict_count=10,
        scope_size=10,
        runner_exit_code=-9,
    ) == "partial"


def test_outcome_partial_when_eval_agent_missing() -> None:
    assert resolve_verify_session_outcome(
        eval_agent_unavailable=True,
        uncached_count=5,
        fresh_verdict_count=0,
        scope_size=5,
        cache_hits=0,
    ) == "partial"
