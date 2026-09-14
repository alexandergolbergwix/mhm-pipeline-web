"""Async AG-UI runs (Rule R28): webhook relay + Redis-Stream SSE bridge."""
from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


async def _mint(sample_run) -> dict:
    session = await sample_run["client"].post(
        "/api/research-agent/sessions",
        json={"run_id": str(sample_run["run_id"])},
    )
    assert session.status_code == 200, session.text
    return session.json()


async def test_agui_events_requires_grant(sample_run):
    resp = await sample_run["client"].post(
        "/api/research-agent/agui-events",
        json={"run_id": "r", "events": []},
    )
    assert resp.status_code == 401


async def test_webhook_rejects_unknown_run(sample_run):
    session = await _mint(sample_run)
    resp = await sample_run["client"].post(
        "/api/research-agent/agui-events",
        headers={"Authorization": f"Bearer {session['tool_grant']}"},
        json={"run_id": "no-such-run", "events": [{"type": "RUN_STARTED"}]},
    )
    assert resp.status_code == 404


async def test_webhook_relay_and_sse_stream(sample_run):
    session = await _mint(sample_run)
    headers = {"Authorization": f"Bearer {session['tool_grant']}"}
    run_id = "async-test-run-1"

    from app.services.research_agent import async_run

    await async_run.register_run(uuid.UUID(session["thread_id"]), run_id)

    resp = await sample_run["client"].post(
        "/api/research-agent/agui-events",
        headers=headers,
        json={
            "run_id": run_id,
            "events": [
                {"type": "RUN_STARTED"},
                {"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"},
                {"type": "RUN_FINISHED"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["stored"] == 3

    stream = await sample_run["client"].get(
        f"/api/research-agent/agui-stream?run_id={run_id}"
    )
    assert stream.status_code == 200, stream.text
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert '"type": "RUN_STARTED"' in stream.text or '"type":"RUN_STARTED"' in stream.text
    assert "RUN_FINISHED" in stream.text
    # The bridge closes after the end marker (no heartbeat spam after done).
    assert stream.text.rstrip().endswith("\n\n") or stream.text.endswith("\n\n")


async def test_stream_unknown_run_is_404(sample_run, db_session):
    resp = await sample_run["client"].get(
        "/api/research-agent/agui-stream?run_id=nope"
    )
    assert resp.status_code == 404


async def test_async_start_needs_modal(sample_run):
    """Without RESEARCH_AGENT_MODAL_URL the async start fails closed."""
    session = await _mint(sample_run)
    resp = await sample_run["client"].post(
        "/api/research-agent/agui-async",
        json={
            "threadId": session["thread_id"],
            "messages": [{"id": "m1", "role": "user", "content": "hi"}],
            "forwardedProps": {"tool_grant": session["tool_grant"]},
        },
    )
    assert resp.status_code == 503
