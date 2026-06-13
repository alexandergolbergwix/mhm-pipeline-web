"""Provenance-chain timeline endpoint (Feature 4 — Phase B).

GET /api/projects/{project_id}/research/provenance?ms=<manuscript_uri>
    [&overlay=lifespans]

Returns ordered provenance events for a single manuscript:
  {ms, ms_label, events: [{type, label, uri, year, year_earliest, year_latest, place}]}

When overlay=lifespans, ownership events are enriched with owner_birth /
owner_death from the authority_matches payload.
"""
from __future__ import annotations

import uuid
from typing import Any

import rdflib
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline.inference_cache import cache_lookup_or_call
from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.research_geo_enrich import owner_place
from app.pipeline.corpus_movement import (
    _extract_corpus_item,
    build_corpus_facets,
    build_corpus_movement,
)
from app.pipeline.research_provenance_map import (
    build_provenance_map,
    data_fingerprint,
    is_owner_role,
)
from app.pipeline.research_queries import query_provenance
from app.routers.linked_data_explorer import (
    _load_graph_or_404,
    _require_viewer,
    _run_ids_for_project,
)

router = APIRouter(tags=["research"])

_HM = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"
_RDFS_LABEL = str(rdflib.RDFS.label)


class ProvenanceEvent(BaseModel):
    type: str
    label: str
    uri: str | None
    year: int | None
    year_earliest: int | None
    year_latest: int | None
    place: str | None
    owner_birth: int | None = None
    owner_death: int | None = None


class ProvenanceTimelineOut(BaseModel):
    ms: str
    ms_label: str | None
    events: list[ProvenanceEvent]


def _ms_label(graph: rdflib.Graph, ms_uri: str) -> str | None:
    """Return the first rdfs:label for the manuscript URI."""
    for obj in graph.objects(rdflib.URIRef(ms_uri), rdflib.RDFS.label):
        return str(obj)
    return None


async def _lifespan_map(
    project_id: uuid.UUID,
    person_uris: list[str],
    graph: rdflib.Graph,
    db: AsyncSession,
) -> dict[str, dict[str, int]]:
    """For each person URI, look up birth/death from authority_matches.

    Matches by rdfs:label of the person URI against matched_name.
    Returns {uri: {birth, death}} (keys present only if year found).
    """
    if not person_uris:
        return {}

    # Build label → uri reverse map
    label_to_uri: dict[str, str] = {}
    for uri in person_uris:
        for obj in graph.objects(rdflib.URIRef(uri), rdflib.RDFS.label):
            label_to_uri[str(obj).lower()] = uri

    if not label_to_uri:
        return {}

    run_rows = await db.execute(select(Run.id).where(Run.project_id == project_id))
    run_ids = list(run_rows.scalars().all())

    result: dict[str, dict[str, int]] = {}
    for run_id in run_ids:
        rows = await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
        for am in rows.scalars().all():
            mn = (am.matched_name or "").lower()
            et = (am.entity_text or "").lower()
            matched_uri = label_to_uri.get(mn) or label_to_uri.get(et)
            if matched_uri is None:
                continue
            payload: dict[str, Any] = am.payload or {}
            birth = payload.get("birth_year")
            death = payload.get("death_year")
            if birth is not None or death is not None:
                entry: dict[str, int] = {}
                if birth is not None:
                    entry["birth"] = int(birth)
                if death is not None:
                    entry["death"] = int(death)
                if matched_uri not in result or am.confidence == "high":
                    result[matched_uri] = entry

    return result


@router.get(
    "/projects/{project_id}/research/provenance",
    response_model=ProvenanceTimelineOut,
)
async def get_provenance_timeline(
    project_id: uuid.UUID,
    ms: str = Query(..., description="Manuscript URI to look up."),
    overlay: str | None = Query(None, description="Pass 'lifespans' to add owner birth/death dates."),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ProvenanceTimelineOut:
    """Return ordered provenance events for a manuscript."""
    graph = await _load_graph_or_404(project_id, auth, db)

    # Check the MS URI exists in the graph
    ms_ref = rdflib.URIRef(ms)
    if (ms_ref, None, None) not in graph:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript URI not found in project graph.",
        )

    label = _ms_label(graph, ms)
    raw_events = query_provenance(graph, ms)

    # Optional lifespan overlay
    lifespan_data: dict[str, dict[str, int]] = {}
    if overlay == "lifespans":
        person_uris = [e["uri"] for e in raw_events if e["type"] == "ownership" and e["uri"]]
        lifespan_data = await _lifespan_map(project_id, person_uris, graph, db)

    events: list[ProvenanceEvent] = []
    for ev in raw_events:
        person_uri = ev["uri"] if ev["type"] == "ownership" else None
        lifespan = lifespan_data.get(person_uri or "") or {} if person_uri else {}
        events.append(
            ProvenanceEvent(
                type=ev["type"],
                label=ev["label"],
                uri=ev["uri"],
                year=ev["year"],
                year_earliest=ev["year_earliest"],
                year_latest=ev["year_latest"],
                place=ev["place"],
                owner_birth=lifespan.get("birth"),
                owner_death=lifespan.get("death"),
            )
        )

    return ProvenanceTimelineOut(ms=ms, ms_label=label, events=events)


# ── Provenance movement map (geo + time) ───────────────────────────────


class ManuscriptPick(BaseModel):
    control_number: str
    label: str | None
    production_year: int | None


class MapStop(BaseModel):
    kind: str
    label: str
    uri: str | None = None
    lat: float | None
    lon: float | None
    year: int | None = None
    year_earliest: int | None = None
    year_latest: int | None = None
    birth_year: int | None = None
    death_year: int | None = None
    certain: bool = False
    inferred_geo: bool = False
    geo_source: str | None = None
    geo_source_label: str | None = None
    approved: bool | None = None
    is_present: bool = False
    time: float | None = None
    has_point: bool = False


class MapEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: int = Field(serialization_alias="from")
    to: int
    inferred: bool
    directed: bool


class ProvenanceMapOut(BaseModel):
    control_number: str
    ms_label: str | None
    stops: list[MapStop]
    edges: list[MapEdge]
    dropped: list[dict[str, Any]]


async def _project_records_and_matches(
    project_id: uuid.UUID, db: AsyncSession,
) -> tuple[list[RunRecord], dict[str, list[AuthorityMatch]]]:
    """All RunRecords + AuthorityMatches (grouped by control_number) for a project."""
    run_ids = await _run_ids_for_project(project_id, db)
    if not run_ids:
        return [], {}
    run_uuids = [uuid.UUID(r) for r in run_ids]
    rec_rows = (
        await db.execute(select(RunRecord).where(RunRecord.run_id.in_(run_uuids)))
    ).scalars().all()
    match_rows = (
        await db.execute(select(AuthorityMatch).where(AuthorityMatch.run_id.in_(run_uuids)))
    ).scalars().all()
    by_cn: dict[str, list[AuthorityMatch]] = {}
    for m in match_rows:
        by_cn.setdefault(m.control_number, []).append(m)
    return list(rec_rows), by_cn


def _project_fingerprint(records: list[RunRecord], by_cn: dict[str, list[AuthorityMatch]]) -> str:
    """Stable hash over the record set + each match's mutable state (approval / qid).

    Changes whenever a record is added, a match re-runs, or a curator approves —
    so the Redis-cached map can never serve stale data after an upstream fix.
    """
    parts: list[str] = [f"records:{len(records)}"]
    for cn in sorted(by_cn):
        for m in sorted(by_cn[cn], key=lambda x: str(x.id)):
            parts.append(
                f"{cn}:{m.id}:{m.wikidata_qid}:{m.confidence}:{int(bool(m.approved))}"
            )
    return data_fingerprint(parts)


@router.get(
    "/projects/{project_id}/research/manuscripts",
    response_model=list[ManuscriptPick],
)
async def list_manuscripts(
    project_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[ManuscriptPick]:
    """Manuscripts in the project for the movement-map picker (Redis-cached)."""
    await _require_viewer(project_id, auth, db)
    records, by_cn = await _project_records_and_matches(project_id, db)
    fp = _project_fingerprint(records, by_cn)

    async def _fetch() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rec in records:
            prepared = prepare_record_for_pipeline(dict(rec.marc or {}))
            dates = prepared.get("dates") or {}
            year = dates.get("year") if isinstance(dates, dict) else None
            try:
                year = int(year) if year is not None else None
            except (TypeError, ValueError):
                year = None
            out.append({
                "control_number": rec.control_number,
                "label": str(prepared.get("title") or "").strip() or rec.control_number,
                "production_year": year,
            })
        out.sort(
            key=lambda r: (r["production_year"] is None, r["production_year"] or 0, r["label"]),
        )
        return out

    rows = await cache_lookup_or_call(
        db, kind="research.manuscripts",
        query_summary={"project": str(project_id), "fp": fp},
        fetch=_fetch, user_id=auth.user.id,
    )
    return [ManuscriptPick(**r) for r in rows]


@router.get(
    "/projects/{project_id}/research/provenance-map",
    response_model=ProvenanceMapOut,
)
async def get_provenance_map(
    project_id: uuid.UUID,
    cn: str = Query(..., description="Manuscript control number."),
    include_unapproved: bool = Query(
        False, description="Preview unapproved owner matches (flagged). Default false.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ProvenanceMapOut:
    """Geo + time-ordered provenance movement map for one manuscript."""
    await _require_viewer(project_id, auth, db)
    records, by_cn = await _project_records_and_matches(project_id, db)

    rec = next((r for r in records if r.control_number == cn), None)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found in this project.",
        )

    matches = by_cn.get(cn, [])
    prepared = prepare_record_for_pipeline(dict(rec.marc or {}))
    ms_label = str(prepared.get("title") or "").strip() or cn

    # Resolve owner coordinates (cached) for owners that pass the cheap gates.
    owner_qids: set[str] = set()
    for m in matches:
        if m.entity_kind == "person" and is_owner_role(m.role or ""):
            if not include_unapproved and not m.approved:
                continue
            if (m.confidence or "").lower() not in ("high", "medium"):
                continue
            qid = (m.wikidata_qid or "").strip()
            if qid:
                owner_qids.add(qid)

    owner_places: dict[str, dict[str, Any] | None] = {}
    for qid in owner_qids:
        owner_places[qid] = await owner_place(qid, db_session=db, user_id=auth.user.id)

    match_dicts = [
        {
            "entity_text": m.entity_text,
            "entity_kind": m.entity_kind,
            "role": m.role,
            "matched_name": m.matched_name,
            "wikidata_qid": m.wikidata_qid,
            "confidence": m.confidence,
            "approved": bool(m.approved),
            "payload": m.payload or {},
        }
        for m in matches
    ]

    result = build_provenance_map(
        control_number=cn,
        ms_label=ms_label,
        record=prepared,
        matches=match_dicts,
        owner_places=owner_places,
        include_unapproved=include_unapproved,
    )

    stops = [MapStop(**s) for s in result["stops"]]
    edges = [
        MapEdge(from_=e["from"], to=e["to"], inferred=e["inferred"], directed=e["directed"])
        for e in result["edges"]
    ]
    return ProvenanceMapOut(
        control_number=cn, ms_label=result["ms_label"],
        stops=stops, edges=edges, dropped=result["dropped"],
    )


# ── Corpus Movement map (all manuscripts, filterable) ─────────────────


async def _build_corpus_items(
    project_id: uuid.UUID, db: AsyncSession,
) -> tuple[list[dict[str, Any]], str]:
    """Return (full corpus items list, project fingerprint) — uncached fetch."""
    records, by_cn = await _project_records_and_matches(project_id, db)
    fp = _project_fingerprint(records, by_cn)
    items: list[dict[str, Any]] = []
    for rec in records:
        prepared = prepare_record_for_pipeline(dict(rec.marc or {}))
        match_dicts = [
            {
                "entity_text": m.entity_text,
                "entity_kind": m.entity_kind,
                "role": m.role,
                "matched_name": m.matched_name,
                "wikidata_qid": m.wikidata_qid,
                "confidence": m.confidence,
                "approved": bool(m.approved),
                "payload": m.payload or {},
            }
            for m in by_cn.get(rec.control_number, [])
        ]
        items.append(_extract_corpus_item(prepared, match_dicts, rec.control_number))
    return items, fp


@router.get("/projects/{project_id}/research/movement/facets")
async def get_corpus_movement_facets(
    project_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Distinct filter-control values for the corpus movement map.

    Returns year_min, year_max, places[], genres[], owners[].
    Cached per project fingerprint.
    """
    await _require_viewer(project_id, auth, db)

    async def _fetch() -> dict[str, Any]:
        items, _ = await _build_corpus_items(project_id, db)
        return build_corpus_facets(items)

    records, by_cn = await _project_records_and_matches(project_id, db)
    fp = _project_fingerprint(records, by_cn)
    result = await cache_lookup_or_call(
        db,
        kind="research.movement_facets",
        query_summary={"project": str(project_id), "fp": fp},
        fetch=_fetch,
        user_id=auth.user.id,
    )
    return result or {}


@router.get("/projects/{project_id}/research/movement")
async def get_corpus_movement(
    project_id: uuid.UUID,
    from_year: int | None = None,
    to_year: int | None = None,
    place: str | None = None,
    genre: str | None = None,
    owner: str | None = None,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """All-manuscripts movement map with optional filters.

    Returns {manuscripts: [...], year_counts: [{year, count}]}.
    The full unfiltered corpus is cached; filters are applied in Python so the
    cache stays valid across different filter combinations.
    """
    await _require_viewer(project_id, auth, db)

    records, by_cn = await _project_records_and_matches(project_id, db)
    fp = _project_fingerprint(records, by_cn)

    async def _fetch_all() -> list[dict[str, Any]]:
        items, _ = await _build_corpus_items(project_id, db)
        return items

    all_items: list[dict[str, Any]] = await cache_lookup_or_call(
        db,
        kind="research.movement",
        query_summary={"project": str(project_id), "fp": fp},
        fetch=_fetch_all,
        user_id=auth.user.id,
    ) or []

    return build_corpus_movement(
        all_items,
        from_year=from_year,
        to_year=to_year,
        place=place,
        genre=genre,
        owner=owner,
    )
