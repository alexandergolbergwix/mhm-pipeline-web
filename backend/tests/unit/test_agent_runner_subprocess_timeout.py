"""Regression test for the eval-agent subprocess idle-timeout.

2026-07-04 outage: the AI-verify SSE endpoints held a Postgres connection
open (via the request-scoped session) for as long as the eval-agent
subprocess ran. When the subprocess produced no output at all — e.g. a
Gemini HTTP call stalled with no client-side timeout — the stream, and
the connection behind it, hung forever. ``_read_subprocess_stream`` must
raise ``TimeoutError`` once the subprocess goes fully silent for
``_SUBPROCESS_IDLE_TIMEOUT_S`` so ``spawn_eval_agent_run`` can kill it and
emit a clean ``runner.error`` instead of blocking indefinitely.
"""

from __future__ import annotations

import asyncio

import pytest

from app.pipeline import agent_runner


@pytest.mark.asyncio
async def test_read_subprocess_stream_times_out_on_total_silence(monkeypatch):
    monkeypatch.setattr(agent_runner, "_SUBPROCESS_IDLE_TIMEOUT_S", 0.05)

    # A StreamReader that never receives data and is never fed EOF —
    # readline() on it hangs forever, exactly like a stalled subprocess.
    stdout = asyncio.StreamReader()

    with pytest.raises(TimeoutError):
        async for _ in agent_runner._read_subprocess_stream(stdout):
            pass


@pytest.mark.asyncio
async def test_read_subprocess_stream_yields_events_before_silence(monkeypatch):
    monkeypatch.setattr(agent_runner, "_SUBPROCESS_IDLE_TIMEOUT_S", 0.2)

    stdout = asyncio.StreamReader()
    stdout.feed_data(b"[STEP] warming up\n")

    events = []
    with pytest.raises(TimeoutError):
        async for ev in agent_runner._read_subprocess_stream(stdout):
            events.append(ev)

    assert len(events) == 1
    assert events[0].type == "runner.step"
    assert events[0].payload == {"message": "warming up"}


@pytest.mark.asyncio
async def test_read_subprocess_stream_parses_trace_agent_verdict():
    stdout = asyncio.StreamReader()
    line = (
        '[TRACE] {"type":"agent.verdict","candidate":{"_local_id":"ms::1"},'
        '"verdict":{"overall":"pass"}}\n'
    )
    stdout.feed_data(line.encode())
    stdout.feed_eof()

    events = [ev async for ev in agent_runner._read_subprocess_stream(stdout)]

    assert len(events) == 1
    assert events[0].type == "agent.verdict"
    assert events[0].payload["candidate"]["_local_id"] == "ms::1"
