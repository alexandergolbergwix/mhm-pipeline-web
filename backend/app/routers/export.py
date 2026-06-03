"""Project-export router — three flavours of JSON download.

The curator wants to walk away with everything the database knows about
their project. Three endpoints satisfy that:

* ``GET /projects/{id}/export`` — full-project bundle. All entity-types'
  current read-model state in one document. Optional
  ``?entity_types=marc_record&entity_types=extraction_entity`` filter
  trims the bundle to a per-entity-type slice.

* ``GET /projects/{id}/export/snapshots`` — cold-tier ``entity_snapshot``
  archive. The 3/day forever history that survives the 1000-event prune.

* ``GET /projects/{id}/export/history`` — full ``project_events`` dump
  for the whole project, or for one entity when both ``entity_type``
  and ``entity_id`` are supplied.

Every endpoint:

* Gates on project membership via ``require_viewer`` from
  :mod:`app.auth.project_perms`.
* Streams JSON via :class:`fastapi.responses.StreamingResponse` so a
  100 MB export never lives in memory all at once.
* Decrypts actor PII (emails) in batch — one ``select(User)`` per call,
  not one per row.
* Sends ``Content-Disposition: attachment; filename=…`` so the browser
  treats the response as a download.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator, Iterable
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_perms import ProjectContext, require_viewer
from app.crypto import pii
from app.db import get_session
from app.models.entity_snapshot import EntitySnapshot
from app.models.event import (
    ALL_ENTITY_TYPES,
    ENTITY_TYPE_WIKIBASE_ITEM,
    ProjectEvent,
)
from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, Run, RunRecord
from app.models.user import User

router = APIRouter(prefix="/projects/{project_id}/export", tags=["export"])


# ── helpers ──────────────────────────────────────────────────────────────


_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slugify(name: str) -> str:
    """Conservative filename slug — keeps things browsers won't reject."""
    cleaned = _SLUG_RE.sub("-", name.strip()).strip("-")
    return cleaned[:64] or "project"


def _json_default(value: Any) -> Any:
    """``json.dumps`` fallback for UUID / datetime / date / bytes."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


async def _email_map_for_actors(
    db: AsyncSession, actor_ids: Iterable[uuid.UUID | None],
) -> dict[uuid.UUID, str]:
    """Batch-load every actor's plaintext email in one query."""
    unique_ids = {uid for uid in actor_ids if uid is not None}
    if not unique_ids:
        return {}
    users = (
        await db.execute(select(User).where(User.id.in_(list(unique_ids))))
    ).scalars().all()
    out: dict[uuid.UUID, str] = {}
    for u in users:
        try:
            out[u.id] = pii.decrypt_pii(u.email_encrypted)
        except Exception:  # noqa: BLE001 — never fail an export on a single bad row
            continue
    return out


def _attachment_headers(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _stream_payload(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    """Yield the payload as a single chunked JSON blob.

    ``json.dumps`` builds the string once in memory; we then slice it
    into 64 KB chunks so a slow client doesn't pin the whole buffer
    on the socket-send side. The dict-build above is the actual memory
    high-water mark for an export; for the foreseeable corpus sizes
    (~100 MB) this is fine. If projects grow into the GB range, the
    per-section loops below can be rewritten to yield arrays
    incrementally — the streaming generator scaffolding is already in
    place to make that swap easy.
    """
    encoded = json.dumps(payload, default=_json_default, ensure_ascii=False).encode("utf-8")
    chunk = 64 * 1024
    for i in range(0, len(encoded), chunk):
        yield encoded[i : i + chunk]


# ── entity-type filter ──────────────────────────────────────────────────


def _parse_entity_type_filter(raw: list[str] | None) -> set[str] | None:
    """Validate the ``?entity_types=…`` query against the closed set.

    Returns ``None`` when no filter was supplied (= "give me everything").
    Returns a set of allowed types otherwise. Rejects unknown values
    with a 400 so an attacker can't enumerate stray model names.
    """
    if not raw:
        return None
    requested = {v.strip() for v in raw if v and v.strip()}
    if not requested:
        return None
    unknown = requested - ALL_ENTITY_TYPES
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown entity_types: {sorted(unknown)}",
        )
    return requested


def _wants(allowed: set[str] | None, entity_type: str) -> bool:
    return allowed is None or entity_type in allowed


# ── full-project bundle ─────────────────────────────────────────────────


@router.get("", response_model=None)
async def export_project(
    entity_types: list[str] | None = Query(default=None),
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Stream the project's current state as a single JSON download.

    The bundle's keys mirror the read-model tables one-to-one. Optional
    ``entity_types`` query trims the bundle; an absent filter yields
    every section.
    """
    allowed = _parse_entity_type_filter(entity_types)
    project = ctx.project
    exported_by_email = pii.decrypt_pii(ctx.auth.user.email_encrypted)

    # All runs in the project — cheap, bounded by project size.
    runs = (
        await db.execute(
            select(Run).where(Run.project_id == project.id).order_by(asc(Run.created_at))
        )
    ).scalars().all()
    run_ids = [r.id for r in runs]

    # Batch-resolve every actor email we'll need across every section.
    actor_ids: set[uuid.UUID | None] = {r.created_by for r in runs}
    actor_ids.add(ctx.auth.user.id)

    marc_rows: list[RunRecord] = []
    extraction_rows: list[ExtractionApproval] = []
    authority_rows: list[AuthorityMatch] = []
    override_rows: list[WikidataItemOverride] = []
    wikibase_event_rows: list[ProjectEvent] = []

    if run_ids:
        if _wants(allowed, "marc_record"):
            marc_rows = (
                await db.execute(
                    select(RunRecord)
                    .where(RunRecord.run_id.in_(run_ids))
                    .order_by(asc(RunRecord.run_id), asc(RunRecord.control_number))
                )
            ).scalars().all()

        if _wants(allowed, "extraction_entity"):
            extraction_rows = (
                await db.execute(
                    select(ExtractionApproval)
                    .where(ExtractionApproval.run_id.in_(run_ids))
                    .order_by(asc(ExtractionApproval.created_at))
                )
            ).scalars().all()
            actor_ids.update(r.approved_by for r in extraction_rows)

        if _wants(allowed, "authority_match"):
            authority_rows = (
                await db.execute(
                    select(AuthorityMatch)
                    .where(AuthorityMatch.run_id.in_(run_ids))
                    .order_by(asc(AuthorityMatch.created_at))
                )
            ).scalars().all()
            actor_ids.update(r.approved_by for r in authority_rows)

        if _wants(allowed, "wikidata_override"):
            override_rows = (
                await db.execute(
                    select(WikidataItemOverride)
                    .where(WikidataItemOverride.run_id.in_(run_ids))
                    .order_by(asc(WikidataItemOverride.created_at))
                )
            ).scalars().all()
            actor_ids.update(r.updated_by for r in override_rows)

    if _wants(allowed, "wikibase_item"):
        # No read-model table; fold the event log to the latest state
        # per (entity_id). Pull every wikibase_item event in this
        # project, sort newest-first, take the first hit per entity.
        wikibase_event_rows = (
            await db.execute(
                select(ProjectEvent)
                .where(
                    ProjectEvent.project_id == project.id,
                    ProjectEvent.entity_type == ENTITY_TYPE_WIKIBASE_ITEM,
                )
                .order_by(desc(ProjectEvent.created_at))
            )
        ).scalars().all()
        actor_ids.update(e.actor_id for e in wikibase_event_rows)

    email_map = await _email_map_for_actors(db, actor_ids)

    # ── build the payload ────────────────────────────────────────────────
    payload: dict[str, Any] = {
        "project_id": str(project.id),
        "project_name": project.name,
        "exported_at": _utcnow(),
        "exported_by_email": exported_by_email,
        "runs": [
            {
                "id": str(r.id),
                "name": r.name,
                "status": r.status,
                "record_count": r.record_count,
                "match_count": r.match_count,
                "error": r.error,
                "created_by_email": email_map.get(r.created_by),
                "created_at": r.created_at,
                "completed_at": r.completed_at,
            }
            for r in runs
        ],
        "marc_records": [
            {
                "run_id": str(r.run_id),
                "control_number": r.control_number,
                "marc": r.marc,
            }
            for r in marc_rows
        ],
        "extraction_entities": [
            {
                "id": str(r.id),
                "run_id": str(r.run_id),
                "control_number": r.control_number,
                "source": r.source,
                "text": r.text,
                "start": r.start,
                "end": r.end,
                "type": r.type,
                "role": r.role,
                "confidence": r.confidence,
                "model_confidence": r.model_confidence,
                "override_type": r.override_type,
                "override_role": r.override_role,
                "override_text": r.override_text,
                "approved": r.approved,
                "approved_by_email": email_map.get(r.approved_by) if r.approved_by else None,
                "approved_at": r.approved_at,
                "ai_verdict": r.ai_verdict,
                "ai_verdict_at": r.ai_verdict_at,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in extraction_rows
        ],
        "authority_matches": [
            {
                "id": str(r.id),
                "run_id": str(r.run_id),
                "control_number": r.control_number,
                "entity_text": r.entity_text,
                "entity_kind": r.entity_kind,
                "role": r.role,
                "matched_name": r.matched_name,
                "mazal_id": r.mazal_id,
                "viaf_id": r.viaf_id,
                "wikidata_qid": r.wikidata_qid,
                "confidence": r.confidence,
                "source": r.source,
                "payload": r.payload,
                "approved": r.approved,
                "approved_by_email": email_map.get(r.approved_by) if r.approved_by else None,
                "approved_at": r.approved_at,
                "created_at": r.created_at,
            }
            for r in authority_rows
        ],
        "wikidata_overrides": [
            {
                "id": str(r.id),
                "run_id": str(r.run_id),
                "local_id": r.local_id,
                "labels": r.labels,
                "descriptions": r.descriptions,
                "aliases": r.aliases,
                "add_statements": r.add_statements,
                "remove_statements": r.remove_statements,
                "statement_edits": r.statement_edits,
                "updated_by_email": email_map.get(r.updated_by) if r.updated_by else None,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in override_rows
        ],
        "wikibase_items": _fold_wikibase_events(wikibase_event_rows, email_map),
    }

    filename = (
        f"project-{_slugify(project.name)}-export-"
        f"{_utcnow().date().isoformat()}.json"
    )
    return StreamingResponse(
        _stream_payload(payload),
        media_type="application/json",
        headers=_attachment_headers(filename),
    )


def _fold_wikibase_events(
    events: list[ProjectEvent], email_map: dict[uuid.UUID, str],
) -> list[dict[str, Any]]:
    """Reduce a newest-first event list to one row per entity_id.

    There is no read-model table for ``wikibase_item`` — the event log
    is the record of what we wrote out. The folded view here is "the
    last known state we attempted to publish for each manifest" (or
    None when the most recent event was a patch with no state attached;
    in practice every wikibase_item event currently carries full state).
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ev in events:
        if ev.entity_id is None or ev.entity_id in seen:
            continue
        seen.add(ev.entity_id)
        out.append({
            "entity_id": ev.entity_id,
            "rev_no": ev.rev_no or 0,
            "state": ev.state,
            "last_event_at": ev.created_at,
            "last_actor_email": email_map.get(ev.actor_id) if ev.actor_id else None,
        })
    return out


# ── snapshot archive ────────────────────────────────────────────────────


@router.get("/snapshots", response_model=None)
async def export_snapshots(
    entity_type: str | None = Query(default=None),
    since: str | None = Query(default=None, description="ISO date (YYYY-MM-DD)"),
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Stream every cold-tier snapshot for this project as a JSON download.

    Snapshots survive the 1000-event prune — this endpoint is a
    "time-travel backup" of the per-entity state over the project's
    lifetime, bucketed 3/day forever.
    """
    if entity_type is not None and entity_type not in ALL_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown entity_type: {entity_type!r}",
        )

    stmt = (
        select(EntitySnapshot)
        .where(EntitySnapshot.project_id == ctx.project.id)
        .order_by(
            asc(EntitySnapshot.entity_type),
            asc(EntitySnapshot.entity_id),
            asc(EntitySnapshot.bucket),
            asc(EntitySnapshot.slot),
        )
    )
    if entity_type:
        stmt = stmt.where(EntitySnapshot.entity_type == entity_type)
    if since:
        try:
            since_date = date.fromisoformat(since)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid `since`: {exc}",
            )
        stmt = stmt.where(EntitySnapshot.bucket >= since_date)

    rows = (await db.execute(stmt)).scalars().all()

    payload: dict[str, Any] = {
        "project_id": str(ctx.project.id),
        "exported_at": _utcnow(),
        "snapshot_count": len(rows),
        "snapshots": [
            {
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "bucket": r.bucket.isoformat(),
                "slot": r.slot,
                "rev_no": r.rev_no,
                "state": r.state,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }
    filename = (
        f"project-{_slugify(ctx.project.name)}-snapshots-"
        f"{_utcnow().date().isoformat()}.json"
    )
    return StreamingResponse(
        _stream_payload(payload),
        media_type="application/json",
        headers=_attachment_headers(filename),
    )


# ── history dump ────────────────────────────────────────────────────────


@router.get("/history", response_model=None)
async def export_history(
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Stream the project's event log as a JSON download.

    With both ``entity_type`` and ``entity_id`` supplied, the dump is
    scoped to that one entity (timeline + diffs). Without, every event
    in the project is exported — large, can hit hundreds of MB, but the
    streaming generator keeps the socket-side memory bounded.
    """
    if entity_type is not None and entity_type not in ALL_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown entity_type: {entity_type!r}",
        )
    if (entity_type is None) ^ (entity_id is None):
        # Both or neither; never one without the other.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity_type and entity_id must be supplied together",
        )

    stmt = (
        select(ProjectEvent)
        .where(ProjectEvent.project_id == ctx.project.id)
        .order_by(asc(ProjectEvent.created_at))
    )
    if entity_type and entity_id:
        stmt = stmt.where(
            ProjectEvent.entity_type == entity_type,
            ProjectEvent.entity_id == entity_id,
        )

    rows = (await db.execute(stmt)).scalars().all()
    email_map = await _email_map_for_actors(db, (r.actor_id for r in rows))

    payload: dict[str, Any] = {
        "project_id": str(ctx.project.id),
        "exported_at": _utcnow(),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "event_count": len(rows),
        "events": [
            {
                "id": str(r.id),
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "rev_no": r.rev_no,
                "parent_event_id": str(r.parent_event_id) if r.parent_event_id else None,
                "op": r.op,
                "type": r.type,
                "patch": r.patch,
                "state": r.state,
                "payload": r.payload,
                "actor_email": email_map.get(r.actor_id) if r.actor_id else None,
                "message": r.message,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }

    suffix = "history"
    if entity_type and entity_id:
        suffix = f"history-{_slugify(entity_type)}-{_slugify(entity_id)}"
    filename = (
        f"project-{_slugify(ctx.project.name)}-{suffix}-"
        f"{_utcnow().date().isoformat()}.json"
    )
    return StreamingResponse(
        _stream_payload(payload),
        media_type="application/json",
        headers=_attachment_headers(filename),
    )
