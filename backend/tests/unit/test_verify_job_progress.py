"""Verify job progress counters + terminal slim snapshot (Rule W-128)."""

from __future__ import annotations

import uuid

from app.pipeline.agent_runner import AgentEvent
from app.pipeline.verify_job import _progress_with_snapshot, _verdict_identity


def test_mid_run_progress_is_counters_only() -> None:
    run_id = uuid.uuid4()
    session_id = "sess-live"
    events = [
        {"type": "session.start", "scope_size": 2},
        {
            "type": "agent.verdict",
            "local_id": "QDraft_A",
            "overall": "pass",
            "reasoning": "ok",
        },
    ]
    progress = _progress_with_snapshot(
        AgentEvent(type="agent.verdict", payload=events[-1]),
        total=2,
        judged=1,
        session_id=session_id,
        run_id=run_id,
        collected_events=events,
    )
    assert "session_snapshot" not in progress
    assert progress["processed"] == 1
    assert progress["session_id"] == session_id


def test_terminal_progress_includes_slim_verdicts(monkeypatch) -> None:
    run_id = uuid.uuid4()
    session_id = "sess-end"
    events = [
        {"type": "session.start", "scope_size": 1},
    ]

    def _fake_read(channel: str, rid: str, sid: str) -> dict:
        return {
            "verdicts": [
                {
                    "candidate": {"_local_id": "QDraft_A"},
                    "verdict": {"overall": "pass", "reasoning": "ok"},
                },
            ],
        }

    monkeypatch.setattr(
        "app.pipeline.agent_runner.read_verify_session",
        _fake_read,
    )
    progress = _progress_with_snapshot(
        AgentEvent(type="session.end", payload={"outcome": "partial"}),
        total=1,
        judged=1,
        session_id=session_id,
        run_id=run_id,
        collected_events=events,
        kind="wikidata_verify",
    )
    snap = progress.get("session_snapshot")
    assert isinstance(snap, dict)
    assert snap["events"] == []
    assert len(snap.get("verdicts") or []) == 1


def test_progress_without_events_omits_snapshot() -> None:
    progress = _progress_with_snapshot(
        AgentEvent(type="session.start", payload={"scope_size": 1}),
        total=1,
        judged=0,
        session_id="s0",
        run_id=uuid.uuid4(),
        collected_events=[],
    )
    assert "session_snapshot" not in progress


def test_replayed_verdict_has_the_same_progress_identity() -> None:
    first = AgentEvent(
        type="agent.verdict",
        payload={"candidate": {"_local_id": "person::shared"}},
    )
    replay = AgentEvent(
        type="agent.verdict",
        payload={"candidate": {"_local_id": "person::shared"}},
    )
    identities = {
        candidate_id
        for event in (first, replay)
        if (candidate_id := _verdict_identity(event)) is not None
    }
    assert identities == {"person::shared"}
