"""Unit tests for ``sse_stream`` error propagation.

Pins the contract that a generator exception must produce a ``runner.error``
SSE event rather than a silent stream-end.

Background: before the fix, ``sse_stream``'s internal ``producer()`` coroutine
swallowed all exceptions from the event generator. The client received a clean
``done: true`` with zero events, the ``for await`` loop exited, and the
"Start verification" button reverted without showing any error. These tests
ensure the regression cannot reappear silently.
"""

from __future__ import annotations

import json

import pytest

from app.pipeline.agent_runner import AgentEvent, sse_stream


# ── SSE frame parser (mirrors the frontend parseFrame logic) ──────────────


def _parse_sse_frames(text: str) -> list[dict]:
    """Parse SSE text into a list of dicts.

    Each ``event:`` + ``data:`` pair becomes ``{"type": ..., **data_payload}``.
    """
    events = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        ev_type = "message"
        data = ""
        for line in frame.split("\n"):
            if line.startswith(": "):
                continue
            colon = line.find(":")
            if colon < 0:
                continue
            field = line[:colon]
            value = line[colon + 1:].lstrip(" ")
            if field == "event":
                ev_type = value
            elif field == "data":
                data = value
        if not data:
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            payload = {"raw": data}
        events.append({"type": ev_type, **payload})
    return events


# ── Tests ─────────────────────────────────────────────────────────────────


class TestSseStreamErrorPropagation:
    @pytest.mark.asyncio
    async def test_generator_exception_after_first_yield_emits_runner_error(
        self,
    ) -> None:
        """Regression: exception AFTER the first yield must not be swallowed."""
        async def raises_mid_stream():
            yield AgentEvent(type="session.start", payload={"ok": True})
            raise RuntimeError("subprocess exploded mid-stream")

        chunks = []
        async for chunk in sse_stream(raises_mid_stream()):
            chunks.append(chunk)

        events = _parse_sse_frames("".join(chunks))
        by_type = {e["type"]: e for e in events}

        assert "session.start" in by_type, "first event must be forwarded"
        assert "runner.error" in by_type, "exception must produce runner.error"
        assert "exploded mid-stream" in (by_type["runner.error"].get("message") or "")

    @pytest.mark.asyncio
    async def test_generator_exception_before_any_yield_emits_runner_error(
        self,
    ) -> None:
        """Regression: exception BEFORE any yield must still produce runner.error.

        This is the exact scenario that caused the silent 'button reverts'
        bug: locate_eval_agent() raised inside the generator before
        session.start was yielded, the exception was swallowed, the client
        received zero events, and the button silently reverted.
        """
        async def raises_before_yield():
            raise FileNotFoundError("eval-agent not found (test)")
            yield  # makes this an async generator

        chunks = []
        async for chunk in sse_stream(raises_before_yield()):
            chunks.append(chunk)

        events = _parse_sse_frames("".join(chunks))

        assert len(events) == 1, "exactly one runner.error event expected"
        assert events[0]["type"] == "runner.error"
        assert "eval-agent" in (events[0].get("message") or "")

    @pytest.mark.asyncio
    async def test_clean_generator_produces_no_runner_error(self) -> None:
        """A generator that completes normally must not produce runner.error."""
        async def clean_gen():
            yield AgentEvent(type="session.start", payload={})
            yield AgentEvent(type="session.end",   payload={"outcome": "complete"})

        chunks = []
        async for chunk in sse_stream(clean_gen()):
            chunks.append(chunk)

        events = _parse_sse_frames("".join(chunks))
        assert not any(e["type"] == "runner.error" for e in events)
        assert {e["type"] for e in events} == {"session.start", "session.end"}
