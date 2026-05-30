"""Stage 4 — RDF graph router.

Five endpoints under ``/runs/{run_id}/rdf/``:

* ``POST /build``             — build the per-run TTL (synchronous return).
* ``GET  /graph``             — Cytoscape JSON of the latest build.
* ``GET  /download.ttl``      — raw Turtle file.
* ``POST /validate``          — run SHACL, return the report.
* ``GET  /status``            — ``idle / built / validated / error``.

The build output goes to ``backend/state/runs/{run_id}/manuscripts.ttl``.
``marc_records`` are pulled from ``RunRecord`` rows; ``authority_matches``
from ``AuthorityMatch`` rows.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline.rdf_build import (
    LAYOUT_KINDS,
    RdfBuildResult,
    ShaclReport,
    build_rdf_graph,
    compute_layout,
    graph_to_cytoscape_json,
    load_graph,
    node_detail,
    normalise_matches,
    rdf_output_path_for_run,
    validate_with_shacl,
)
from app.routers.runs import _lookup_run_with_access

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/runs", tags=["rdf"])


# ── DTOs ───────────────────────────────────────────────────────────────


class RdfBuildResponse(BaseModel):
    triples_count: int
    manuscripts_count: int
    output_path: str
    started_at: str
    finished_at: str
    mapping_errors: list[str]


class CytoscapeNodePosition(BaseModel):
    x: float
    y: float


class CytoscapeNode(BaseModel):
    id: str
    label: str
    type: str
    color: str
    properties: dict[str, list[str]] = {}
    # Pre-computed by the server so the browser uses Cytoscape's
    # ``preset`` layout (zero-cost placement). Absent when the legacy
    # layout endpoint is hit.
    position: CytoscapeNodePosition | None = None


class CytoscapeEdge(BaseModel):
    id: str
    source: str
    target: str
    predicate: str
    predicate_label: str


class CytoscapeGraphResponse(BaseModel):
    nodes: list[CytoscapeNode]
    edges: list[CytoscapeEdge]
    truncated: bool
    total_nodes: int
    total_edges: int
    layout: str | None = None


class NodeTypeRef(BaseModel):
    uri: str
    label: str


class NodeProperty(BaseModel):
    predicate: str
    predicate_label: str
    value: str
    datatype: str | None = None


class NodeEdgeRef(BaseModel):
    predicate: str
    predicate_label: str
    target_id: str | None = None
    target_label: str | None = None
    target_type: str | None = None
    target_color: str | None = None
    source_id: str | None = None
    source_label: str | None = None
    source_type: str | None = None
    source_color: str | None = None


class NodeDetailResponse(BaseModel):
    id: str
    label: str
    type: str
    color: str
    types: list[NodeTypeRef]
    properties: list[NodeProperty]
    outgoing: list[NodeEdgeRef]
    incoming: list[NodeEdgeRef]


class ShaclViolationDto(BaseModel):
    focus_node: str
    source_shape: str
    severity: str
    message: str
    value: str | None = None


class ShaclReportDto(BaseModel):
    conforms: bool
    violations: list[ShaclViolationDto]


class RdfStatusResponse(BaseModel):
    status: str  # idle | built | validated | error
    triples_count: int | None = None
    manuscripts_count: int | None = None
    last_built_at: str | None = None
    error: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/{run_id}/rdf/build", response_model=RdfBuildResponse)
async def build(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RdfBuildResponse:
    """Build the RDF graph for the run and write it to disk.

    Returns synchronously — the mapper runs in a worker thread so we
    don't block the event loop, but the wait is bounded (typically
    < 10 s for a 100-MS run on a laptop).
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)

    records = (
        await db.execute(
            select(RunRecord)
            .where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    if not records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run has no MARC records — ingest before building RDF.",
        )

    matches = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()

    marc_records = [dict(r.marc) for r in records]
    authority_matches = normalise_matches(matches)

    out_path = rdf_output_path_for_run(str(run_id))
    try:
        result: RdfBuildResult = await build_rdf_graph(
            marc_records=marc_records,
            authority_matches=authority_matches,
            output_path=out_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("RDF build failed for run %s", run_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RDF build failed: {exc}",
        ) from exc

    return RdfBuildResponse(**result.to_dict())


@router.get("/{run_id}/rdf/graph", response_model=CytoscapeGraphResponse)
async def graph(
    run_id: uuid.UUID,
    max_nodes: int = 500,
    # Server-side layout — browser used to freeze running cose-bilkent
    # on 500 nodes. Layouts are cheap to recompute (~1s for spring on
    # 500 nodes) and cached to disk per (run, layout, max_nodes).
    layout: str = "spring",
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> CytoscapeGraphResponse:
    """Return Cytoscape.js JSON for the latest build, with positions."""
    await _lookup_run_with_access(db, run_id, auth)

    if layout not in LAYOUT_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown layout={layout!r}; valid: {list(LAYOUT_KINDS)}",
        )

    ttl = rdf_output_path_for_run(str(run_id))
    if not ttl.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No RDF graph yet for this run — POST /rdf/build first.",
        )

    import asyncio  # noqa: PLC0415
    import json  # noqa: PLC0415

    # Per-(layout, max_nodes) cache. Invalidated implicitly when the
    # underlying .ttl is rebuilt (mtime check).
    cache_path = ttl.parent / f"graph_{layout}_{max_nodes}.json"
    if cache_path.exists() and cache_path.stat().st_mtime >= ttl.stat().st_mtime:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return CytoscapeGraphResponse(**cached)
        except (json.JSONDecodeError, ValueError):
            # Cache corrupt — fall through and rebuild.
            pass

    g = await asyncio.to_thread(load_graph, ttl)
    total_nodes_pre = {str(s) for s, _, _ in g} | {
        str(o) for _, _, o in g if not _is_literal_value(o)
    }
    raw_total_edges = sum(
        1 for _s, p, o in g
        if not _is_literal_value(o)
        and str(p) != "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    )

    payload = await asyncio.to_thread(
        graph_to_cytoscape_json, g, max_nodes=max_nodes,
    )
    payload = await asyncio.to_thread(
        compute_layout, payload, kind=layout,
    )
    truncated = len(payload["nodes"]) < len(total_nodes_pre)

    response = CytoscapeGraphResponse(
        nodes=[CytoscapeNode(**n) for n in payload["nodes"]],
        edges=[CytoscapeEdge(**e) for e in payload["edges"]],
        truncated=truncated,
        total_nodes=len(total_nodes_pre),
        total_edges=raw_total_edges,
        layout=layout,
    )
    try:
        cache_path.write_text(response.model_dump_json(), encoding="utf-8")
    except OSError:
        pass  # caching is best-effort
    return response


@router.get("/{run_id}/rdf/node", response_model=NodeDetailResponse)
async def node(
    run_id: uuid.UUID,
    id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> NodeDetailResponse:
    """Return full detail for ONE node (clicked in the graph view).

    Includes every ``rdf:type``, every literal property, every
    outgoing edge (with target's label + colour), every incoming
    edge. The frontend renders this as a side panel.

    ``id`` is passed as a query parameter (not a path segment) so URIs
    containing slashes don't need encoding gymnastics.
    """
    await _lookup_run_with_access(db, run_id, auth)
    ttl = rdf_output_path_for_run(str(run_id))
    if not ttl.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No RDF graph yet for this run — POST /rdf/build first.",
        )

    import asyncio  # noqa: PLC0415

    g = await asyncio.to_thread(load_graph, ttl)
    detail = await asyncio.to_thread(node_detail, g, id)
    return NodeDetailResponse(**detail)


def _is_literal_value(o: Any) -> bool:
    from rdflib import Literal  # noqa: PLC0415

    return isinstance(o, Literal)


@router.get("/{run_id}/rdf/download.ttl")
async def download_ttl(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Stream the raw Turtle file as a download."""
    await _lookup_run_with_access(db, run_id, auth)

    ttl = rdf_output_path_for_run(str(run_id))
    if not ttl.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No RDF graph yet for this run — POST /rdf/build first.",
        )
    return FileResponse(
        ttl,
        media_type="text/turtle",
        filename=f"run-{run_id}-manuscripts.ttl",
    )


@router.post("/{run_id}/rdf/validate", response_model=ShaclReportDto)
async def validate(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ShaclReportDto:
    """Run SHACL validation over the latest built graph."""
    await _lookup_run_with_access(db, run_id, auth, write=True)

    ttl = rdf_output_path_for_run(str(run_id))
    if not ttl.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No RDF graph yet for this run — POST /rdf/build first.",
        )
    try:
        report: ShaclReport = await validate_with_shacl(ttl)
    except Exception as exc:  # noqa: BLE001
        logger.exception("SHACL validation failed for run %s", run_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SHACL validation failed: {exc}",
        ) from exc
    return ShaclReportDto(**report.to_dict())


@router.get("/{run_id}/rdf/status", response_model=RdfStatusResponse)
async def get_status(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RdfStatusResponse:
    """Report whether the run has a built graph on disk.

    ``idle``      — no manuscripts.ttl on disk.
    ``built``     — file present, never SHACL-validated this session.
    ``validated`` — file present + an in-memory validation marker (not
                    persisted across restarts — frontend should treat
                    this as a hint).
    """
    await _lookup_run_with_access(db, run_id, auth)

    ttl = rdf_output_path_for_run(str(run_id))
    if not ttl.exists():
        return RdfStatusResponse(status="idle")

    stat = ttl.stat()
    from datetime import datetime, timezone  # noqa: PLC0415

    last_built = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    # Quick triple count + manuscript count — cheap to scan a 100k-triple
    # graph here (sub-second), so we surface it on the status card.
    try:
        import asyncio  # noqa: PLC0415

        g = await asyncio.to_thread(load_graph, ttl)
        triples = len(g)
        from rdflib import RDF  # noqa: PLC0415

        manuscripts = 0
        for _s, _p, o in g.triples((None, RDF.type, None)):
            local = str(o).rsplit("/", 1)[-1].split("#")[-1]
            if local in ("Manuscript", "F4_Manifestation_Singleton", "F3_Manifestation"):
                manuscripts += 1
    except Exception as exc:  # noqa: BLE001
        return RdfStatusResponse(status="error", error=str(exc), last_built_at=last_built)

    return RdfStatusResponse(
        status="built",
        triples_count=triples,
        manuscripts_count=manuscripts,
        last_built_at=last_built,
    )
