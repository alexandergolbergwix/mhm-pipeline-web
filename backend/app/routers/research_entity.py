"""Entity detail endpoint (Feature 3 — Phase B).

GET /api/projects/{project_id}/research/entity?uri=<entity_uri>

Resolves any RDF URI (person, place, work, or manuscript) to a structured
summary: label, type, roles across manuscripts, geographic coordinates,
and linked authority identifiers (VIAF, Wikidata, ...).

Identifiers and biographical dates are joined from the ``authority_matches``
table by matching the entity's rdfs:label against ``matched_name``.
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
from app.routers.linked_data_explorer import _load_graph_or_404

router = APIRouter(tags=["research"])

# ── namespace constants ────────────────────────────────────────────────────

_HM = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"
_LRMOO = "http://iflastandards.info/ns/lrm/lrmoo/"
_WGS84 = "http://www.w3.org/2003/01/geo/wgs84_pos#"
_RDFS_LABEL = str(rdflib.RDFS.label)
_RDF_TYPE = str(rdflib.RDF.type)

# predicate → role name for person-bearing predicates
_PERSON_ROLE_PREDICATES: dict[str, str] = {
    _HM + "has_author": "author",
    _HM + "has_scribe": "scribe",
    _HM + "has_owner": "owner",
    _HM + "has_illuminator": "illuminator",
    _HM + "has_translator": "translator",
    _HM + "has_commentator": "commentator",
}

# predicate → role name for place-bearing predicates
_PLACE_ROLE_PREDICATES: dict[str, str] = {
    _HM + "has_production_place": "production",
    _HM + "mentions_place": "mentioned",
}

# rdf:type value → friendly type string
_TYPE_MAP: dict[str, str] = {
    _HM + "Manuscript_Object": "manuscript",
    _HM + "Person": "person",
    _HM + "Place": "place",
    _LRMOO + "F4_Manifestation_Singleton": "work",
    _LRMOO + "F2_Expression": "work",
    _LRMOO + "F1_Work": "work",
    _HM + "Work": "work",
}

# ── response schemas ───────────────────────────────────────────────────────


class ManuscriptRef(BaseModel):
    uri: str
    label: str | None
    role: str


class EntityDetailOut(BaseModel):
    uri: str
    label: str | None
    type: str
    roles: list[str]
    manuscripts: list[ManuscriptRef]
    dates: dict[str, int] | None
    geo: dict[str, float] | None
    identifiers: dict[str, str]


# ── helpers ────────────────────────────────────────────────────────────────


def _label(graph: rdflib.Graph, uri: str) -> str | None:
    """Return the first rdfs:label for a URI, preferring Hebrew."""
    he_label: str | None = None
    any_label: str | None = None
    for obj in graph.objects(rdflib.URIRef(uri), rdflib.RDFS.label):
        s = str(obj)
        lang = getattr(obj, "language", None)
        if lang and lang.startswith("he"):
            he_label = s
        else:
            any_label = s
    return he_label or any_label


def _entity_type(graph: rdflib.Graph, uri: str) -> str:
    """Determine the friendly type of an entity from its rdf:type triples."""
    for obj in graph.objects(rdflib.URIRef(uri), rdflib.RDF.type):
        t = _TYPE_MAP.get(str(obj))
        if t:
            return t
    return "entity"


def _roles_and_manuscripts(
    graph: rdflib.Graph, uri: str
) -> tuple[list[str], list[ManuscriptRef]]:
    """Find all roles and associated manuscript URIs for the given entity.

    Searches for triples of the form  <manuscript> <predicate> <uri>
    where the predicate maps to a known role.
    """
    all_predicates = {**_PERSON_ROLE_PREDICATES, **_PLACE_ROLE_PREDICATES}
    role_set: set[str] = set()
    ms_list: list[ManuscriptRef] = []

    subject_uri = rdflib.URIRef(uri)
    for pred_uri, role_name in all_predicates.items():
        for ms_node in graph.subjects(rdflib.URIRef(pred_uri), subject_uri):
            ms_str = str(ms_node)
            role_set.add(role_name)
            ms_list.append(
                ManuscriptRef(
                    uri=ms_str,
                    label=_label(graph, ms_str),
                    role=role_name,
                )
            )

    return sorted(role_set), ms_list


def _geo(graph: rdflib.Graph, uri: str) -> dict[str, float] | None:
    """Return {lat, lon} from wgs84 predicates, or None if absent."""
    lat_val = next(
        graph.objects(rdflib.URIRef(uri), rdflib.URIRef(_WGS84 + "lat")), None
    )
    lon_val = next(
        graph.objects(rdflib.URIRef(uri), rdflib.URIRef(_WGS84 + "long")), None
    )
    if lat_val is None or lon_val is None:
        return None
    try:
        return {"lat": float(str(lat_val)), "lon": float(str(lon_val))}
    except ValueError:
        return None


async def _authority_info(
    project_id: uuid.UUID,
    label: str,
    db: AsyncSession,
) -> tuple[dict[str, str], dict[str, int] | None]:
    """Lookup authority identifiers and dates from the DB.

    Searches authority_matches across all project runs where matched_name
    case-insensitively matches the entity label. Returns the best match
    (highest confidence: high > medium > low).
    """
    run_rows = await db.execute(select(Run.id).where(Run.project_id == project_id))
    run_ids = list(run_rows.scalars().all())
    if not run_ids:
        return {}, None

    _CONF_RANK = {"high": 0, "medium": 1, "low": 2}

    best: AuthorityMatch | None = None
    lower_label = label.lower()

    for run_id in run_ids:
        rows = await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
        for am in rows.scalars().all():
            mn = (am.matched_name or "").lower()
            et = (am.entity_text or "").lower()
            if lower_label in (mn, et):
                if best is None:
                    best = am
                else:
                    rank_new = _CONF_RANK.get(am.confidence or "low", 2)
                    rank_best = _CONF_RANK.get(best.confidence or "low", 2)
                    if rank_new < rank_best:
                        best = am

    if best is None:
        return {}, None

    identifiers: dict[str, str] = {}
    if best.viaf_id:
        identifiers["viaf"] = best.viaf_id
    if best.wikidata_qid:
        identifiers["wikidata"] = best.wikidata_qid
    if best.mazal_id:
        identifiers["mazal"] = best.mazal_id

    dates: dict[str, int] | None = None
    payload: dict[str, Any] = best.payload or {}
    birth = payload.get("birth_year")
    death = payload.get("death_year")
    if birth is not None or death is not None:
        dates = {}
        if birth is not None:
            dates["birth"] = int(birth)
        if death is not None:
            dates["death"] = int(death)

    return identifiers, dates


# ── endpoint ───────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/research/entity", response_model=EntityDetailOut)
async def get_entity_detail(
    project_id: uuid.UUID,
    uri: str = Query(..., description="The entity URI to look up."),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> EntityDetailOut:
    """Resolve a Linked-Data URI to its structured entity summary."""
    graph = await _load_graph_or_404(project_id, auth, db)

    # Check URI exists in the graph
    entity_ref = rdflib.URIRef(uri)
    if (entity_ref, None, None) not in graph and (None, None, entity_ref) not in graph:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URI not found in project graph.")

    label = _label(graph, uri)
    entity_type = _entity_type(graph, uri)
    roles, manuscripts = _roles_and_manuscripts(graph, uri)
    geo = _geo(graph, uri) if entity_type == "place" else None

    identifiers: dict[str, str] = {}
    dates: dict[str, int] | None = None
    if label and entity_type in ("person", "place", "work"):
        identifiers, dates = await _authority_info(project_id, label, db)

    return EntityDetailOut(
        uri=uri,
        label=label,
        type=entity_type,
        roles=roles,
        manuscripts=manuscripts,
        dates=dates,
        geo=geo,
        identifiers=identifiers,
    )
