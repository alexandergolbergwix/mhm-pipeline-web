"""Progress / snapshot hygiene for verify jobs (Rules W-127 / W-128)."""

from __future__ import annotations

import uuid

from app.pipeline.agent_runner import AgentEvent
from app.pipeline.verify_job import _progress_with_snapshot, _verdict_identity
from app.pipeline.verify_session_store import slim_job_session_snapshot


def test_verdict_identity_accepts_ner_entity_id() -> None:
    """NER candidates carry ``_entity_id``; without it judged stays 0/total."""
    event = AgentEvent(
        type="agent.verdict",
        payload={"candidate": {"_entity_id": "e-1", "text": "x"}},
    )
    assert _verdict_identity(event) == "e-1"


def test_verdict_identity_prefers_local_id() -> None:
    event = AgentEvent(
        type="agent.verdict",
        payload={"candidate": {"_entity_id": "e-1", "_local_id": "ms::1"}},
    )
    assert _verdict_identity(event) == "ms::1"


def test_progress_skips_snapshot_mid_run(monkeypatch) -> None:
    last = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

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


def test_progress_forces_slim_snapshot_on_session_end(monkeypatch) -> None:
    last = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: 0.1)

    events = [
        {"type": "session.start", "scope_size": 1},
        {
            "type": "agent.verdict",
            "candidate": {
                "_local_id": "a",
                "label": "MS 1",
                "verify_evidence": {"marc": "x" * 5000},
            },
            "verdict": {"overall": "pass", "reasoning": "ok"},
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
    snap = progress["session_snapshot"]
    assert snap["events"] == []
    assert snap["verdicts"]
    assert "verify_evidence" not in (snap["verdicts"][0].get("candidate") or {})


def test_slim_job_session_snapshot_strips_evidence() -> None:
    snap = slim_job_session_snapshot({
        "session_id": "s",
        "run_id": "r",
        "events": [{"type": "runner.step", "message": "x" * 1000}],
        "verdicts": [{
            "candidate": {"_local_id": "a", "label": "L", "marc_context": {"big": True}},
            "verdict": {"overall": "partial", "reasoning": "y" * 2000},
        }],
    })
    assert snap["events"] == []
    assert snap["verdicts"][0]["candidate"]["_local_id"] == "a"
    assert "marc_context" not in snap["verdicts"][0]["candidate"]
    assert len(snap["verdicts"][0]["verdict"]["reasoning"]) <= 800
