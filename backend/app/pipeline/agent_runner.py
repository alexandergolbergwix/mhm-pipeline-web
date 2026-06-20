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
    threshold: float | None = None,
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
    if threshold is not None:
        # NER evaluators drop predictions below this confidence
        # (default 0.85). When a curator hand-picks entities they want
        # ALL of them judged regardless of confidence, so callers pass a
        # negative sentinel. NB eval-agent computes the threshold as
        # ``float(args.threshold or default)`` — 0.0 is falsy and would
        # silently fall back to the 0.85 default, so the "judge all"
        # value must be negative (truthy + below every real confidence).
        cmd += ["--threshold", str(threshold)]

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if api_key:
        env["GEMINI_API_KEY"] = api_key
    # Defense-in-depth for Rule 52: inject state_dir via env var AS WELL AS
    # --state-dir argv so older bundled eval-agent versions that only honour
    # the env var still write results to the right location.
    if state_dir is not None:
        env["EVAL_AGENT_STATE_DIR"] = str(state_dir)

    logger.info("eval-agent spawn cmd=%s cwd=%s", cmd[:6] + ["…"], root)
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(root), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None

    # Drain stderr concurrently. Without this a chatty subprocess can fill
    # the ~64 KB pipe buffer and deadlock, and — more importantly here — the
    # subprocess's failure reason (e.g. an import error that kills it before
    # any [STEP] line) was being silently discarded, surfacing to the UI as
    # an unexplained "0 verdicts". We keep the tail and emit it as a
    # runner.error on a non-zero exit.
    stderr_chunks: list[str] = []

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        while True:
            chunk = await proc.stderr.readline()
            if not chunk:
                return
            stderr_chunks.append(chunk.decode("utf-8", errors="replace"))

    stderr_task = asyncio.create_task(_drain_stderr())
    try:
        async for ev in _read_subprocess_stream(proc.stdout):
            yield ev
        rc = await proc.wait()
        await stderr_task
        if rc != 0:
            tail = "".join(stderr_chunks).strip()[-2000:]
            logger.error(
                "eval-agent exited rc=%s stderr_tail=%s", rc, tail or "<empty>",
            )
            yield AgentEvent(
                type="runner.error",
                payload={
                    "message": (
                        f"Verification subprocess failed (exit {rc}). "
                        + (tail or "No error output was captured.")
                    ),
                    "return_code": rc,
                },
            )
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
    finally:
        if not stderr_task.done():
            stderr_task.cancel()


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

    Robust fallback: if ``run_state_dir/runs/`` is empty, also checks
    ``run_state_dir/`` directly and the eval-agent's default in-tree
    ``state/runs/`` so older bundled versions that ignore ``--state-dir``
    are still handled gracefully.
    """
    def _read_from_dir(candidate: Path) -> list[dict[str, Any]] | None:
        runs_root = candidate / "runs"
        if not runs_root.exists():
            return None
        dirs = [p for p in runs_root.iterdir() if p.is_dir()]
        if not dirs:
            return None
        latest = max(dirs, key=lambda p: p.name)
        results = latest / "results.jsonl"
        if not results.exists():
            logger.debug("read_run_verdicts: run-dir %s has no results.jsonl", latest)
            return None
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
        if out:
            logger.info("read_run_verdicts: loaded %d verdicts from %s", len(out), results)
        return out

    # Primary: caller-supplied state_dir
    verdicts = _read_from_dir(run_state_dir)
    if verdicts is not None:
        return verdicts

    # Fallback 1: eval-agent in-tree default (handles older bundles that
    # ignored --state-dir/EVAL_AGENT_STATE_DIR).
    try:
        eval_root = locate_eval_agent()
        fallback = eval_root / "state"
        if fallback != run_state_dir:
            verdicts = _read_from_dir(fallback)
            if verdicts is not None:
                logger.warning(
                    "read_run_verdicts: primary state_dir %s had no verdicts; "
                    "found %d in eval-agent default %s — state_dir fix not active",
                    run_state_dir, len(verdicts), fallback,
                )
                return verdicts
    except FileNotFoundError:
        pass

    logger.warning(
        "read_run_verdicts: no results.jsonl found under %s (or fallback paths)",
        run_state_dir,
    )
    return []


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
    # The verdict cache + the accumulated `runs/<ts>/` artefacts live at
    # the per-run root so opening the modal again warm-hits prior
    # Gemini judgements. The per-session subdir holds only the
    # filtered fixture + SSE event log.
    state_dir = state / "ai-verify-sessions" / run_id
    base = state_dir / "sessions" / session_id
    pipeline_output = base / "pipeline-output"
    yield pipeline_output, base


def new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def ensure_verify_session_dir(
    *,
    run_id: str,
    session_id: str,
    channel: str,
) -> Path:
    """Persistent per-session dir for trace replay (modal + job consumers).

    ``channel`` is e.g. ``ai-verify-sessions`` or ``extraction-verify-sessions``.
    """
    root = locate_eval_agent()
    base = root / "state" / channel / run_id / "sessions" / session_id
    base.mkdir(parents=True, exist_ok=True)
    return base


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
        except Exception as exc:  # noqa: BLE001
            # Propagate generator errors to the client so the browser
            # gets a runner.error event instead of a silent stream-end
            # that leaves the "Start verification" button stuck/reset.
            err_payload = json.dumps({"message": str(exc)}, ensure_ascii=False)
            await queue.put(f"event: runner.error\ndata: {err_payload}\n\n")
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


def _resolve_session_dir(run_id: str, session_id: str) -> Path | None:
    """Find the on-disk dir for one session, accepting both layouts.

    New layout (post-cache-fix): per-run ``state_dir`` with sessions
    isolated under ``sessions/<session_id>/``; cache + runs/ live at
    the state_dir root so verdicts warm-hit across sessions.

    Legacy layout: session_id was a direct child of the run_id dir.
    Old sessions written before the fix still load via this fallback.
    """
    try:
        root = locate_eval_agent()
    except FileNotFoundError:
        return None
    new = root / "state" / "ai-verify-sessions" / run_id / "sessions" / session_id
    if new.exists():
        return new
    legacy = root / "state" / "ai-verify-sessions" / run_id / session_id
    if legacy.exists():
        return legacy
    return None


def list_sessions(run_id: str) -> list[dict[str, Any]]:
    """Return all past AI verification sessions for *run_id*, newest first.

    Walks both the new ``sessions/<session_id>/`` subdir and the legacy
    direct-child layout so sessions written before the cache-fix still
    appear in the list.
    """
    try:
        root = locate_eval_agent()
    except FileNotFoundError:
        return []
    run_root = root / "state" / "ai-verify-sessions" / run_id
    if not run_root.exists():
        return []
    candidates: list[Path] = []
    new_sessions = run_root / "sessions"
    if new_sessions.exists():
        candidates.extend(p for p in new_sessions.iterdir() if p.is_dir())
    # Legacy layout: every direct child that has a trace.jsonl is a
    # session. Skip the new "sessions" / "cache" / "runs" dirs.
    for p in run_root.iterdir():
        if not p.is_dir() or p.name in {"sessions", "cache", "runs"}:
            continue
        if (p / "trace.jsonl").exists():
            candidates.append(p)
    out: list[dict[str, Any]] = []
    for child in sorted(candidates, key=lambda p: p.name, reverse=True):
        meta = _session_meta(child)
        out.append({"session_id": child.name, **meta})
    return out


def _verdict_storage_key(v: dict[str, Any]) -> str:
    """Stable dedupe key for one verdict row (mirrors frontend verdictStorageKey)."""
    cand = v.get("candidate") if isinstance(v.get("candidate"), dict) else {}
    match_id = cand.get("_match_id")
    if match_id:
        return str(match_id)
    entity_id = cand.get("_entity_id")
    if entity_id:
        return str(entity_id)
    record_id = str(v.get("record_id") or cand.get("record_id") or "")
    name = str(cand.get("name") or cand.get("person") or cand.get("text") or "").strip()
    role = str(cand.get("role") or "").strip()
    sub_type = str(v.get("sub_type") or cand.get("sub_type") or "").strip()
    evaluator = str(v.get("evaluator_id") or cand.get("evaluator_id") or "").strip()
    idx = cand.get("_match_index")
    if record_id and name:
        return f"{record_id}|{name}|{role}|{sub_type}|{evaluator}|{idx if idx is not None else ''}"
    if record_id:
        return f"{record_id}|{idx if idx is not None else name}"
    return f"anon|{evaluator}|{sub_type}|{name}"


def _verdicts_from_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect agent.verdict payloads from a session trace, last-write-wins per key."""
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for ev in events:
        if ev.get("type") != "agent.verdict":
            continue
        payload = {k: val for k, val in ev.items() if k not in ("ts", "type")}
        key = _verdict_storage_key(payload)
        if key not in by_key:
            order.append(key)
        by_key[key] = payload
    return [by_key[k] for k in order]


def read_session(run_id: str, session_id: str) -> dict[str, Any] | None:
    base = _resolve_session_dir(run_id, session_id)
    if base is None:
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
    # Prefer verdicts replayed from this session's trace — the per-run
    # results.jsonl under state_dir/runs/ is shared across sessions and
    # may belong to a different scope (stale count vs scope_size).
    verdicts = _verdicts_from_trace(events)
    if not verdicts:
        state_dir = base.parent.parent if base.parent.name == "sessions" else base
        verdicts = read_run_verdicts(state_dir) or read_run_verdicts(base)
    return {
        "session_id": session_id,
        "run_id":     run_id,
        "events":     events,
        "verdicts":   verdicts,
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
    base = _resolve_session_dir(run_id, session_id)
    if base is not None and base.exists():
        shutil.rmtree(base, ignore_errors=True)


__all__ = [
    "AgentEvent",
    "build_filtered_fixture",
    "ensure_verify_session_dir",
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
