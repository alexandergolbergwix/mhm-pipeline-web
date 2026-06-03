"""Section-level export endpoints.

One GET per pipeline section, each returning a StreamingResponse with
Content-Disposition: attachment.  All endpoints gate on the run's
project membership via ``_lookup_run_with_access`` (viewer OK for
reads).

URL surface::

    GET /runs/{run_id}/extraction/export?format=json|csv&approved_only=true|false
    GET /runs/{run_id}/authority/export?format=json|csv&approved_only=true|false
    GET /runs/{run_id}/rdf/export?format=ttl|nt
    GET /runs/{run_id}/wikibase/export?format=json|csv|ttl
    GET /runs/{run_id}/wikidata-studio/export?format=json|csv|ttl

The existing endpoints (``rdf/download.ttl``, ``wikidata-studio/
quickstatements.txt``) are kept as-is — this module adds new routes
alongside them.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.export.formatters import (
    csv_stream,
    items_to_rdf_graph,
    json_stream,
    nt_stream,
    ttl_stream,
)
from app.models.event import ENTITY_TYPE_WIKIBASE_ITEM, ProjectEvent
from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, Run, RunRecord
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline import wikidata_studio as ws_pipeline
from app.pipeline.rdf_build import rdf_output_path_for_run
from app.routers.runs import _lookup_run_with_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["section-export"])

_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slugify(name: str) -> str:
    return (_SLUG_RE.sub("-", name.strip()).strip("-") or "run")[:64]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _attachment(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


# ── Extraction ────────────────────────────────────────────────────────

_EXTRACTION_JSON_FIELDS = [
    "id", "control_number", "source", "text", "start", "end",
    "type", "role", "confidence", "model_confidence",
    "override_text", "override_type", "override_role",
    "approved", "approved_at",
    "ai_verdict_overall", "ai_verdict_reasoning",
    "created_at", "updated_at",
]

_EXTRACTION_CSV_FIELDS = _EXTRACTION_JSON_FIELDS


def _extraction_row(r: ExtractionApproval) -> dict[str, Any]:
    verdict = r.ai_verdict or {}
    return {
        "id": str(r.id),
        "control_number": r.control_number,
        "source": r.source,
        "text": r.text,
        "start": r.start,
        "end": r.end,
        "type": r.type,
        "role": r.role,
        "confidence": r.confidence,
        "model_confidence": r.model_confidence,
        "override_text": r.override_text,
        "override_type": r.override_type,
        "override_role": r.override_role,
        "approved": r.approved,
        "approved_at": r.approved_at,
        "ai_verdict_overall": verdict.get("overall"),
        "ai_verdict_reasoning": verdict.get("reasoning"),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


@router.get("/{run_id}/extraction/export", response_model=None)
async def export_extraction(
    run_id: uuid.UUID,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    approved_only: bool = Query(default=False),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Download extraction entities as JSON or CSV."""
    run = await _lookup_run_with_access(db, run_id, auth)
    stmt = (
        select(ExtractionApproval)
        .where(ExtractionApproval.run_id == run_id)
        .order_by(asc(ExtractionApproval.control_number), asc(ExtractionApproval.start))
    )
    if approved_only:
        stmt = stmt.where(ExtractionApproval.approved.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    data = [_extraction_row(r) for r in rows]

    suffix = "approved" if approved_only else "all"
    if format == "csv":
        filename = f"run-{run_id}-extraction-{suffix}.csv"
        return StreamingResponse(
            csv_stream(data, _EXTRACTION_CSV_FIELDS),
            media_type="text/csv",
            headers=_attachment(filename),
        )
    filename = f"run-{run_id}-extraction-{suffix}.json"
    return StreamingResponse(
        json_stream({"run_id": str(run_id), "approved_only": approved_only, "entities": data}),
        media_type="application/json",
        headers=_attachment(filename),
    )


# ── Authority ─────────────────────────────────────────────────────────

_AUTHORITY_CSV_FIELDS = [
    "id", "control_number", "entity_text", "entity_kind", "role",
    "matched_name", "mazal_id", "viaf_id", "wikidata_qid",
    "confidence", "source",
    "kima_id", "preferred_name_lat", "preferred_name_heb", "cluster_ids",
    "approved", "approved_at", "created_at",
]


def _authority_row(r: AuthorityMatch) -> dict[str, Any]:
    payload = r.payload or {}
    return {
        "id": str(r.id),
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
        "kima_id": payload.get("kima_id") or payload.get("kima_geonames") or "",
        "preferred_name_lat": payload.get("preferred_name_lat", ""),
        "preferred_name_heb": payload.get("preferred_name_heb", ""),
        "cluster_ids": payload.get("cluster_ids") or {},
        "approved": r.approved,
        "approved_at": r.approved_at,
        "created_at": r.created_at,
    }


@router.get("/{run_id}/authority/export", response_model=None)
async def export_authority(
    run_id: uuid.UUID,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    approved_only: bool = Query(default=False),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Download authority matches as JSON or CSV."""
    await _lookup_run_with_access(db, run_id, auth)
    stmt = (
        select(AuthorityMatch)
        .where(AuthorityMatch.run_id == run_id)
        .order_by(asc(AuthorityMatch.control_number))
    )
    if approved_only:
        stmt = stmt.where(AuthorityMatch.approved.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    data = [_authority_row(r) for r in rows]

    suffix = "approved" if approved_only else "all"
    if format == "csv":
        filename = f"run-{run_id}-authority-{suffix}.csv"
        return StreamingResponse(
            csv_stream(data, _AUTHORITY_CSV_FIELDS),
            media_type="text/csv",
            headers=_attachment(filename),
        )
    filename = f"run-{run_id}-authority-{suffix}.json"
    return StreamingResponse(
        json_stream({"run_id": str(run_id), "approved_only": approved_only, "matches": data}),
        media_type="application/json",
        headers=_attachment(filename),
    )


# ── RDF Graph ─────────────────────────────────────────────────────────


@router.get("/{run_id}/rdf/export", response_model=None)
async def export_rdf(
    run_id: uuid.UUID,
    format: str = Query(default="ttl", pattern="^(ttl|nt)$"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Export the built RDF graph as Turtle or N-Triples."""
    await _lookup_run_with_access(db, run_id, auth)
    ttl_path = rdf_output_path_for_run(str(run_id))
    if not ttl_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RDF graph not built yet — run POST /rdf/build first.",
        )

    if format == "ttl":
        filename = f"run-{run_id}-manuscripts.ttl"
        # The TTL file already exists on disk — stream it directly
        # without parsing to avoid rdflib overhead.
        async def _read_ttl() -> AsyncIterator[bytes]:
            chunk = 64 * 1024
            content = await asyncio.to_thread(ttl_path.read_bytes)
            for i in range(0, len(content), chunk):
                yield content[i: i + chunk]

        return StreamingResponse(
            _read_ttl(),
            media_type="text/turtle",
            headers=_attachment(filename),
        )

    # N-Triples: parse the TTL then re-serialise
    filename = f"run-{run_id}-manuscripts.nt"

    async def _nt_gen() -> AsyncIterator[bytes]:
        import rdflib  # noqa: PLC0415

        graph: rdflib.Graph = await asyncio.to_thread(
            lambda: rdflib.Graph().parse(str(ttl_path), format="turtle"),
        )
        async for chunk in nt_stream(graph):
            yield chunk

    return StreamingResponse(
        _nt_gen(),
        media_type="application/n-triples",
        headers=_attachment(filename),
    )


# ── Wikibase (HMO Studio) ─────────────────────────────────────────────

_WIKIBASE_CSV_FIELDS = [
    "entity_id", "label_en", "label_he", "description_en",
    "instance_of_qid", "manuscript_cn", "claims_count", "rev_no",
]

_WIKIBASE_BASE_URI = "https://mhm-hmo.wikibase.cloud/entity/"


def _fold_wikibase_events(events: list[ProjectEvent]) -> list[dict[str, Any]]:
    """Reduce a newest-first list to one record per entity_id."""
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
        })
    return out


def _wikibase_csv_row(item: dict[str, Any]) -> dict[str, Any]:
    state = item.get("state") or {}
    labels = state.get("labels") or {}
    descs = state.get("descriptions") or {}
    claims = state.get("claims") or state.get("statements") or []
    # Detect P31 (instance of) for first claim
    p31 = next(
        (c.get("value") or "" for c in claims if (c.get("property") or "").lstrip("P") == "31"),
        "",
    )
    return {
        "entity_id": item.get("entity_id", ""),
        "label_en": labels.get("en", ""),
        "label_he": labels.get("he", ""),
        "description_en": descs.get("en", ""),
        "instance_of_qid": p31,
        "manuscript_cn": state.get("manuscript_cn") or state.get("control_number") or "",
        "claims_count": len(claims),
        "rev_no": item.get("rev_no", 0),
    }


@router.get("/{run_id}/wikibase/export", response_model=None)
async def export_wikibase(
    run_id: uuid.UUID,
    format: str = Query(default="json", pattern="^(json|csv|ttl)$"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Export Wikibase items (from project_events) as JSON, CSV, or TTL."""
    run = await _lookup_run_with_access(db, run_id, auth)
    events = (
        await db.execute(
            select(ProjectEvent)
            .where(
                ProjectEvent.project_id == run.project_id,
                ProjectEvent.entity_type == ENTITY_TYPE_WIKIBASE_ITEM,
            )
            .order_by(desc(ProjectEvent.created_at))
        )
    ).scalars().all()
    items = _fold_wikibase_events(events)

    if format == "json":
        filename = f"run-{run_id}-wikibase.json"
        return StreamingResponse(
            json_stream({"run_id": str(run_id), "items": items}),
            media_type="application/json",
            headers=_attachment(filename),
        )

    if format == "csv":
        rows = [_wikibase_csv_row(item) for item in items]
        filename = f"run-{run_id}-wikibase.csv"
        return StreamingResponse(
            csv_stream(rows, _WIKIBASE_CSV_FIELDS),
            media_type="text/csv",
            headers=_attachment(filename),
        )

    # TTL
    filename = f"run-{run_id}-wikibase.ttl"
    # Build items list from state dicts for the RDF converter
    rdf_items = [
        {**(item.get("state") or {}), "entity_id": item["entity_id"]}
        for item in items
    ]

    async def _ttl_gen() -> AsyncIterator[bytes]:
        graph = await asyncio.to_thread(items_to_rdf_graph, rdf_items, _WIKIBASE_BASE_URI)
        async for chunk in ttl_stream(graph):
            yield chunk

    return StreamingResponse(
        _ttl_gen(),
        media_type="text/turtle",
        headers=_attachment(filename),
    )


# ── Wikidata Studio ───────────────────────────────────────────────────

_WIKIDATA_CSV_FIELDS = [
    "local_id", "qid", "label_en", "label_he", "description_en",
    "instance_of_qid", "manuscript_cn", "claims_count",
]

_WIKIDATA_BASE_URI = "http://www.wikidata.org/entity/"


def _wikidata_csv_row(item: dict[str, Any]) -> dict[str, Any]:
    labels = item.get("labels") or {}
    descs = item.get("descriptions") or {}
    claims = item.get("claims") or item.get("statements") or []
    p31 = next(
        (c.get("value") or "" for c in claims if (c.get("property") or "").lstrip("P") == "31"),
        "",
    )
    return {
        "local_id": item.get("id") or item.get("local_id") or "",
        "qid": item.get("qid") or "",
        "label_en": labels.get("en", ""),
        "label_he": labels.get("he", ""),
        "description_en": descs.get("en", ""),
        "instance_of_qid": p31,
        "manuscript_cn": item.get("manuscript_cn") or item.get("control_number") or "",
        "claims_count": len(claims),
    }


async def _get_wikidata_items(
    db: AsyncSession,
    run_id: uuid.UUID,
    approved_only: bool = True,
) -> list[dict[str, Any]]:
    """Return cached build items, rebuilding from scratch on miss."""
    # Try cache first
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    all_matches = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()
    entity_rows = (
        await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
        )
    ).scalars().all()
    override_rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()

    fingerprint = ws_pipeline.compute_build_fingerprint(
        records, list(all_matches), entity_rows, override_rows, approved_only,
    )
    cached = (
        await db.execute(
            select(WikidataStudioCache).where(
                WikidataStudioCache.run_id == run_id,
                WikidataStudioCache.approved_only == approved_only,
            )
        )
    ).scalar_one_or_none()

    if cached is not None and cached.input_fingerprint == fingerprint:
        return cached.result_items

    # Cache miss — full build
    matches = [m for m in all_matches if m.approved] if approved_only else list(all_matches)
    marc_records = [dict(r.marc) for r in records]
    approved_matches = [
        {
            "id": str(m.id),
            "control_number": m.control_number,
            "entity_text": m.entity_text,
            "role": m.role,
            "matched_name": m.matched_name,
            "mazal_id": m.mazal_id,
            "viaf_id": m.viaf_id,
            "wikidata_qid": m.wikidata_qid,
            "confidence": m.confidence,
            "source": m.source,
            "payload": m.payload or {},
        }
        for m in matches
    ]
    entities_by_cn: dict[str, list[dict[str, Any]]] = {}
    for e in entity_rows:
        if approved_only and not e.approved:
            continue
        entities_by_cn.setdefault(e.control_number, []).append({
            "text": e.override_text or e.text,
            "type": e.override_type or e.type,
            "role": e.override_role or e.role,
            "source": e.source,
        })
    result = await ws_pipeline.build_items_for_run(
        marc_records=marc_records,
        approved_matches=approved_matches,
        entities_by_cn=entities_by_cn,
        overrides={r.local_id: {
            "labels": r.labels,
            "descriptions": r.descriptions,
            "aliases": r.aliases,
            "add_statements": r.add_statements,
            "remove_statements": r.remove_statements,
            "statement_edits": r.statement_edits,
        } for r in override_rows},
    )
    return result.get("items", [])


@router.get("/{run_id}/wikidata-studio/export", response_model=None)
async def export_wikidata_studio(
    run_id: uuid.UUID,
    format: str = Query(default="json", pattern="^(json|csv|ttl)$"),
    approved_only: bool = Query(default=True),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Export Wikidata Studio items as JSON, CSV, or TTL."""
    await _lookup_run_with_access(db, run_id, auth)
    items = await _get_wikidata_items(db, run_id, approved_only)

    suffix = "approved" if approved_only else "all"

    if format == "json":
        filename = f"run-{run_id}-wikidata-{suffix}.json"
        return StreamingResponse(
            json_stream({"run_id": str(run_id), "approved_only": approved_only, "items": items}),
            media_type="application/json",
            headers=_attachment(filename),
        )

    if format == "csv":
        rows = [_wikidata_csv_row(item) for item in items]
        filename = f"run-{run_id}-wikidata-{suffix}.csv"
        return StreamingResponse(
            csv_stream(rows, _WIKIDATA_CSV_FIELDS),
            media_type="text/csv",
            headers=_attachment(filename),
        )

    # TTL
    filename = f"run-{run_id}-wikidata-{suffix}.ttl"

    async def _ttl_gen() -> AsyncIterator[bytes]:
        graph = await asyncio.to_thread(items_to_rdf_graph, items, _WIKIDATA_BASE_URI)
        async for chunk in ttl_stream(graph):
            yield chunk

    return StreamingResponse(
        _ttl_gen(),
        media_type="text/turtle",
        headers=_attachment(filename),
    )
