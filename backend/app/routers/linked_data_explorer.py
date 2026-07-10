"""Linked Data Explorer — custom SPARQL console endpoints.

Three endpoints proxy SPARQL queries to different data sources:
  - /projects/{id}/research/sparql         → project HMO graph (in-process rdflib)
  - /projects/{id}/research/sparql/wikibase → project Wikibase SPARQL endpoint
  - /projects/{id}/research/sparql/wikidata → public Wikidata SPARQL endpoint

All endpoints:
  - Require project viewer access
  - Only allow SELECT / CONSTRUCT queries (no write operations)
  - Cap results at 1 000 rows
  - Enforce a 30-second timeout
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.project import Membership
from app.models.rdf_artifact import RdfArtifact
from app.models.run import Run
from app.pipeline.rdf_build import rdf_output_path_for_run
from app.pipeline.research_graph import load_merged_graph
from app.settings import get_settings

router = APIRouter(tags=["research"])
logger = logging.getLogger(__name__)

_MAX_ROWS = 1000
_TIMEOUT_S = 30.0

# Simple in-memory LRU cache for Wikidata proxy results (10-minute TTL equiv.)
_WIKIDATA_CACHE: OrderedDict[str, Any] = OrderedDict()
_WIKIDATA_CACHE_MAX = 64


# ── Shared helpers ───────────────────────────────────────────────────────────

class SparqlRequest(BaseModel):
    query: str


class SparqlResponse(BaseModel):
    columns: list[str]
    rows: list[list[str | None]]
    truncated: bool


def _validate_query(query: str) -> None:
    """Raise 400 if the query is not a read-only SELECT or CONSTRUCT."""
    stripped = re.sub(r"(#[^\n]*)|\s+", " ", query).strip().upper()
    if not (stripped.startswith("SELECT") or stripped.startswith("CONSTRUCT")
            or stripped.startswith("PREFIX") or stripped.startswith("BASE")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only SELECT and CONSTRUCT queries are allowed.",
        )
    for forbidden in ("INSERT", "DELETE", "CLEAR", "DROP", "CREATE", "LOAD", "COPY", "MOVE", "ADD"):
        if re.search(rf"\b{forbidden}\b", stripped):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Write operation '{forbidden}' is not permitted.",
            )


async def _require_viewer(
    project_id: uuid.UUID,
    auth: AuthContext,
    db: AsyncSession,
) -> None:
    row = await db.execute(
        select(Membership).where(
            Membership.project_id == project_id,
            Membership.user_id == auth.user.id,
        )
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this project.")


async def _run_ids_for_project(project_id: uuid.UUID, db: AsyncSession) -> list[str]:
    rows = await db.execute(select(Run.id).where(Run.project_id == project_id))
    return [str(r) for r in rows.scalars().all()]


async def _restore_missing_ttls(run_ids: list[str], db: AsyncSession) -> None:
    for run_id in run_ids:
        ttl_path = rdf_output_path_for_run(run_id)
        if ttl_path.exists():
            continue
        row = await db.get(RdfArtifact, uuid.UUID(run_id))
        if row is None:
            continue
        ttl_path.parent.mkdir(parents=True, exist_ok=True)
        ttl_path.write_text(row.ttl_content, encoding="utf-8")


async def _load_graph_or_404(project_id: uuid.UUID, auth: AuthContext, db: AsyncSession):
    await _require_viewer(project_id, auth, db)
    run_ids = await _run_ids_for_project(project_id, db)
    if not run_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No runs found for this project — build the RDF first.",
        )
    await _restore_missing_ttls(run_ids, db)
    graph = await load_merged_graph(run_ids)
    if len(graph) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No RDF data found — build the graph for each run first.",
        )
    return graph


# ── 1. HMO Graph endpoint ────────────────────────────────────────────────────

def _execute_rdflib_query(graph, query: str) -> SparqlResponse:
    import rdflib
    from rdflib.namespace import RDF, RDFS
    from rdflib import Namespace

    HM    = Namespace("https://w3id.org/mhm/ontology#")
    CIDOC = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
    LRMOO = Namespace("http://iflastandards.info/ns/lrm/lrmoo/")
    WGS84 = Namespace("http://www.w3.org/2003/01/geo/wgs84_pos#")

    init_ns = {
        "hm":    HM, "cidoc": CIDOC, "lrmoo": LRMOO,
        "wgs84": WGS84, "rdf": RDF, "rdfs": RDFS,
    }

    result = graph.query(query, initNs=init_ns)

    # CONSTRUCT returns a graph, not rows
    if isinstance(result.graph, rdflib.Graph):
        triples = list(result.graph)[:_MAX_ROWS]
        rows = [[str(s), str(p), str(o)] for s, p, o in triples]
        return SparqlResponse(
            columns=["subject", "predicate", "object"],
            rows=rows,
            truncated=len(triples) == _MAX_ROWS,
        )

    columns = [str(v) for v in result.vars] if result.vars else []
    all_rows = list(result)
    truncated = len(all_rows) > _MAX_ROWS
    rows = [
        [str(row[v]) if row[v] is not None else None for v in result.vars]
        for row in all_rows[:_MAX_ROWS]
    ]
    return SparqlResponse(columns=columns, rows=rows, truncated=truncated)


@router.post("/projects/{project_id}/research/sparql")
async def sparql_hmo_graph(
    project_id: uuid.UUID,
    body: SparqlRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SparqlResponse:
    """Execute a SELECT/CONSTRUCT query against the project's merged HMO RDF graph."""
    _validate_query(body.query)
    graph = await _load_graph_or_404(project_id, auth, db)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_execute_rdflib_query, graph, body.query),
            timeout=_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT, detail="Query timed out after 30 s.")
    except Exception as exc:
        logger.warning("HMO SPARQL query failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Query error: {exc}")


# ── 2. Wikibase proxy endpoint ───────────────────────────────────────────────

def _sparql_json_to_response(data: dict[str, Any]) -> SparqlResponse:
    """Convert SPARQL 1.1 JSON results to SparqlResponse."""
    bindings = data.get("results", {}).get("bindings", [])
    vars_list: list[str] = data.get("head", {}).get("vars", [])
    truncated = len(bindings) > _MAX_ROWS
    rows = [
        [b.get(v, {}).get("value") for v in vars_list]
        for b in bindings[:_MAX_ROWS]
    ]
    return SparqlResponse(columns=vars_list, rows=rows, truncated=truncated)


async def run_wikibase_sparql(wikibase_url: str, query: str) -> dict[str, Any]:
    """Execute a SPARQL query against a Wikibase endpoint, returning the raw
    SPARQL 1.1 JSON result. Raises on timeout / HTTP / transport errors so
    callers can decide whether to surface a 5xx or degrade gracefully."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        resp = await client.get(
            wikibase_url,
            params={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )
        resp.raise_for_status()
    return resp.json()


@router.post("/projects/{project_id}/research/sparql/wikibase")
async def sparql_wikibase(
    project_id: uuid.UUID,
    body: SparqlRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SparqlResponse:
    """Proxy a SELECT/CONSTRUCT query to the project's Wikibase SPARQL endpoint."""
    _validate_query(body.query)
    await _require_viewer(project_id, auth, db)

    settings = get_settings()
    wikibase_url: str = getattr(settings, "wikibase_sparql_url", "")
    if not wikibase_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Wikibase SPARQL endpoint is not configured (WIKIBASE_SPARQL_URL).",
        )

    try:
        return _sparql_json_to_response(await run_wikibase_sparql(wikibase_url, body.query))
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT, detail="Wikibase query timed out.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Wikibase error: {exc.response.status_code}")
    except Exception as exc:
        logger.warning("Wikibase SPARQL proxy failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Wikibase proxy error: {exc}")


# ── 3. Wikidata proxy endpoint ───────────────────────────────────────────────

_WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_USER_AGENT = "MHM-Pipeline-Web/1.0 (hebrew manuscript pipeline; contact via project admin)"


@router.post("/projects/{project_id}/research/sparql/wikidata")
async def sparql_wikidata(
    project_id: uuid.UUID,
    body: SparqlRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SparqlResponse:
    """Proxy a SELECT/CONSTRUCT query to Wikidata's public SPARQL endpoint.

    Results are cached in-process for repeated identical queries.
    """
    _validate_query(body.query)
    await _require_viewer(project_id, auth, db)

    cache_key = hashlib.sha256(body.query.encode()).hexdigest()
    if cache_key in _WIKIDATA_CACHE:
        return _WIKIDATA_CACHE[cache_key]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                _WIKIDATA_SPARQL,
                params={"query": body.query, "format": "json"},
                headers={
                    "Accept": "application/sparql-results+json",
                    "User-Agent": _USER_AGENT,
                },
            )
            resp.raise_for_status()
        result = _sparql_json_to_response(resp.json())
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT, detail="Wikidata query timed out (30 s). Try a more specific query.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Wikidata returned HTTP {exc.response.status_code}.")
    except Exception as exc:
        logger.warning("Wikidata SPARQL proxy failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Wikidata proxy error: {exc}")

    # Store in LRU cache
    _WIKIDATA_CACHE[cache_key] = result
    _WIKIDATA_CACHE.move_to_end(cache_key)
    if len(_WIKIDATA_CACHE) > _WIKIDATA_CACHE_MAX:
        _WIKIDATA_CACHE.popitem(last=False)

    return result
