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
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.run import AuthorityMatch, Run
from app.pipeline.research_queries import query_provenance
from app.routers.linked_data_explorer import _load_graph_or_404

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
