"""LLM-driven orchestrator endpoints.

Two endpoints — one to kick off a session and stream events live via
SSE, one to list / replay past sessions.

The eval-agent project lives in a sibling repo (Rule 48); we shell
out via ``app.pipeline.orchestrator_runner`` rather than importing.

Security: ``GEMINI_API_KEY`` is read from the per-user encrypted
secret store the wider app already has. The key is passed to the
subprocess via env var ONLY — never on argv (Rule 50).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.pipeline.orchestrator_runner import (
    OrchestratorEvent, locate_eval_agent, sse_stream, spawn_orchestrator,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["orchestrator"])


# ── Schemas ───────────────────────────────────────────────────────────


class OrchestratorRequest(BaseModel):
    """Payload for starting one orchestrator session.

    ``mode`` is gated at the eval-agent side too (Phase-2+ modes are
    pre-declared but ship empty allowlists), so callers can freely pass
    ``supervised``/``autonomous`` to test the wiring without risking a
    live mutation.
    """

    goal:        str   = Field(..., min_length=4, max_length=1024,
                                description="Natural-language goal.")
    mode:        str   = Field("plan_only", pattern=r"^(plan_only|supervised|autonomous)$")
    judge_model: str   = Field("gemini-3.5-flash", min_length=4, max_length=64)
    max_steps:   int   = Field(12, ge=1, le=64)
    max_seconds: int   = Field(180, ge=10, le=1800)
    max_usd:     float = Field(0.10, ge=0.0, le=5.0)
    # Useful for the UI's live-flow demo without a key, mirrors the
    # eval-agent CLI's --no-llm.
    use_stub_judge: bool = False


class SessionListing(BaseModel):
    """Past session as returned by GET /orchestrator/sessions."""

    session_id:    str
    started_at:    str | None = None
    ended_at:      str | None = None
    outcome:       str | None = None
    goal:          str | None = None
    mode:          str | None = None
    has_final:     bool = False


# ── POST /orchestrator/run-stream  — SSE event stream  ─────────────────


@router.post("/orchestrator/run-stream")
async def run_stream(
    payload: OrchestratorRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Kick off one orchestrator session and stream events via SSE.

    The browser opens this with EventSource (or fetch + getReader);
    each ``event:``/``data:`` pair carries one trace event from the
    eval-agent subprocess. The stream ends when the eval-agent exits.
    """
    # Resolve the Gemini API key. Stubs skip this — useful for the
    # frontend's animation demo + integration tests.
    api_key: str | None = None
    if not payload.use_stub_judge:
        api_key = await _resolve_gemini_key(db, auth)
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No Gemini API key configured. Open Settings → "
                    "Credentials and add one, or set use_stub_judge=true "
                    "to run a deterministic demo without an LLM."
                ),
            )

    try:
        locate_eval_agent()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    events = spawn_orchestrator(
        goal=payload.goal,
        mode=payload.mode,
        judge_model=payload.judge_model,
        api_key=api_key,
        max_steps=payload.max_steps,
        max_seconds=payload.max_seconds,
        max_usd=payload.max_usd,
        use_stub_judge=payload.use_stub_judge,
    )

    return StreamingResponse(
        sse_stream(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control":  "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx response buffering
        },
    )


# ── GET /orchestrator/sessions — list  ─────────────────────────────────


@router.get("/orchestrator/sessions", response_model=list[SessionListing])
async def list_sessions(
    limit: int = Query(20, ge=1, le=200),
    auth: AuthContext = Depends(current_auth),   # noqa: ARG001 — gate
) -> list[SessionListing]:
    """List past orchestrator sessions (newest first)."""
    sessions_dir = _sessions_root()
    if not sessions_dir.exists():
        return []
    out: list[SessionListing] = []
    for child in sorted(sessions_dir.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        meta = _session_meta(child)
        out.append(SessionListing(session_id=child.name, **meta))
        if len(out) >= limit:
            break
    return out


# ── GET /orchestrator/sessions/{id}/trace — replay  ────────────────────


@router.get("/orchestrator/sessions/{session_id}/trace")
async def session_trace(
    session_id: str,
    auth: AuthContext = Depends(current_auth),   # noqa: ARG001 — gate
) -> dict[str, Any]:
    """Return the full trace + final-report markdown for a past session."""
    base = _sessions_root() / session_id
    if not base.exists() or not base.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                             detail="session not found")
    events: list[dict[str, Any]] = []
    trace_file = base / "trace.jsonl"
    if trace_file.exists():
        for line in trace_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    final_md = base / "final_report.md"
    return {
        "session_id":  session_id,
        "events":      events,
        "final_report_md": (
            final_md.read_text(encoding="utf-8") if final_md.exists() else ""
        ),
    }


# ── Helpers ───────────────────────────────────────────────────────────


def _sessions_root() -> Path:
    """Where the eval-agent dropped session dirs."""
    return locate_eval_agent() / "state" / "orchestrator" / "sessions"


def _session_meta(session_dir: Path) -> dict[str, Any]:
    trace_file = session_dir / "trace.jsonl"
    if not trace_file.exists():
        return {"started_at": None, "ended_at": None,
                "outcome": None, "goal": None, "mode": None,
                "has_final": False}
    start: dict[str, Any] = {}
    end:   dict[str, Any] = {}
    has_final = False
    try:
        for line in trace_file.read_text(encoding="utf-8").splitlines():
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
            elif ev.get("type") == "session.final":
                has_final = True
    except OSError:
        pass
    return {
        "started_at": start.get("ts"),
        "ended_at":   end.get("ts"),
        "outcome":    end.get("outcome"),
        "goal":       start.get("goal"),
        "mode":       start.get("mode"),
        "has_final":  has_final,
    }


async def _resolve_gemini_key(db: AsyncSession, auth: AuthContext) -> str | None:
    """Pull the user's encrypted Gemini key from the shared secret store.

    The web app already has an api-keys mechanism for the candidate-
    level judge; reuse the same row so the orchestrator and the judge
    share one configured key. Falls back to env GEMINI_API_KEY for dev.
    """
    import os
    try:
        # Lazy-import to avoid a hard dep cycle with crypto/secrets in
        # tests that mock this router.
        from app.pipeline.ai_verifier import (  # noqa: PLC0415
            unwrap_user_gemini_key,
        )
        key = await unwrap_user_gemini_key(db, auth)
        if key:
            return key
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not unwrap stored Gemini key: %s", exc)
    return os.environ.get("GEMINI_API_KEY")
