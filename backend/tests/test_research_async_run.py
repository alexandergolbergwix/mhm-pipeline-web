"""Async AG-UI runs (Rule R28): webhook relay + Redis-Stream SSE bridge."""
from __future__ import annotations

import uuid

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

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


@pytest.mark.asyncio
async def test_stream_without_terminal_event_reports_a_distinct_code():
    """P0/P1 (W-274): silence surfaces as `stream_ended` with a retryable
    code — never a fabricated look-alike of a run failure."""
    from app.services.research_agent import async_run as ar

    original_idle = ar._MAX_IDLE_S
    ar._MAX_IDLE_S = 0.4
    try:
        await ar.register_run("thread-x", "run-x")
        events = [e async for e in ar.iter_stream("thread-x", "run-x", last_id="$")]
        ticks = [e for e in events if e is None]
        errors = [e for e in events if isinstance(e, dict) and e.get("type") == "RUN_ERROR"]
        assert ticks, "the bridge must heartbeat while waiting"
        assert errors and errors[0].get("code") in {"stream_ended", "deadline_exceeded"}
        assert errors[0].get("retryable") is True
        assert "stopped responding" not in str(errors[0].get("message"))
    finally:
        ar._MAX_IDLE_S = original_idle


class _FlakyRedis:
    """Fake shared client: the first `fail_times` SETs raise ConnectionError."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.set_calls = 0

    async def set(self, *args, **kwargs):
        self.set_calls += 1
        if self.set_calls <= self.fail_times:
            raise RedisConnectionError("Connection lost")
        return True


async def test_register_run_retries_once_on_connection_loss(monkeypatch):
    from app.services.research_agent import async_run as ar

    fake = _FlakyRedis(fail_times=1)

    async def _fake_get_redis():
        return fake

    monkeypatch.setattr(ar, "get_redis", _fake_get_redis)
    await ar.register_run("thread-x", "run-retry")
    assert fake.set_calls == 2


async def test_register_run_raises_after_the_retry_fails(monkeypatch):
    from app.services.research_agent import async_run as ar

    fake = _FlakyRedis(fail_times=99)

    async def _fake_get_redis():
        return fake

    monkeypatch.setattr(ar, "get_redis", _fake_get_redis)
    with pytest.raises(RedisConnectionError):
        await ar.register_run("thread-x", "run-dead")
    assert fake.set_calls == 2


async def test_async_start_maps_relay_outage_to_503(sample_run, monkeypatch):
    """A dead relay connection is a retryable 503, never a bare 500."""
    from app.services.research_agent import async_run as ar
    from app.settings import get_settings

    monkeypatch.setattr(
        get_settings(), "research_agent_modal_url", "https://modal.invalid",
        raising=False,
    )

    async def _dead(*args, **kwargs):
        raise RedisConnectionError("Connection lost")

    monkeypatch.setattr(ar, "register_run", _dead)
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
    assert "temporarily unavailable" in resp.json()["detail"]


async def test_webhook_maps_relay_outage_to_503(sample_run, monkeypatch):
    """The webhook 503 lets Modal's retry resend the batch."""
    from app.services.research_agent import async_run as ar

    session = await _mint(sample_run)
    run_id = "async-relay-outage"
    await ar.register_run(uuid.UUID(session["thread_id"]), run_id)

    async def _dead(*args, **kwargs):
        raise RedisConnectionError("Connection lost")

    monkeypatch.setattr(ar, "append_events", _dead)
    resp = await sample_run["client"].post(
        "/api/research-agent/agui-events",
        headers={"Authorization": f"Bearer {session['tool_grant']}"},
        json={"run_id": run_id, "events": [{"type": "RUN_FINISHED"}]},
    )
    assert resp.status_code == 503
