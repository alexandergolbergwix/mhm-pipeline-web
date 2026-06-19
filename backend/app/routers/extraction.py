"""AI Extraction — extraction (NER + genre classifier) endpoints.

All three endpoints are nested under a run, RBAC-gated through
``_lookup_run_with_access`` from the runs router so a viewer cannot
fire inference and an outsider gets a 403 (never a 404 → information
disclosure).

* ``POST /runs/{run_id}/extraction/start-stream`` — SSE. Streams the
  ``extraction.*`` events from ``app.pipeline.extraction`` while the
  per-record loop runs. Persists ``ner_results.json`` under
  ``backend/state/runs/{run_id}/``.

* ``GET /runs/{run_id}/extraction/results`` — returns the parsed
  ``ner_results.json`` (404 if AI Extraction hasn't completed for this run).

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
from typing import Any

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.crypto import secrets as secrets_mod
from app.db import get_session
from app.models.api_key import ApiKey
from app.models.event import (
    ENTITY_TYPE_EXTRACTION_ENTITY,
    OP_CREATE,
    OP_PATCH,
    ProjectEvent,
)
from app.models.extraction_approval import ExtractionApproval
from app.models.run import RunRecord
from app.pipeline.agent_runner import AgentEvent, sse_stream
from app.pipeline.extraction import ExtractionEvent, extract_entities_stream
from app.pipeline.marc_structured_index import MarcStructuredIndex
from app.pipeline.ner_verdict_cache import sanitise_stale_ai_verdict
from app.cache.scoped_cache import scoped_cache_get, scoped_cache_lookup_or_call
from app.pipeline.extraction_entities_cache import (
    ENTITIES_CACHE_KIND,
    compute_entities_fingerprint,
    entities_cacheable,
    entities_etag,
    invalidate_entities_cache,
)
from app.routers.runs import _lookup_run_with_access
from app.versioning import apply_event

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
    skip_cache: bool = False,   # skip shared inference_cache (force fresh)
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Fire AI Extraction inference + stream progress events via SSE.

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
    # Defensive re-derivation of the flat NER-input keys (notes,
    # provenance, contents, colophon_text) from raw MARC subfield
    # columns. Without this, records uploaded before the marc_ingest
    # fix have empty NER inputs and Modal returns 0 entities on
    # every record. Idempotent — only fires when raw subfield keys
    # are present.
    from app.pipeline.marc_ingest import _collapse_marc_subfields  # noqa: PLC0415
    marc_records: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(r.marc) if r.marc else {}
        if any("$" in k for k in rec):
            _collapse_marc_subfields(rec)
        marc_records.append(rec)

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
        sse_stream(_as_agent_events(_extract_and_persist(
            extract_entities_stream(
                marc_records=marc_records,
                output_dir=output_dir,
                hf_token=hf_token,
                mode=mode,
                enabled_models=enabled,
                db_session=db,
                user_id=auth.user.id,
                skip_cache=skip_cache,
            ),
            run_id=run_id,
            db=db,
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

    404 when AI Extraction hasn't been run yet (the caller can check
    ``/status`` first to avoid an exception).
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)
    path = _results_path(run_id)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI Extraction results not found — run extraction first.",
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
    # Probe the resolved inference backend so the frontend can render
    # "Inference: Modal" / "Inference: HuggingFace" / etc. before the
    # first run streams an `extraction.start` event. Driven by the
    # EXTRACTION_MODE env var via app.pipeline.extraction_backend.
    from app.pipeline.extraction_backend import resolve_mode  # noqa: PLC0415
    extraction_mode = resolve_mode()

    path = _results_path(run_id)
    if not path.exists():
        # File absent (ephemeral filesystem wipe on deploy/restart).
        # Fall back to the durable DB store: if extraction_approvals has
        # rows for this run, the extraction did complete at some point.
        from sqlalchemy import func as _func  # noqa: PLC0415
        entity_total = (
            await db.execute(
                select(_func.count(ExtractionApproval.id)).where(
                    ExtractionApproval.run_id == run_id,
                )
            )
        ).scalar_one()
        if entity_total > 0:
            return {
                "state":           "complete",
                "records":         None,
                "entity_total":    entity_total,
                "extraction_mode": extraction_mode,
            }
        return {"state": "idle", "extraction_mode": extraction_mode}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "state": "error",
            "detail": "ner_results.json unparseable",
            "extraction_mode": extraction_mode,
        }
    if not isinstance(data, list):
        return {
            "state": "error",
            "detail": "ner_results.json malformed",
            "extraction_mode": extraction_mode,
        }
    entity_total = sum(len((r or {}).get("entities") or []) for r in data)
    return {
        "state":            "complete",
        "records":          len(data),
        "entity_total":     entity_total,
        "results_path":     str(path),
        "extraction_mode":  extraction_mode,
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


async def _extract_and_persist(
    inner_stream,
    *,
    run_id: uuid.UUID,
    db: "AsyncSession",
):
    """Wrap the extraction stream; on ``extraction.end`` bulk-upsert every
    entity into ``extraction_approvals`` so they survive ephemeral-filesystem
    wipes (Heroku deploy / dyno restart).

    This is the canonical durable store.  ``ner_results.json`` becomes a
    fast-path cache that speeds up subsequent reads; the DB is the source of
    truth when the file is absent.
    """
    async for ev in inner_stream:
        yield ev
        if ev.type == "extraction.end":
            path = _results_path(run_id)
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        await _bulk_persist_entities(db, run_id, data)
                except Exception as exc:   # noqa: BLE001
                    logger.warning(
                        "Entity persistence to DB failed for run %s: %s",
                        run_id, exc,
                    )


async def _bulk_persist_entities(
    db: "AsyncSession",
    run_id: uuid.UUID,
    results: list[dict],
) -> None:
    """Upsert all extracted entities into ``extraction_approvals``.

    On INSERT: row is created with ``approved=False`` and the full
    prediction snapshot (type, role, confidence, model_confidence).

    On CONFLICT: only the prediction snapshot columns are updated so
    curator decisions (approved, override_type, override_role, ai_verdict)
    are never clobbered by a re-run.
    """
    flat = _flatten_records(results)
    if not flat:
        return

    # Build the MARC grounding index ONCE here (stream-end) and snapshot
    # each entity's classification into the row, so list_entities never
    # rebuilds it on every poll (Rule W-16 §10). Deterministic for a given
    # (entity text, MARC record), so a snapshot is safe.
    marc_rows = (
        await db.execute(select(RunRecord).where(RunRecord.run_id == run_id))
    ).scalars().all()
    marc_index = MarcStructuredIndex.from_records(
        dict(r.marc or {}) for r in marc_rows
    )

    def _exists_in(ent: dict) -> dict:
        candidate_type = ent.get("type") or ent.get("role") or ent.get("source")
        return marc_index.classify(
            ent["control_number"], ent["text"],
            candidate_type=str(candidate_type) if candidate_type else None,
        )

    rows = [
        {
            "run_id":           run_id,
            "control_number":   ent["control_number"],
            "source":           ent["source"],
            "text":             ent["text"],
            "start":            int(ent.get("start") or 0),
            "end":              int(ent.get("end") or 0),
            "type":             ent.get("type") or None,
            "role":             ent.get("role") or None,
            "confidence":       ent.get("confidence"),
            "model_confidence": ent.get("model_confidence"),
            "approved":         False,
            "exists_in":        _exists_in(ent),
        }
        for ent in flat
    ]
    if not rows:
        return
    stmt = pg_insert(ExtractionApproval).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_extraction_approval_key",
        set_={
            "type":             stmt.excluded.type,
            "role":             stmt.excluded.role,
            "confidence":       stmt.excluded.confidence,
            "model_confidence": stmt.excluded.model_confidence,
            "exists_in":        stmt.excluded.exists_in,
        },
    )
    await db.execute(stmt)
    await db.commit()
    logger.info("Persisted %d entities to DB for run %s", len(rows), run_id)


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

    approved:       bool | None = None
    override_type:  str | None = Field(default=None, max_length=64)
    override_role:  str | None = Field(default=None, max_length=64)
    override_text:  str | None = Field(default=None, max_length=512)

    @field_validator("override_type", "override_role", "override_text")
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
    """``POST /extraction/entities/auto-approve[/preview]`` body."""

    min_confidence:  float           = Field(0.85, ge=0.0, le=1.0)
    sources:         list[str] | None = None
    types:           list[str] | None = None
    not_roles:       list[str] | None = None
    # When True, only approve entities that already have an AI verdict of pass.
    require_ai_pass: bool = False
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


async def _emit_extraction_event(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    row: ExtractionApproval,
    actor_id: uuid.UUID,
    message: str = "extraction edit",
) -> None:
    """Append a versioning event for one ExtractionApproval row.

    Failure is swallowed (logged at WARN) so a versioning bug can never
    500 the surrounding handler. The caller owns the transaction and
    must NOT commit here.
    """
    entity_id_str = str(row.id)
    try:
        has_history = (
            await db.execute(
                select(ProjectEvent.id)
                .where(
                    ProjectEvent.entity_type == ENTITY_TYPE_EXTRACTION_ENTITY,
                    ProjectEvent.entity_id == entity_id_str,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        op_kind = OP_PATCH if has_history else OP_CREATE
        new_state = {
            "approved":       bool(row.approved),
            "override_type":  row.override_type,
            "override_role":  row.override_role,
            "override_text":  row.override_text,
            "ai_verdict":     row.ai_verdict,
        }
        await apply_event(
            db,
            project_id=project_id,
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id_str,
            op=op_kind,
            new_state=new_state,
            actor_id=actor_id,
            message=message,
        )
    except Exception as exc:    # noqa: BLE001 — versioning must never 500
        logger.warning(
            "apply_event failed for extraction_entity %s: %s", row.id, exc,
        )


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
            # JointNERPipeline (Person NER, source=person_ner) emits
            # entity dicts with a ``person`` field rather than ``text``;
            # the other NER channels (provenance / contents) use
            # ``text``. Accept both so person rows aren't silently
            # dropped from the entity table.
            text  = str(ent.get("text") or ent.get("person") or "").strip()
            start = int(ent.get("start") or 0)
            end   = int(ent.get("end") or 0)
            if not (cn and src and text):
                continue
            # Each NER channel has a canonical entity TYPE in the
            # entity-table sense:
            #   person_ner     → PERSON (always)
            #   provenance_ner → uses the per-entity ``type`` field
            #                    (OWNER / DATE / COLLECTION)
            #   contents_ner   → uses the per-entity ``type`` field
            #                    (WORK / FOLIO / WORK_AUTHOR)
            # When the entity dict has no ``type`` field (the joint
            # Person NER doesn't write one — it writes the role
            # instead), fall back to PERSON for person_ner rows so the
            # type chip filter + the type column aren't blank.
            raw_type = ent.get("type")
            ent_type = (str(raw_type) if raw_type
                        else "PERSON" if src == "person_ner"
                        else "")
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
                "type":             ent_type,
                "role":             ent.get("role") or "",
                "confidence":       ent.get("confidence"),
                "model_confidence": ent.get("model_confidence"),
                "full_text":        full_text,
            })
        for genre in rec.get("ml_genres") or []:
            label = str(genre.get("label") or "").strip()
            if not (cn and label):
                continue
            # Use ``genre_ml`` as the source so the frontend chip
            # filter (Source = genre_ml) matches; type=GENRE so the
            # Type filter also categorises it.
            flat.append({
                "id":               _entity_id(
                    control_number=cn, source="genre_ml", text=label,
                    start=0, end=0,
                ),
                "control_number":   cn,
                "source":           "genre_ml",
                "text":             label,
                "start":            0,
                "end":              0,
                "type":             "GENRE",
                "role":             "",
                "confidence":       float(genre.get("confidence") or 0.0),
                "model_confidence": float(genre.get("confidence") or 0.0),
                "full_text":        full_text,
            })
    return flat


def _entity_dict_from_approval_row(r: ExtractionApproval) -> dict:
    """Build a flat entity dict from a durable ``extraction_approvals`` row."""
    return {
        "id":               _entity_id(
            control_number=r.control_number, source=r.source,
            text=r.text, start=int(r.start or 0), end=int(r.end or 0),
        ),
        "control_number":   r.control_number,
        "source":           r.source,
        "text":             r.text,
        "start":            int(r.start or 0),
        "end":              int(r.end or 0),
        "type":             r.type or "",
        "role":             r.role or "",
        "confidence":       r.confidence,
        "model_confidence": r.model_confidence,
        "full_text":        "",
    }


async def _build_unfiltered_entities_bundle(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """Merge ner_results + approvals + exists_in backfill (unfiltered)."""
    path = _results_path(run_id)
    db_rows = (
        await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
        )
    ).scalars().all()

    if not path.exists():
        if not db_rows:
            return {
                "out": [], "approved_count": 0, "record_count": 0,
                "source_counts": {},
            }
        flat = [_entity_dict_from_approval_row(r) for r in db_rows]
    else:
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            records = []
        flat = _flatten_records(records) if isinstance(records, list) else []
        if db_rows and len(flat) < len(db_rows):
            by_id = {e["id"]: e for e in flat}
            for r in db_rows:
                eid = _entity_id(
                    control_number=r.control_number, source=r.source,
                    text=r.text, start=int(r.start or 0), end=int(r.end or 0),
                )
                if eid not in by_id:
                    by_id[eid] = _entity_dict_from_approval_row(r)
            flat = list(by_id.values())

    approvals = {
        _entity_id(
            control_number=r.control_number, source=r.source,
            text=r.text, start=r.start, end=r.end,
        ): r
        for r in db_rows
    }

    out: list[dict] = []
    for ent in flat:
        a = approvals.get(ent["id"])
        eff_type = (a.override_type if a and a.override_type else ent["type"])
        eff_role = (a.override_role if a and a.override_role else ent["role"])
        eff_text = (a.override_text if a and a.override_text else ent["text"])
        out.append({
            **ent,
            "approval_row_id":  str(a.id) if a else None,
            "approved":         bool(a.approved) if a else False,
            "rejected":         False,
            "override_type":    (a.override_type if a else None),
            "override_role":    (a.override_role if a else None),
            "override_text":    (a.override_text if a else None),
            "effective_type":   eff_type,
            "effective_role":   eff_role,
            "effective_text":   eff_text,
            "type":             eff_type,
            "role":             eff_role,
            "text":             eff_text,
            "ai_verdict":       sanitise_stale_ai_verdict({
                **ent,
                "override_type": a.override_type if a else None,
                "override_role": a.override_role if a else None,
                "override_text": a.override_text if a else None,
                "type": eff_type,
                "role": eff_role,
                "text": eff_text,
                "ai_verdict": a.ai_verdict if a else None,
            }),
            "ai_verdict_at":    (a.ai_verdict_at.isoformat() if a and a.ai_verdict_at else None),
            "exists_in":        (a.exists_in if a and a.exists_in else None),
        })

    null_entities = [e for e in out if e.get("exists_in") is None]
    if null_entities:
        marc_rows_for_backfill = (
            await db.execute(
                select(RunRecord).where(RunRecord.run_id == run_id)
            )
        ).scalars().all()
        marc_index_backfill = MarcStructuredIndex.from_records(
            dict(r.marc or {}) for r in marc_rows_for_backfill
        )
        updated_ids: list[uuid.UUID] = []
        for ent in null_entities:
            candidate_type = ent.get("type") or ent.get("role") or ent.get("source")
            ei = marc_index_backfill.classify(
                ent["control_number"], ent["text"],
                candidate_type=str(candidate_type) if candidate_type else None,
            )
            ent["exists_in"] = ei
            a = approvals.get(ent["id"])
            if a:
                a.exists_in = ei
                updated_ids.append(a.id)
        if updated_ids:
            await db.commit()
            logger.info(
                "Backfilled exists_in for %d entities on run %s",
                len(updated_ids), run_id,
            )

    approved_count = sum(1 for e in out if e["approved"])
    record_count = len({e["control_number"] for e in out})
    source_counts: dict[str, int] = {}
    for e in out:
        src = str(e.get("source") or "")
        source_counts[src] = source_counts.get(src, 0) + 1

    return {
        "out":            out,
        "approved_count": approved_count,
        "record_count":   record_count,
        "source_counts":  source_counts,
    }


@router.get("/runs/{run_id}/extraction/entities")
async def list_entities(
    run_id: uuid.UUID,
    source:      str | None  = Query(None),
    type_filter: str | None  = Query(None, alias="type"),
    role_filter: str | None  = Query(None, alias="role"),
    approved:    bool | None  = Query(None),
    search:      str | None  = Query(None),
    sort_by:     str | None  = Query(None),
    sort_dir:    str          = Query("asc"),
    page:        int | None   = Query(None, ge=1),
    page_size:   int | None   = Query(None, ge=1, le=2000),
    no_cache:    bool         = Query(False),
    if_none_match: str | None = Header(None, alias="If-None-Match"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
):
    """Return extracted entities for this run, joined with curator
    approval / override / AI-verdict state, plus run-level aggregates.

    Response shape::

        {entities, total, approved_count, record_count, source_counts,
         page, page_size}

    ``total`` is the count AFTER the optional filters (for pagination);
    ``approved_count`` / ``record_count`` / ``source_counts`` are over the
    full unfiltered run so the header summary always shows run totals.

    The ``source`` / ``type`` / ``role`` / ``approved`` / ``search``
    filters, ``sort_by`` + ``sort_dir``, and ``page`` + ``page_size`` are
    all OPTIONAL — with none supplied the endpoint returns every entity
    (the historical behaviour the entity table relies on).

    ``exists_in`` (MARC grounding) is read straight from the row — it was
    snapshotted once at stream-end by ``_bulk_persist_entities`` — so this
    GET no longer rebuilds the full ``MarcStructuredIndex`` on every poll.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)

    path = _results_path(run_id)
    fingerprint = await compute_entities_fingerprint(db, run_id, path)
    etag = entities_etag(fingerprint)

    if if_none_match and if_none_match.strip('"') == fingerprint:
        return Response(status_code=304, headers={"ETag": etag})

    can_cache = entities_cacheable(
        source=source, type_filter=type_filter, role_filter=role_filter,
        approved=approved, search=search, sort_by=sort_by,
        page=page, page_size=page_size,
    )
    cache_summary = {"fingerprint": fingerprint}
    cache_status = "BYPASS"

    if can_cache and not no_cache:
        cached = await scoped_cache_get(
            scope="run", scope_id=str(run_id),
            kind=ENTITIES_CACHE_KIND, query_summary=cache_summary,
        )
        if cached is not None:
            bundle = cached
            cache_status = "HIT"
        else:
            async def _fetch_bundle() -> dict[str, Any]:
                return await _build_unfiltered_entities_bundle(db, run_id)

            bundle = await scoped_cache_lookup_or_call(
                scope="run", scope_id=str(run_id),
                kind=ENTITIES_CACHE_KIND, query_summary=cache_summary,
                fetch=_fetch_bundle,
            )
            cache_status = "MISS"
    else:
        bundle = await _build_unfiltered_entities_bundle(db, run_id)

    if not bundle["out"]:
        body = _empty_entities_response(page, page_size)
    else:
        rows = _filter_entities(
            bundle["out"], source=source, type_filter=type_filter,
            role_filter=role_filter, approved=approved, search=search,
        )
        rows = _sort_entities(rows, sort_by=sort_by, sort_dir=sort_dir)
        total = len(rows)
        if page is not None and page_size is not None:
            start = (page - 1) * page_size
            rows = rows[start:start + page_size]
        body = {
            "entities":       rows,
            "total":          total,
            "approved_count": bundle["approved_count"],
            "record_count":   bundle["record_count"],
            "source_counts":  bundle["source_counts"],
            "page":           page,
            "page_size":      page_size,
        }

    return JSONResponse(
        content=body,
        headers={"ETag": etag, "X-Cache": cache_status},
    )


def _empty_entities_response(page: int | None, page_size: int | None) -> dict:
    return {
        "entities": [], "total": 0, "approved_count": 0,
        "record_count": 0, "source_counts": {},
        "page": page, "page_size": page_size,
    }


def _filter_entities(
    rows: list[dict], *, source: str | None, type_filter: str | None,
    role_filter: str | None, approved: bool | None, search: str | None,
) -> list[dict]:
    out = rows
    if source:
        out = [e for e in out if e.get("source") == source]
    if type_filter:
        out = [e for e in out if (e.get("effective_type") or e.get("type")) == type_filter]
    if role_filter:
        out = [e for e in out if (e.get("effective_role") or e.get("role")) == role_filter]
    if approved is not None:
        out = [e for e in out if bool(e.get("approved")) == approved]
    if search:
        needle = search.strip().lower()
        if needle:
            out = [
                e for e in out
                if needle in str(e.get("text") or "").lower()
                or needle in str(e.get("control_number") or "").lower()
            ]
    return out


def _sort_entities(rows: list[dict], *, sort_by: str | None, sort_dir: str) -> list[dict]:
    if not sort_by:
        return rows
    reverse = sort_dir == "desc"
    numeric = sort_by in ("confidence", "model_confidence")
    if numeric:
        return sorted(rows, key=lambda e: (e.get(sort_by) is None, e.get(sort_by) or 0.0), reverse=reverse)
    return sorted(
        rows,
        key=lambda e: (e.get(sort_by) is None, str(e.get(sort_by) or "").lower()),
        reverse=reverse,
    )


# ── GET /runs/{run_id}/extraction/marc-source/{control_number} ────────


@router.get("/runs/{run_id}/extraction/marc-source/{control_number}")
async def get_marc_source(
    run_id: uuid.UUID,
    control_number: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Return the full MARC record + its extracted entities.

    Powers the right-side MARC source drawer in the AI Extraction review
    surface (Rule W-16): a curator clicks one NER hit and sees the
    manuscript's full structured fields side-by-side with every entity
    AI Extraction found for that record.

    The entity list is drawn from BOTH (a) the persisted
    ``ExtractionApproval`` rows (with curator overrides applied) and
    (b) the raw ``ner_results.json`` on disk when no approval row
    exists yet. The latter is the common case for the first curator
    to open a record — without it the drawer would show "no entities"
    even though the table clearly does.

    404 when no MARC record exists for that control_number under this
    run (the URL is misspelled or the run is mis-scoped).
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)

    try:
        rec = (
            await db.execute(
                select(RunRecord).where(
                    RunRecord.run_id == run_id,
                    RunRecord.control_number == control_number,
                )
            )
        ).scalar_one_or_none()
        if rec is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No MARC record for control_number {control_number!r} "
                    f"under this run."
                ),
            )

        ext_rows = (
            await db.execute(
                select(ExtractionApproval).where(
                    ExtractionApproval.run_id == run_id,
                    ExtractionApproval.control_number == control_number,
                ).order_by(
                    ExtractionApproval.source,
                    ExtractionApproval.start,
                    ExtractionApproval.text,
                )
            )
        ).scalars().all()

        approval_ids: set[str] = set()
        entities: list[dict] = []
        for r in ext_rows:
            eid = _entity_id(
                control_number=r.control_number, source=r.source,
                text=r.text, start=r.start, end=r.end,
            )
            approval_ids.add(eid)
            entities.append({
                "id":     eid,
                "text":   r.override_text or r.text,
                "type":   r.override_type or r.type or "",
                "role":   r.override_role or r.role or "",
                "start":  int(r.start or 0),
                "end":    int(r.end or 0),
                "source": r.source,
            })

        # Fallback to ner_results.json for entities that don't have an
        # ExtractionApproval row yet (the common case before any
        # curator approve/edit happens). Without this the drawer
        # shows "No entities" even though the entity table clearly has
        # rows.
        path = _results_path(run_id)
        if path.exists():
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                records = []
            if isinstance(records, list):
                for raw in _flatten_records(records):
                    if raw.get("control_number") != control_number:
                        continue
                    if raw["id"] in approval_ids:
                        continue
                    entities.append({
                        "id":     raw["id"],
                        "text":   raw["text"],
                        "type":   raw.get("type") or "",
                        "role":   raw.get("role") or "",
                        "start":  int(raw.get("start") or 0),
                        "end":    int(raw.get("end") or 0),
                        "source": raw["source"],
                    })

        # Coerce the MARC payload to a JSON-able dict. RunRecord.marc
        # is a JSONB column → returns a dict on Postgres, a parsed
        # dict on SQLite. Defensive: if anything else (a string, a
        # list) comes back, wrap it under a "_raw" key so the drawer
        # still renders something useful.
        raw_marc = rec.marc
        if isinstance(raw_marc, dict):
            marc_dict = raw_marc
        elif raw_marc is None:
            marc_dict = {}
        else:
            marc_dict = {"_raw": raw_marc}

        return {
            "control_number": control_number,
            "marc":           marc_dict,
            "entities":       entities,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "get_marc_source failed for run_id=%s control_number=%s",
            run_id, control_number,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load MARC source — see server logs.",
        )


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
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
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

    input_changing = (
        payload.override_text is not None
        or payload.override_type is not None
        or payload.override_role is not None
    )
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
        override_text=payload.override_text,
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
    if payload.override_text is not None:
        update_cols["override_text"] = payload.override_text or None
    update_cols["updated_at"] = now
    stmt = stmt.on_conflict_do_update(
        constraint="uq_extraction_approval_key",
        set_=update_cols,
    ).returning(ExtractionApproval)
    row = (await db.execute(stmt)).scalar_one()

    if input_changing:
        row.ai_verdict = None
        row.ai_verdict_at = None
        eff_text_now = row.override_text or row.text
        eff_type_now = row.override_type or row.type or ""
        rec = (
            await db.execute(
                select(RunRecord).where(
                    RunRecord.run_id == run_id,
                    RunRecord.control_number == cn,
                )
            )
        ).scalar_one_or_none()
        if rec is not None:
            marc_index = MarcStructuredIndex.from_records([dict(rec.marc or {})])
            candidate_type = eff_type_now or row.role or row.source
            row.exists_in = marc_index.classify(
                cn, eff_text_now,
                candidate_type=str(candidate_type) if candidate_type else None,
            )

    await _emit_extraction_event(
        db,
        project_id=run.project_id,
        row=row,
        actor_id=auth.user.id,
        message="extraction edit",
    )
    await db.commit()
    await invalidate_entities_cache(run_id)

    eff_type = row.override_type or row.type or ""
    eff_role = row.override_role or row.role or ""
    eff_text = row.override_text or row.text
    return {
        "id":             entity_id,
        "approval_row_id": str(row.id),
        "control_number": cn,
        "text":           eff_text,
        "type":           eff_type,
        "role":           eff_role,
        "approved":       bool(row.approved),
        "override_type":  row.override_type,
        "override_role":  row.override_role,
        "override_text":  row.override_text,
        "effective_text": eff_text,
        "effective_type": eff_type,
        "effective_role": eff_role,
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
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

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
    ).returning(ExtractionApproval)
    touched = (await db.execute(stmt)).scalars().all()
    for row in touched:
        await _emit_extraction_event(
            db,
            project_id=run.project_id,
            row=row,
            actor_id=auth.user.id,
            message="extraction bulk approve",
        )
    await db.commit()
    await invalidate_entities_cache(run_id)
    return {"updated": len(rows_to_upsert), "approved": payload.approved}


async def _auto_approve_eligible(
    db: AsyncSession,
    run_id: uuid.UUID,
    payload: AutoApprovePayload,
) -> list[dict]:
    """Return the list of entity dicts that pass the auto-approve predicate.

    Shared by the preview and apply endpoints so they can never diverge.
    Does NOT write anything to the database.
    """
    path = _results_path(run_id)
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    rows = (
        await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
        )
    ).scalars().all()

    ai_verdict_map: dict[str, str] = {}
    for r in rows:
        if r.ai_verdict:
            eid = _entity_id(
                control_number=r.control_number, source=r.source,
                text=r.text, start=r.start, end=r.end,
            )
            ai_verdict_map[eid] = str(r.ai_verdict.get("overall") or "").lower()

    eligible: list[dict] = []
    for ent in _flatten_records(records):
        if payload.sources and ent["source"] not in payload.sources:
            continue
        if payload.types and ent.get("type") not in payload.types:
            continue
        mconf = float(ent.get("model_confidence") or 0.0)
        if mconf < payload.min_confidence:
            continue
        verdict = ai_verdict_map.get(ent["id"], "")
        if payload.require_ai_pass and verdict != "pass":
            continue
        if payload.respect_ai_fail and verdict == "fail":
            continue
        if payload.not_roles and ent.get("role") in payload.not_roles:
            continue
        eligible.append(ent)
    return eligible


@router.post("/runs/{run_id}/extraction/entities/auto-approve/preview")
async def preview_auto_approve_entities(
    run_id: uuid.UUID,
    payload: AutoApprovePayload,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Dry-run of auto-approve: returns ``{matched, approved}`` without
    writing. The frontend uses this to show a live preview count as the
    curator adjusts the rule sliders.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)
    eligible = await _auto_approve_eligible(db, run_id, payload)
    return {"matched": len(eligible), "approved": 0}


@router.post("/runs/{run_id}/extraction/entities/auto-approve")
async def auto_approve_entities(
    run_id: uuid.UUID,
    payload: AutoApprovePayload,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Auto-approve every entity that passes the rule predicate.

    Returns ``{matched, approved}``.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    now = datetime.now(timezone.utc)

    eligible = await _auto_approve_eligible(db, run_id, payload)
    rows_to_insert = [
        {
            "run_id":           run_id,
            "control_number":   ent["control_number"],
            "source":           ent["source"],
            "text":             ent["text"],
            "start":            ent["start"],
            "end":              ent["end"],
            "type":             ent.get("type"),
            "role":             ent.get("role"),
            "confidence":       ent.get("confidence"),
            "model_confidence": ent.get("model_confidence"),
            "approved":         True,
            "approved_by":      auth.user.id,
            "approved_at":      now,
        }
        for ent in eligible
    ]

    if rows_to_insert:
        stmt = pg_insert(ExtractionApproval).values(rows_to_insert)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_extraction_approval_key",
            set_={
                "approved":     stmt.excluded.approved,
                "approved_by":  stmt.excluded.approved_by,
                "approved_at":  stmt.excluded.approved_at,
            },
        ).returning(ExtractionApproval)
        touched = (await db.execute(stmt)).scalars().all()
        for row in touched:
            await _emit_extraction_event(
                db,
                project_id=run.project_id,
                row=row,
                actor_id=auth.user.id,
                message="extraction auto-approve",
            )
        await db.commit()

    return {"matched": len(eligible), "approved": len(rows_to_insert)}
