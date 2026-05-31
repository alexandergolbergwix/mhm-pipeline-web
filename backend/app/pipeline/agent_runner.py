"""AI-agent verification runner + SSE event source.

Spawns ``eval-agent run`` (the existing candidate-level agentic judge,
NOT the planner / orchestrator) over a filtered fixture and streams
``[STEP]``/``[STATS]``/``[TRACE]`` lines back to the SSE consumer as
structured events.

Filtering: an AI verification "session" usually targets a subset of a
run's matches (one row in MatchDetailDialog, or 12 ticked rows, or all
filtered rows). The eval-agent CLI reads its inputs from a directory
holding ``marc_extracted.json`` + ``authority_enriched.json`` — both
filtered by control_number for the session. We write that pair into a
tmp dir per session and hand its path to ``eval-agent run``. Zero
eval-agent changes needed.

The runner emits these event types (the ai_verify router unions them
into a single SSE stream, the modal renders the live diagram + verdicts
+ step log):

* ``session.start`` — synthetic, carries action_id, scope, session_id
* ``runner.step``   — wraps a ``[STEP]`` line from eval-agent
* ``agent.stats``   — wraps a ``[STATS]`` line from eval-agent
* ``agent.verdict`` — synthesised from per-candidate results jsonl
* ``session.end``   — synthetic, carries summary stats + return code
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

logger = logging.getLogger(__name__)


_DEV_DEFAULT = Path("/Users/alexandergo/Documents/Doctorat/eval-agent")


@dataclass(frozen=True)
class AgentEvent:
    """One event parsed from the eval-agent subprocess.

    Mirrors the trace-event shape so the SSE consumer + the on-disk
    audit log carry the same vocabulary.
    """

    type: str
    payload: dict


# ── eval-agent location + interpreter ──────────────────────────────────


def locate_eval_agent() -> Path:
    """Return the sibling eval-agent repo root."""
    env = os.environ.get("EVAL_AGENT_ROOT")
    if env:
        p = Path(env).resolve()
        if (p / "eval_agent" / "cli.py").exists():
            return p
        raise FileNotFoundError(
            f"EVAL_AGENT_ROOT={env!r} but {p}/eval_agent/cli.py not found",
        )
    sibling = (Path(__file__).resolve().parents[3] / "eval-agent").resolve()
    if (sibling / "eval_agent" / "cli.py").exists():
        return sibling
    if (_DEV_DEFAULT / "eval_agent" / "cli.py").exists():
        return _DEV_DEFAULT
    raise FileNotFoundError(
        "eval-agent project not found. Set EVAL_AGENT_ROOT or place the "
        "sibling repo next to mhm-pipeline-web/.",
    )


def _python_for(eval_agent_root: Path) -> str:
    venv = eval_agent_root / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    import sys
    return sys.executable


# ── Filtered fixture builder ───────────────────────────────────────────


def build_filtered_fixture(
    *,
    dest_dir: Path,
    marc_records: Iterable[dict[str, Any]],
    authority_records: Iterable[dict[str, Any]],
) -> None:
    """Write a minimal ``marc_extracted.json`` + ``authority_enriched.json``
    pair into *dest_dir* so eval-agent can be pointed at it directly.

    The caller is responsible for filtering BEFORE calling — we just
    serialise whatever's passed. That keeps this module pure I/O and
    leaves the policy of "which control_numbers go in this session"
    in the router (which knows about DB rows).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "marc_extracted.json").write_text(
        json.dumps(list(marc_records), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (dest_dir / "authority_enriched.json").write_text(
        json.dumps(list(authority_records), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── The run loop ───────────────────────────────────────────────────────


async def spawn_eval_agent_run(
    *,
    pipeline_output: Path,
    evaluators: tuple[str, ...],
    api_key: str,
    state_dir: Path | None = None,
    eval_agent_root: Path | None = None,
    tier_model: str | None = None,
    override_cache: bool = False,
    rpm: int = 60,
) -> AsyncIterator[AgentEvent]:
    """Run ``eval-agent run`` and yield each parsed event.

    Order of events: one ``runner.step`` for the start banner, then
    interleaved ``runner.step`` / ``agent.stats`` lines for the entire
    judging loop, finally a synthetic ``runner.exit`` with the return
    code. The caller is responsible for any extra ``session.start`` /
    ``session.end`` framing.

    Cancellation: cancelling the consumer terminates the child process
    so we never keep paying Gemini for an orphaned conversation.

    Caching: eval-agent's verdict cache (``state/cache/
    verdict_cache.jsonl``) is used by default — repeated runs over the
    same candidates skip the Gemini call entirely. Pass
    ``override_cache=True`` to force fresh judgements; cached entries
    are still overwritten with the new verdict so the next run benefits.
    """
    root = eval_agent_root or locate_eval_agent()
    if not api_key:
        raise ValueError("Gemini API key required for verification.")

    py = _python_for(root)
    cmd: list[str] = [
        py, "-m", "eval_agent.cli", "run",
        "--pipeline-output", str(pipeline_output),
        "--evaluators", ",".join(evaluators) if evaluators else "all",
        "--rpm", str(rpm),
        "--no-self-verify",   # web sessions don't want the 5% re-judge tail
    ]
    if state_dir is not None:
        cmd += ["--state-dir", str(state_dir)]
    if tier_model:
        cmd += ["--tier-model", tier_model]
    if override_cache:
        # --no-cache skips cache READS but still writes fresh verdicts
        # so the next session warm-hits whichever ones overlap.
        cmd += ["--no-cache"]

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if api_key:
        env["GEMINI_API_KEY"] = api_key

    logger.info("eval-agent spawn cmd=%s cwd=%s", cmd[:6] + ["…"], root)
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(root), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None

    try:
        async for ev in _read_subprocess_stream(proc.stdout):
            yield ev
        rc = await proc.wait()
        yield AgentEvent(type="runner.exit", payload={"return_code": rc})
    except asyncio.CancelledError:
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
        raise


async def _read_subprocess_stream(
    stdout: asyncio.StreamReader,
) -> AsyncIterator[AgentEvent]:
    """Translate the eval-agent's three line conventions into events."""
    while True:
        line_bytes = await stdout.readline()
        if not line_bytes:
            return
        line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
        if not line:
            continue
        if line.startswith("[TRACE] "):
            # Already JSON — passthrough.
            try:
                payload = json.loads(line[len("[TRACE] "):])
            except json.JSONDecodeError:
                continue
            yield AgentEvent(
                type=payload.get("type", "trace"),
                payload=payload,
            )
        elif line.startswith("[STEP] "):
            yield AgentEvent(
                type="runner.step",
                payload={"message": line[len("[STEP] "):]},
            )
        elif line.startswith("[STATS] "):
            yield AgentEvent(
                type="agent.stats",
                payload=_parse_stats_line(line[len("[STATS] "):]),
            )
        # Anything else (incidental prints) is silently dropped so the
        # SSE stream stays clean for the UI.


def _parse_stats_line(body: str) -> dict[str, Any]:
    """Parse ``total=N hits=M judged=K in_tok=I out_tok=O``."""
    out: dict[str, int] = {}
    for kv in body.split():
        if "=" in kv:
            k, v = kv.split("=", 1)
            try:
                out[k] = int(v)
            except ValueError:
                pass
    return out


# ── Verdict synthesis (post-run) ───────────────────────────────────────


def read_run_verdicts(run_state_dir: Path) -> list[dict[str, Any]]:
    """Parse ``results.jsonl`` from a finished eval-agent run.

    eval-agent writes one verdict per candidate; this helper returns
    them as a flat list of dicts so the AI verification API can ship
    them straight to the UI's verdict table.
    """
    runs_root = run_state_dir / "runs"
    if not runs_root.exists():
        return []
    # Pick the newest run-dir under state/runs/<ts>/.
    latest = max(
        (p for p in runs_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        default=None,
    )
    if latest is None:
        return []
    results = latest / "results.jsonl"
    if not results.exists():
        return []
    out: list[dict[str, Any]] = []
    with results.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# ── Session sandbox ────────────────────────────────────────────────────


@asynccontextmanager
async def session_sandbox(
    *,
    run_id: str,
    session_id: str,
) -> AsyncIterator[tuple[Path, Path]]:
    """Yield ``(pipeline_output_dir, state_dir)`` for one session.

    Both live under the eval-agent's ``state/`` tree namespaced by
    ``run_id`` so listing sessions for one run never mixes with another.
    Caller is expected to write the filtered fixture into the first
    path before invoking ``spawn_eval_agent_run``.

    Cleanup is deliberately a no-op: we keep the session dir around so
    "Replay" works after the modal closes. Garbage collection of old
    sessions is a separate concern, handled by an out-of-band CLI.
    """
    root = locate_eval_agent()
    state = root / "state"
    base = state / "ai-verify-sessions" / run_id / session_id
    pipeline_output = base / "pipeline-output"
    yield pipeline_output, base


def new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


# ── SSE formatter ──────────────────────────────────────────────────────


async def sse_stream(
    events: AsyncIterator[AgentEvent],
) -> AsyncIterator[str]:
    """Format an async event stream as SSE bytes.

    Each event becomes ``event: <type>\\ndata: <json>\\n\\n``. A ``:
    keepalive`` comment line every 15 s prevents idle proxies from
    closing the connection during long Gemini calls.
    """
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

    ka_task = asyncio.create_task(keepalive())
    prod_task = asyncio.create_task(producer())
    try:
        while True:
            chunk = await queue.get()
            if chunk == "__DONE__":
                break
            yield chunk
    finally:
        ka_task.cancel()
        prod_task.cancel()
        for t in (ka_task, prod_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


# ── Session persistence (the audit log) ────────────────────────────────


def persist_session_event(session_dir: Path, ev: AgentEvent) -> None:
    """Append one event to the session's trace.jsonl (the audit log)."""
    session_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"ts": datetime.now(timezone.utc).isoformat(),
         "type": ev.type, **ev.payload},
        ensure_ascii=False,
    )
    with (session_dir / "trace.jsonl").open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def list_sessions(run_id: str) -> list[dict[str, Any]]:
    """Return all past AI verification sessions for *run_id*, newest first."""
    try:
        root = locate_eval_agent()
    except FileNotFoundError:
        return []
    base = root / "state" / "ai-verify-sessions" / run_id
    if not base.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        meta = _session_meta(child)
        out.append({"session_id": child.name, **meta})
    return out


def read_session(run_id: str, session_id: str) -> dict[str, Any] | None:
    try:
        root = locate_eval_agent()
    except FileNotFoundError:
        return None
    base = root / "state" / "ai-verify-sessions" / run_id / session_id
    if not base.exists():
        return None
    events: list[dict[str, Any]] = []
    trace = base / "trace.jsonl"
    if trace.exists():
        with trace.open(encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return {
        "session_id": session_id,
        "run_id":     run_id,
        "events":     events,
        "verdicts":   read_run_verdicts(base),
    }


def _session_meta(session_dir: Path) -> dict[str, Any]:
    trace = session_dir / "trace.jsonl"
    if not trace.exists():
        return {"started_at": None, "ended_at": None,
                "action_id": None, "scope_size": 0, "outcome": None}
    start: dict[str, Any] = {}
    end:   dict[str, Any] = {}
    try:
        for line in trace.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "session.start":
                start = ev
            elif ev.get("type") == "session.end":
                end = ev
    except OSError:
        pass
    return {
        "started_at": start.get("ts"),
        "ended_at":   end.get("ts"),
        "action_id":  start.get("action_id"),
        "scope_size": start.get("scope_size", 0),
        "outcome":    end.get("outcome"),
    }


# ── Cleanup helper for tests ───────────────────────────────────────────


def purge_session_dir(run_id: str, session_id: str) -> None:
    try:
        root = locate_eval_agent()
    except FileNotFoundError:
        return
    base = root / "state" / "ai-verify-sessions" / run_id / session_id
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)


__all__ = [
    "AgentEvent",
    "build_filtered_fixture",
    "list_sessions",
    "locate_eval_agent",
    "new_session_id",
    "persist_session_event",
    "purge_session_dir",
    "read_run_verdicts",
    "read_session",
    "session_sandbox",
    "spawn_eval_agent_run",
    "sse_stream",
]
