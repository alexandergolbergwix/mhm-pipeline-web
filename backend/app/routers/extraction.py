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
from app.models.extraction_approval import ExtractionApproval
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
    mode: str | None = None,    # "local" | "hf-api"; default env / "local"
    models: str | None = None,  # CSV of {person,provenance,contents,genre}
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

    # Parse the enabled-models CSV. Default: all four. Unknown roles
    # are silently dropped; an empty set after parsing means "all".
    _ALLOWED = {"person", "provenance", "contents", "genre"}
    enabled: set[str] | None = None
    if models:
        picked = {p.strip() for p in models.split(",") if p.strip()}
        enabled = picked & _ALLOWED
        if not enabled:
            enabled = None    # treat as "all"

    output_dir = _run_output_dir(run_id)
    return StreamingResponse(
        sse_stream(_as_agent_events(extract_entities_stream(
            marc_records=marc_records,
            output_dir=output_dir,
            hf_token=hf_token,
            mode=mode,
            enabled_models=enabled,
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


# ── Entities + approvals ───────────────────────────────────────────────


from datetime import datetime, timezone  # noqa: E402

from pydantic import BaseModel, Field, field_validator  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402


class EntityEdit(BaseModel):
    """Patch payload for one extraction-approval row."""

    approved:      bool | None = None
    override_type: str | None = Field(default=None, max_length=64)
    override_role: str | None = Field(default=None, max_length=64)

    @field_validator("override_type", "override_role")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class BulkApprovePayload(BaseModel):
    """``POST /extraction/entities/bulk-approve`` body."""

    entity_ids: list[str] = Field(..., min_length=1)
    approved:   bool      = True


class AutoApprovePayload(BaseModel):
    """``POST /extraction/entities/auto-approve`` body."""

    min_confidence: float = Field(0.85, ge=0.0, le=1.0)
    # When set, only auto-approve rows whose source is in this list.
    sources:        list[str] | None = None
    # When True, refuse to flip rows the AI already failed.
    respect_ai_fail: bool = True


def _entity_id(*,
    control_number: str, source: str, text: str,
    start: int, end: int,
) -> str:
    """Encode the canonical entity key into a single URL-safe string.

    Used as both the on-disk content hash and the route-param id.
    Round-trips losslessly through ``_parse_entity_id``.
    """
    import base64, json as _json   # noqa: PLC0415
    payload = _json.dumps([control_number, source, text, int(start), int(end)],
                            ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _parse_entity_id(eid: str) -> tuple[str, str, str, int, int]:
    import base64, json as _json   # noqa: PLC0415
    pad = "=" * (-len(eid) % 4)
    raw = base64.urlsafe_b64decode((eid + pad).encode("ascii")).decode("utf-8")
    parts = _json.loads(raw)
    if not (isinstance(parts, list) and len(parts) == 5):
        raise ValueError(f"malformed entity id {eid!r}")
    return (str(parts[0]), str(parts[1]), str(parts[2]),
            int(parts[3]), int(parts[4]))


def _flatten_records(records: list[dict]) -> list[dict]:
    """Walk ``ner_results.json`` records → flat list of entity dicts.

    Each emitted dict carries:
        id, control_number, source, text, start, end, type, role,
        confidence, model_confidence, full_text (the record's full
        text — used by the detail dialog).

    Genre predictions are flattened as rows with ``source="genre"``,
    ``text=label``, ``confidence=conf``, start=end=0.
    """
    flat: list[dict] = []
    for rec in records:
        cn = str(rec.get("_control_number") or rec.get("control_number") or "")
        full_text = str(rec.get("text") or "")
        for ent in rec.get("entities") or []:
            src   = str(ent.get("source") or "")
            text  = str(ent.get("text") or "").strip()
            start = int(ent.get("start") or 0)
            end   = int(ent.get("end") or 0)
            if not (cn and src and text):
                continue
            flat.append({
                "id":               _entity_id(
                    control_number=cn, source=src, text=text,
                    start=start, end=end,
                ),
                "control_number":   cn,
                "source":           src,
                "text":             text,
                "start":            start,
                "end":              end,
                "type":             ent.get("type"),
                "role":             ent.get("role"),
                "confidence":       ent.get("confidence"),
                "model_confidence": ent.get("model_confidence"),
                "full_text":        full_text,
            })
        for genre in rec.get("ml_genres") or []:
            label = str(genre.get("label") or "").strip()
            if not (cn and label):
                continue
            flat.append({
                "id":               _entity_id(
                    control_number=cn, source="genre", text=label,
                    start=0, end=0,
                ),
                "control_number":   cn,
                "source":           "genre",
                "text":             label,
                "start":            0,
                "end":              0,
                "type":             None,
                "role":             None,
                "confidence":       float(genre.get("confidence") or 0.0),
                "model_confidence": float(genre.get("confidence") or 0.0),
                "full_text":        full_text,
            })
    return flat


@router.get("/runs/{run_id}/extraction/entities")
async def list_entities(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return every extracted entity for this run, joined with the
    curator's approval / override / AI-verdict state.

    One row per (control_number, source, text, start, end). Fast enough
    for typical run sizes (≤ a few thousand rows); the frontend filters
    + sorts client-side.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)

    # 1. Read ner_results.json off disk + flatten.
    path = _results_path(run_id)
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(records, list):
        return []
    flat = _flatten_records(records)

    # 2. Pull existing approval rows + key by entity_id.
    rows = (
        await db.execute(
            select(ExtractionApproval).where(
                ExtractionApproval.run_id == run_id,
            )
        )
    ).scalars().all()
    approvals = {
        _entity_id(
            control_number=r.control_number, source=r.source,
            text=r.text, start=r.start, end=r.end,
        ): r
        for r in rows
    }

    # 3. Merge.
    out: list[dict] = []
    for ent in flat:
        a = approvals.get(ent["id"])
        out.append({
            **ent,
            "approved":         bool(a.approved) if a else False,
            "override_type":    (a.override_type if a else None),
            "override_role":    (a.override_role if a else None),
            "effective_type":   (a.override_type if a and a.override_type else ent["type"]),
            "effective_role":   (a.override_role if a and a.override_role else ent["role"]),
            "ai_verdict":       (a.ai_verdict if a else None),
            "ai_verdict_at":    (a.ai_verdict_at.isoformat() if a and a.ai_verdict_at else None),
        })
    return out


@router.patch("/runs/{run_id}/extraction/entities/{entity_id}")
async def patch_entity(
    run_id: uuid.UUID,
    entity_id: str,
    payload: EntityEdit,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Set approval and/or override on a single entity.

    Uses Postgres' UPSERT so a first-time approval creates the row and
    subsequent calls update it. Snapshots the original prediction's
    type/role/confidence on first write so subsequent reads have full
    context even if the run's ner_results.json is rebuilt.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)
    try:
        cn, src, text, start, end = _parse_entity_id(entity_id)
    except (ValueError, Exception) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"bad entity_id: {exc}",
        ) from exc

    # Look up the entity dict from disk for the prediction snapshot.
    pred_type:  str | None = None
    pred_role:  str | None = None
    pred_conf:  float | None = None
    pred_mconf: float | None = None
    path = _results_path(run_id)
    if path.exists():
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            records = []
        for ent in _flatten_records(records):
            if ent["id"] == entity_id:
                pred_type, pred_role = ent.get("type"), ent.get("role")
                pred_conf  = ent.get("confidence")
                pred_mconf = ent.get("model_confidence")
                break

    now = datetime.now(timezone.utc)
    stmt = pg_insert(ExtractionApproval).values(
        run_id=run_id, control_number=cn, source=src, text=text,
        start=start, end=end,
        type=pred_type, role=pred_role,
        confidence=pred_conf, model_confidence=pred_mconf,
        approved=bool(payload.approved) if payload.approved is not None else False,
        approved_by=auth.user.id if payload.approved else None,
        approved_at=now if payload.approved else None,
        override_type=payload.override_type,
        override_role=payload.override_role,
    )
    update_cols: dict = {}
    if payload.approved is not None:
        update_cols["approved"]    = bool(payload.approved)
        update_cols["approved_by"] = auth.user.id if payload.approved else None
        update_cols["approved_at"] = now if payload.approved else None
    if payload.override_type is not None:
        update_cols["override_type"] = payload.override_type or None
    if payload.override_role is not None:
        update_cols["override_role"] = payload.override_role or None
    stmt = stmt.on_conflict_do_update(
        constraint="uq_extraction_approval_key",
        set_=update_cols or {"updated_at": now},
    ).returning(ExtractionApproval)
    row = (await db.execute(stmt)).scalar_one()
    await db.commit()

    return {
        "id":             entity_id,
        "approved":       bool(row.approved),
        "override_type":  row.override_type,
        "override_role":  row.override_role,
        "ai_verdict":     row.ai_verdict,
    }


@router.post("/runs/{run_id}/extraction/entities/bulk-approve")
async def bulk_approve_entities(
    run_id: uuid.UUID,
    payload: BulkApprovePayload,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Approve/unapprove many entities in one round-trip."""
    await _lookup_run_with_access(db, run_id, auth, write=True)

    now = datetime.now(timezone.utc)
    # Build (entity_id → prediction snapshot) map from disk so even
    # first-touch rows get a full snapshot.
    snapshot: dict[str, dict] = {}
    path = _results_path(run_id)
    if path.exists():
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            records = []
        for ent in _flatten_records(records):
            snapshot[ent["id"]] = ent

    rows_to_upsert: list[dict] = []
    for eid in payload.entity_ids:
        try:
            cn, src, text, start, end = _parse_entity_id(eid)
        except Exception:
            continue
        ent_snap = snapshot.get(eid, {})
        rows_to_upsert.append({
            "run_id":          run_id,
            "control_number":  cn,
            "source":          src,
            "text":            text,
            "start":           start,
            "end":             end,
            "type":            ent_snap.get("type"),
            "role":            ent_snap.get("role"),
            "confidence":      ent_snap.get("confidence"),
            "model_confidence": ent_snap.get("model_confidence"),
            "approved":        payload.approved,
            "approved_by":     auth.user.id if payload.approved else None,
            "approved_at":     now if payload.approved else None,
        })
    if not rows_to_upsert:
        return {"updated": 0}

    stmt = pg_insert(ExtractionApproval).values(rows_to_upsert)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_extraction_approval_key",
        set_={
            "approved":     stmt.excluded.approved,
            "approved_by":  stmt.excluded.approved_by,
            "approved_at":  stmt.excluded.approved_at,
        },
    )
    await db.execute(stmt)
    await db.commit()
    return {"updated": len(rows_to_upsert), "approved": payload.approved}


@router.post("/runs/{run_id}/extraction/entities/auto-approve")
async def auto_approve_entities(
    run_id: uuid.UUID,
    payload: AutoApprovePayload,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Auto-approve every entity whose model_confidence ≥ threshold.

    Honours ``payload.sources`` (subset filter) and
    ``payload.respect_ai_fail`` (skip rows the AI already failed).
    Returns ``{checked, approved}``.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)

    path = _results_path(run_id)
    if not path.exists():
        return {"checked": 0, "approved": 0}
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"checked": 0, "approved": 0}

    # Pull existing rows so we can honour respect_ai_fail.
    rows = (
        await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
        )
    ).scalars().all()
    ai_fail = set()
    for r in rows:
        if r.ai_verdict and str(r.ai_verdict.get("overall") or "").lower() == "fail":
            ai_fail.add(_entity_id(
                control_number=r.control_number, source=r.source,
                text=r.text, start=r.start, end=r.end,
            ))

    now = datetime.now(timezone.utc)
    eligible: list[dict] = []
    checked = 0
    for ent in _flatten_records(records):
        checked += 1
        if payload.sources and ent["source"] not in payload.sources:
            continue
        mconf = float(ent.get("model_confidence") or 0.0)
        if mconf < payload.min_confidence:
            continue
        if payload.respect_ai_fail and ent["id"] in ai_fail:
            continue
        eligible.append({
            "run_id":          run_id,
            "control_number":  ent["control_number"],
            "source":          ent["source"],
            "text":            ent["text"],
            "start":           ent["start"],
            "end":             ent["end"],
            "type":            ent.get("type"),
            "role":            ent.get("role"),
            "confidence":      ent.get("confidence"),
            "model_confidence": ent.get("model_confidence"),
            "approved":        True,
            "approved_by":     auth.user.id,
            "approved_at":     now,
        })

    if eligible:
        stmt = pg_insert(ExtractionApproval).values(eligible)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_extraction_approval_key",
            set_={
                "approved":     stmt.excluded.approved,
                "approved_by":  stmt.excluded.approved_by,
                "approved_at":  stmt.excluded.approved_at,
            },
        )
        await db.execute(stmt)
        await db.commit()

    return {"checked": checked, "approved": len(eligible)}
