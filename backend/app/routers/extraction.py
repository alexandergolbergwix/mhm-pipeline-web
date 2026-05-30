"""Stage 2 — extraction (NER + genre classifier) endpoints.

All three endpoints are nested under a run, RBAC-gated through
``_lookup_run_with_access`` from the runs router so a viewer cannot
fire inference and an outsider gets a 403 (never a 404 → information
disclosure).

* ``POST /runs/{run_id}/extraction/start-stream`` — SSE. Streams the
  ``extraction.*`` events from ``app.pipeline.extraction`` while the
  per-record loop runs. Persists ``ner_results.json`` under
  ``backend/state/runs/{run_id}/``.

* ``GET /runs/{run_id}/extraction/results`` — returns the parsed
  ``ner_results.json`` (404 if Stage 2 hasn't completed for this run).

* ``GET /runs/{run_id}/extraction/status`` — ``idle``/``running``/
  ``complete``/``error``. Polled by the UI before deciding whether to
  re-fire the stream.

The HuggingFace access token is unwrapped per-request from the same
encrypted store that holds the Gemini and Wikidata tokens
(``app.models.api_key.ApiKey`` rows with ``key_name='huggingface'``).
The browser never sees the token; it is passed to the inference layer
in-process only.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.crypto import secrets as secrets_mod
from app.db import get_session
from app.models.api_key import ApiKey
from app.models.run import RunRecord
from app.pipeline.agent_runner import AgentEvent, sse_stream
from app.pipeline.extraction import ExtractionEvent, extract_entities_stream
from app.routers.runs import _lookup_run_with_access

logger = logging.getLogger(__name__)
router = APIRouter(tags=["extraction"])


# ── Per-run state directory ───────────────────────────────────────────


def _run_output_dir(run_id: uuid.UUID) -> Path:
    """Resolve the on-disk output directory for one run.

    Lives at ``backend/state/runs/{run_id}/`` so artefacts persist
    across uvicorn restarts. Mirrors the desktop pipeline's per-run
    output folder convention.
    """
    backend_root = Path(__file__).resolve().parents[2]
    return backend_root / "state" / "runs" / str(run_id)


def _results_path(run_id: uuid.UUID) -> Path:
    return _run_output_dir(run_id) / "ner_results.json"


# ── POST /runs/{run_id}/extraction/start-stream ───────────────────────


@router.post("/runs/{run_id}/extraction/start-stream")
async def start_extraction_stream(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Fire Stage 2 inference + stream progress events via SSE.

    The caller must have editor role on the run's project. The HF token
    is read from the calling user's encrypted store. If they haven't
    saved one, returns 400 with a friendly message pointing at the
    Settings → Credentials surface.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)

    # Fetch all parsed MARC records for this run.
    rows = (
        await db.execute(
            select(RunRecord)
            .where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run has no parsed records — re-upload the MARC file.",
        )
    marc_records = [dict(r.marc) for r in rows]

    # Unwrap the HF token. Hard-fail when absent; the UI prompts the
    # user to add one in Settings → Credentials.
    hf_token = await _unwrap_user_huggingface_key(
        db, user_id=auth.user.id, kek=auth.kek,
    )
    if not hf_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No HuggingFace access token configured. Open Settings → "
                "Credentials and add a token with 'read' access to "
                "alexgoldberg/hebrew-manuscript-joint-ner-v2."
            ),
        )

    output_dir = _run_output_dir(run_id)
    return StreamingResponse(
        sse_stream(_as_agent_events(extract_entities_stream(
            marc_records=marc_records,
            output_dir=output_dir,
            hf_token=hf_token,
        ))),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /runs/{run_id}/extraction/results ────────────────────────────


@router.get("/runs/{run_id}/extraction/results")
async def get_extraction_results(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return the parsed ``ner_results.json`` for this run.

    404 when Stage 2 hasn't been run yet (the caller can check
    ``/status`` first to avoid an exception).
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)
    path = _results_path(run_id)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stage 2 results not found — run extraction first.",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not parse ner_results.json: {exc.msg}",
        ) from exc
    if not isinstance(data, list):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ner_results.json is malformed (expected a list)",
        )
    return data


# ── GET /runs/{run_id}/extraction/status ─────────────────────────────


@router.get("/runs/{run_id}/extraction/status")
async def get_extraction_status(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Report extraction state for this run.

    ``idle``     — never run; no output file on disk.
    ``complete`` — ``ner_results.json`` exists and parses.
    ``error``    — the file exists but is unparseable.

    The desktop runner streams to a temp dir and only writes the final
    file atomically — so ``ner_results.json`` is either fully there or
    absent. We don't yet model ``running`` because every start-stream
    response holds the connection until completion; the caller knows
    the run is in flight by virtue of holding the SSE stream open.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)
    path = _results_path(run_id)
    if not path.exists():
        return {"state": "idle"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"state": "error", "detail": "ner_results.json unparseable"}
    if not isinstance(data, list):
        return {"state": "error", "detail": "ner_results.json malformed"}
    entity_total = sum(len((r or {}).get("entities") or []) for r in data)
    return {
        "state":         "complete",
        "records":       len(data),
        "entity_total":  entity_total,
        "results_path":  str(path),
    }


# ── helpers ───────────────────────────────────────────────────────────


async def _as_agent_events(stream):
    """Adapt ``ExtractionEvent`` → ``AgentEvent`` so ``sse_stream`` (which
    expects the latter) can serialise without an isinstance check."""
    async for ev in stream:
        if isinstance(ev, ExtractionEvent):
            yield AgentEvent(type=ev.type, payload=ev.payload)
        else:
            yield ev


async def _unwrap_user_huggingface_key(
    db: AsyncSession, *, user_id, kek: bytes,
) -> str | None:
    row = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == user_id, ApiKey.key_name == "huggingface",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        wrapped = secrets_mod.WrappedSecret(
            ciphertext=row.ciphertext,
            ciphertext_nonce=row.ciphertext_nonce,
            dek_wrapped=row.dek_wrapped,
            dek_wrap_nonce=row.dek_wrap_nonce,
        )
        return secrets_mod.unwrap_secret(wrapped, kek=kek)
    except InvalidTag:
        logger.warning("Failed to unwrap HuggingFace key for user %s", user_id)
        return None
