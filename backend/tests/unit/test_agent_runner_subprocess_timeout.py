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


@pytest.mark.asyncio
async def test_large_trace_preserves_unicode_and_following_events():
    import json
    payload = {'type': 'agent.verdict', 'reasoning': 'מקור' * 40000}
    wire = ('[TRACE] ' + json.dumps(payload, ensure_ascii=False) + '\n[STEP] next\n').encode()
    stdout = asyncio.StreamReader()
    async def feed():
        for offset in range(0, len(wire), 997):
            stdout.feed_data(wire[offset:offset + 997])
            await asyncio.sleep(0)
        stdout.feed_eof()
    writer = asyncio.create_task(feed())
    try:
        events = [event async for event in agent_runner._read_subprocess_stream(stdout)]
    finally:
        await writer
    assert events[0].payload == payload
    assert events[1].payload == {'message': 'next'}


@pytest.mark.asyncio
async def test_unterminated_final_event_is_preserved():
    stdout = asyncio.StreamReader()
    stdout.feed_data(b'[STEP] complete')
    stdout.feed_eof()
    events = [event async for event in agent_runner._read_subprocess_stream(stdout)]
    assert events[0].payload == {'message': 'complete'}


@pytest.mark.asyncio
async def test_event_memory_limit_fails_explicitly(monkeypatch):
    monkeypatch.setattr(agent_runner, '_MAX_AGENT_LINE_BYTES', 128)
    stdout = asyncio.StreamReader()
    stdout.feed_data(b'x' * 129)
    with pytest.raises(ValueError, match='protocol limit'):
        _ = [event async for event in agent_runner._read_subprocess_stream(stdout)]


@pytest.mark.asyncio
async def test_real_child_drains_large_stdout_and_stderr(tmp_path, monkeypatch):
    import sys
    package = tmp_path / 'eval_agent'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'cli.py').write_text(
        "import json, sys\n"
        "print('[TRACE] ' + json.dumps({'type': 'agent.verdict', 'text': 'x' * 300000}), flush=True)\n"
        "sys.stderr.write('e' * 300000 + '\\n'); sys.stderr.flush()\n"
        "print('[STEP] complete', flush=True)\n"
    )
    monkeypatch.setattr(agent_runner, '_python_for', lambda root: sys.executable)
    monkeypatch.setattr(agent_runner, '_SUBPROCESS_IDLE_TIMEOUT_S', 2)
    async def run():
        return [event async for event in agent_runner.spawn_eval_agent_run(
            pipeline_output=tmp_path, evaluators=('fixture',), eval_agent_root=tmp_path,
            tier_model='gemini-3.5-flash', api_key='fixture-unused-key')]
    events = await asyncio.wait_for(run(), timeout=5)
    assert next(event for event in events if event.type == 'agent.verdict').payload['text'] == 'x' * 300000
    assert events[-1].type == 'runner.exit'
    assert events[-1].payload['return_code'] == 0
