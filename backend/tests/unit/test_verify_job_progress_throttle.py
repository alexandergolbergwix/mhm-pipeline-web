"""Progress snapshot throttling for verify jobs (Rule W-127)."""

from __future__ import annotations

import uuid

from app.pipeline.agent_runner import AgentEvent
from app.pipeline.verify_job import _progress_with_snapshot


def test_progress_skips_snapshot_between_throttles(monkeypatch) -> None:
    monkeypatch.setattr("app.pipeline.verify_job._PROGRESS_SNAPSHOT_INTERVAL_S", 60.0)
    last = [1000.0]
    times = iter([1000.1, 1000.2])
    monkeypatch.setattr("time.monotonic", lambda: next(times))

    events = [
        {"type": "session.start", "scope_size": 3},
        {
            "type": "agent.verdict",
            "candidate": {"_local_id": "a"},
            "verdict": {"overall": "pass"},
        },
    ]
    progress = _progress_with_snapshot(
        AgentEvent(type="agent.verdict", payload={"candidate": {"_local_id": "a"}}),
        total=3,
        judged=1,
        session_id="s1",
        run_id=uuid.uuid4(),
        collected_events=events,
        last_snapshot_at=last,
    )
    assert "session_snapshot" not in progress


def test_progress_forces_snapshot_on_session_end(monkeypatch) -> None:
    monkeypatch.setattr("app.pipeline.verify_job._PROGRESS_SNAPSHOT_INTERVAL_S", 60.0)
    last = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: 0.1)

    events = [
        {"type": "session.start", "scope_size": 1},
        {
            "type": "agent.verdict",
            "candidate": {"_local_id": "a"},
            "verdict": {"overall": "pass"},
        },
        {"type": "session.end", "outcome": "partial"},
    ]
    progress = _progress_with_snapshot(
        AgentEvent(type="session.end", payload={"outcome": "partial"}),
        total=1,
        judged=1,
        session_id="s1",
        run_id=uuid.uuid4(),
        collected_events=events,
        last_snapshot_at=last,
    )
    assert "session_snapshot" in progress
    assert progress["session_snapshot"]["verdicts"]
