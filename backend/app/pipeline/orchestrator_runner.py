"""Subprocess runner + SSE event source for the eval-agent orchestrator.

Per Rule 48, the eval-agent lives in a sibling project; the web app
shells out to it rather than importing it. The contract is one-way:
the web backend writes a goal to argv + the API key to the env, and
reads ``[TRACE] {...json...}`` lines from stdout.

This module owns:

* :func:`locate_eval_agent` — finds the sibling repo (env override →
  default path).
* :func:`spawn_orchestrator` — launches ``python -m eval_agent.cli
  orchestrate ...`` and yields parsed event dicts.
* :func:`sse_stream` — wraps the dict iterator as an SSE-formatted
  string iterator the route hands to ``StreamingResponse``.

The runner streams **lines** asynchronously off the child's stdout, so
the browser sees agent activity within milliseconds — same UX as the
desktop dialog's animated agent diagram.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Sequence

logger = logging.getLogger(__name__)


# Where the sibling eval-agent project lives. Honoured in this order:
#   1. EVAL_AGENT_ROOT env var (production / deploy).
#   2. ../eval-agent next to this repo (dev).
#   3. /Users/alexandergo/Documents/Doctorat/eval-agent (the canonical
#      dev path on the maintainer's machine).
_DEV_DEFAULT = Path("/Users/alexandergo/Documents/Doctorat/eval-agent")


@dataclass(frozen=True)
class OrchestratorEvent:
    """One ``[TRACE]`` event parsed off the child process stdout."""

    type: str
    payload: dict


def locate_eval_agent() -> Path:
    """Return the eval-agent repo root, or raise FileNotFoundError."""
    env = os.environ.get("EVAL_AGENT_ROOT")
    if env:
        p = Path(env).resolve()
        if (p / "eval_agent" / "cli.py").exists():
            return p
        raise FileNotFoundError(
            f"EVAL_AGENT_ROOT={env!r} but {p}/eval_agent/cli.py not found",
        )
    repo_sibling = (Path(__file__).resolve().parents[3] / "eval-agent").resolve()
    if (repo_sibling / "eval_agent" / "cli.py").exists():
        return repo_sibling
    if (_DEV_DEFAULT / "eval_agent" / "cli.py").exists():
        return _DEV_DEFAULT
    raise FileNotFoundError(
        "eval-agent project not found. Set EVAL_AGENT_ROOT or "
        "place the sibling repo next to mhm-pipeline-web/.",
    )


def _python_for(eval_agent_root: Path) -> str:
    """Pick a Python binary capable of importing eval_agent.

    The sibling project ships its own .venv (Python 3.12 — see
    ``eval-agent/.venv/bin/python``). Prefer it; fall back to the
    parent process interpreter only when missing.
    """
    venv = eval_agent_root / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    import sys
    return sys.executable


# ── Spawn + stream ─────────────────────────────────────────────────────


async def spawn_orchestrator(
    *,
    goal: str,
    mode: str = "plan_only",
    judge_model: str = "gemini-3.5-flash",
    api_key: str | None = None,
    max_steps: int = 12,
    max_seconds: int = 180,
    max_usd: float = 0.10,
    use_stub_judge: bool = False,
    eval_agent_root: Path | None = None,
) -> AsyncIterator[OrchestratorEvent]:
    """Run the orchestrator and yield each [TRACE] event.

    Yields a final synthetic ``"runner.exit"`` event carrying the exit
    code so the frontend knows the stream is over even if no
    ``session.end`` ever arrived (e.g. the subprocess crashed).

    Raises FileNotFoundError when the sibling project can't be found,
    or ValueError when ``api_key`` is missing and ``use_stub_judge``
    is False.
    """
    root = eval_agent_root or locate_eval_agent()
    if not use_stub_judge and not api_key:
        raise ValueError("Gemini API key required unless use_stub_judge is True")

    py = _python_for(root)
    cmd: list[str] = [
        py, "-m", "eval_agent.cli", "orchestrate",
        "--goal", goal,
        "--judge", judge_model,
        "--max-steps", str(max_steps),
        "--max-seconds", str(max_seconds),
        "--max-usd", str(max_usd),
    ]
    if mode == "plan_only":
        cmd.append("--plan-only")
    elif mode == "supervised":
        cmd.append("--supervised")
    elif mode == "autonomous":
        cmd.append("--autonomous")
    if use_stub_judge:
        cmd.append("--no-llm")

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if api_key:
        # Pass on the env, NEVER on argv (Rule 50 — keys leak in ps).
        env["GEMINI_API_KEY"] = api_key

    logger.info("orchestrator spawn cmd=%s cwd=%s", cmd[:6] + ["…"], root)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None

    try:
        async for ev in _read_trace_stream(proc.stdout):
            yield ev
        # Drain anything still pending after stdout EOF (the child may
        # have emitted [TRACE] lines after its [STEP] done marker).
        rc = await proc.wait()
        yield OrchestratorEvent(
            type="runner.exit",
            payload={"return_code": rc},
        )
    except asyncio.CancelledError:
        # The client disconnected mid-stream. Be polite to Gemini's
        # rate limiter: terminate the child rather than letting it
        # run to completion against a now-orphaned stream.
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
        raise


async def _read_trace_stream(
    stdout: asyncio.StreamReader,
) -> AsyncIterator[OrchestratorEvent]:
    while True:
        line_bytes = await stdout.readline()
        if not line_bytes:
            return
        line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
        if not line:
            continue
        if line.startswith("[TRACE] "):
            body = line[len("[TRACE] "):]
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                logger.warning("malformed [TRACE] line: %s (%s)", body[:200], exc)
                continue
            yield OrchestratorEvent(type=payload.get("type", "unknown"),
                                     payload=payload)
        elif line.startswith("[STEP] "):
            # [STEP] is a human-readable banner from the CLI wrapper;
            # surface it as a synthetic event so the UI can show
            # "starting…" / "done" pills even though the structured
            # event stream covers the real activity in trace.jsonl.
            yield OrchestratorEvent(
                type="runner.step",
                payload={"type": "runner.step",
                          "message": line[len("[STEP] "):]},
            )


# ── SSE formatter ──────────────────────────────────────────────────────


async def sse_stream(
    events: AsyncIterator[OrchestratorEvent],
) -> AsyncIterator[str]:
    """Format an async iterator of events as an SSE byte stream.

    SSE framing: each event is ``event: <type>\\ndata: <json>\\n\\n``.
    A ``: keepalive`` comment line every 15s prevents proxies from
    closing an idle connection during long Gemini calls.
    """
    keepalive_task: asyncio.Task[None] | None = None
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def keepalive() -> None:
        while True:
            await asyncio.sleep(15)
            await queue.put(": keepalive\n\n")

    async def producer() -> None:
        try:
            async for ev in events:
                payload = json.dumps(ev.payload, ensure_ascii=False)
                await queue.put(f"event: {ev.type}\ndata: {payload}\n\n")
        finally:
            await queue.put("__DONE__")

    keepalive_task   = asyncio.create_task(keepalive())
    producer_task    = asyncio.create_task(producer())
    try:
        while True:
            chunk = await queue.get()
            if chunk == "__DONE__":
                break
            yield chunk
    finally:
        keepalive_task.cancel()
        producer_task.cancel()
        try:
            await keepalive_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await producer_task
        except (asyncio.CancelledError, Exception):
            pass


__all__ = [
    "OrchestratorEvent",
    "locate_eval_agent",
    "sse_stream",
    "spawn_orchestrator",
]
