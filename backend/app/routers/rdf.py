"""RDF Graph — RDF graph router.

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
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.extraction_approval import ExtractionApproval
from app.models.rdf_artifact import RdfArtifact
from app.models.run import AuthorityMatch, RdfTripleOverride, Run, RunRecord
from app.pipeline.rdf_build import (
    LAYOUT_KINDS,
    RdfBuildOptions,
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
    coverage_path: str | None = None
    unknown_class_count: int | None = None
    ontology_coverage_path: str | None = None
    ontology_class_count: int | None = None
    ontology_property_count: int | None = None
    ontology_missing_terms: list[str] = []


class RdfBuildRequest(BaseModel):
    add_epistemological_status: bool = True
    add_cataloging_view: bool = True
    add_philological_overlay: bool = True


class RdfCoverageResponse(BaseModel):
    rdf_class_count: int
    unknown_class_count: int
    classes: list[dict[str, Any]]


class RdfOntologyCoverageResponse(BaseModel):
    classes_covered: int
    classes_total: int
    properties_covered: int
    properties_total: int
    missing_classes: list[str]
    missing_properties: list[str]
    class_percent: float
    property_percent: float


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
    manuscript_count: int | None = None
    manuscripts_in_view: int | None = None


class GraphCatalogResponse(BaseModel):
    total_nodes: int
    total_edges: int
    node_types: dict[str, int]
    edge_predicates: dict[str, int]
    manuscript_count: int


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


class TripleOverrideRequest(BaseModel):
    subject_uri: str
    predicate_uri: str
    new_value: str
    new_datatype: str | None = None
    new_lang: str | None = None


class TripleOverrideResponse(BaseModel):
    id: str
    subject_uri: str
    predicate_uri: str
    new_value: str
    new_datatype: str | None
    new_lang: str | None
    old_value: str | None
    created_at: str


# ── Helpers ────────────────────────────────────────────────────────────


async def _ensure_ttl_on_disk(run_id: uuid.UUID, db: AsyncSession) -> None:
    """Restore the TTL from the DB if the local cache file is missing.

    The local dyno filesystem is ephemeral on Heroku — this re-seeds it
    from the durable Postgres copy without requiring a full rebuild.
    Only called by read endpoints; the build endpoint writes both.
    """
    ttl = rdf_output_path_for_run(str(run_id))
    if ttl.exists():
        return
    row = await db.get(RdfArtifact, run_id)
    if row is None:
        return
    ttl.parent.mkdir(parents=True, exist_ok=True)
    ttl.write_text(row.ttl_content, encoding="utf-8")


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/{run_id}/rdf/build", response_model=RdfBuildResponse)
async def build(
    run_id: uuid.UUID,
    body: RdfBuildRequest | None = None,
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

    # Only approved authority matches flow into the RDF graph so that
    # unvetted candidates never produce sameAs / external-ID triples.
    matches = (
        await db.execute(
            select(AuthorityMatch)
            .where(AuthorityMatch.run_id == run_id)
            .where(AuthorityMatch.approved.is_(True))
        )
    ).scalars().all()

    # Only approved NER entities feed the graph — same "ship this in the
    # final output" semantics as Authority Enrichment (ExtractionApproval
    # docstring).  Curator overrides (override_type / override_role) take
    # precedence over the model's prediction, mirroring the Wikidata
    # Studio path.
    ner_rows = (
        await db.execute(
            select(ExtractionApproval)
            .where(ExtractionApproval.run_id == run_id)
            .where(ExtractionApproval.approved.is_(True))
        )
    ).scalars().all()
    entities_by_cn: dict[str, list[dict[str, Any]]] = {}
    for r in ner_rows:
        entities_by_cn.setdefault(r.control_number, []).append({
            "text":             r.override_text or r.text,
            "type":             (r.override_type or r.type or "").upper(),
            "role":             (r.override_role or r.role or "").upper(),
            "source":           r.source,
            "start":            int(r.start or 0),
            "end":              int(r.end or 0),
            "confidence":       r.confidence,
            "model_confidence": r.model_confidence,
        })

    marc_records = [dict(r.marc) for r in records]
    authority_matches = normalise_matches(matches)
    kima_places_by_cn: dict[str, dict[str, str]] = {}
    for rec in marc_records:
        cn = str(rec.get("_control_number") or rec.get("control_number") or "")
        kp = rec.get("kima_places")
        if cn and isinstance(kp, dict) and kp:
            kima_places_by_cn[cn.strip("\"'")] = kp

    opts = RdfBuildOptions(
        add_epistemological_status=(body or RdfBuildRequest()).add_epistemological_status,
        add_cataloging_view=(body or RdfBuildRequest()).add_cataloging_view,
        add_philological_overlay=(body or RdfBuildRequest()).add_philological_overlay,
    )

    overrides_rows = (
        await db.execute(
            select(RdfTripleOverride).where(RdfTripleOverride.run_id == run_id)
        )
    ).scalars().all()
    overrides = [
        {
            "subject_uri": r.subject_uri,
            "predicate_uri": r.predicate_uri,
            "new_value": r.new_value,
            "new_datatype": r.new_datatype,
            "new_lang": r.new_lang,
        }
        for r in overrides_rows
    ]

    out_path = rdf_output_path_for_run(str(run_id))
    try:
        result: RdfBuildResult = await build_rdf_graph(
            marc_records=marc_records,
            authority_matches=authority_matches,
            entities_by_cn=entities_by_cn,
            output_path=out_path,
            overrides=overrides,
            kima_places_by_cn=kima_places_by_cn,
            build_options=opts,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("RDF build failed for run %s", run_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RDF build failed: {exc}",
        ) from exc

    # Persist TTL to Postgres so it survives dyno restarts / deploys.
    ttl_text = out_path.read_text(encoding="utf-8")
    existing = await db.get(RdfArtifact, run_id)
    if existing:
        existing.ttl_content = ttl_text
        existing.triples_count = result.triples_count
        existing.manuscripts_count = result.manuscripts_count
    else:
        db.add(RdfArtifact(
            run_id=run_id,
            ttl_content=ttl_text,
            triples_count=result.triples_count,
            manuscripts_count=result.manuscripts_count,
        ))
    await db.commit()

    # Bust every downstream cache so the fresh TTL is visible immediately:
    # 1. Cytoscape JSON files (graph_{layout}_{max_nodes}.json) — delete them
    #    so the next GET /graph re-derives from the new TTL.
    for cache_file in out_path.parent.glob("graph_*.json"):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
    for cache_file in out_path.parent.glob("graph_viewport_*.json"):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass

    # 2. Research merged-graph LRU (in-process) — drop all entries that
    #    include this run so the Research Explorer re-loads the new graph.
    try:
        from app.pipeline.research_graph import invalidate_cache as _inval  # noqa: PLC0415
        _inval(str(run_id))
    except Exception:  # noqa: BLE001
        pass

    return RdfBuildResponse(**result.to_dict())


@router.get("/{run_id}/rdf/coverage", response_model=RdfCoverageResponse)
async def coverage(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RdfCoverageResponse:
    """Return the HMO projection-coverage report from the latest RDF build."""
    await _lookup_run_with_access(db, run_id, auth)
    coverage_path = rdf_output_path_for_run(str(run_id)).parent / "rdf_projection_coverage.json"
    if not coverage_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Coverage report not found — build RDF first.",
        )
    report = json.loads(coverage_path.read_text(encoding="utf-8"))
    classes = report.get("classes") or []
    unknown_count = sum(
        1 for cls in classes if cls.get("projection_status") == "unknown"
    )
    return RdfCoverageResponse(
        rdf_class_count=int(report.get("rdf_class_count") or len(classes)),
        unknown_class_count=unknown_count,
        classes=classes,
    )


@router.get("/{run_id}/rdf/ontology-coverage", response_model=RdfOntologyCoverageResponse)
async def ontology_coverage(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> RdfOntologyCoverageResponse:
    """Return HMO ontology class/property coverage (admin-only dev metric)."""
    await _lookup_run_with_access(db, run_id, auth)
    coverage_path = rdf_output_path_for_run(str(run_id)).parent / "ontology_coverage.json"
    if not coverage_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ontology coverage report not found — build RDF first.",
        )
    report = json.loads(coverage_path.read_text(encoding="utf-8"))
    classes_total = int(report.get("classes_total") or 0)
    properties_total = int(report.get("properties_total") or 0)
    classes_covered = int(report.get("classes_covered") or 0)
    properties_covered = int(report.get("properties_covered") or 0)
    return RdfOntologyCoverageResponse(
        classes_covered=classes_covered,
        classes_total=classes_total,
        properties_covered=properties_covered,
        properties_total=properties_total,
        missing_classes=list(report.get("missing_classes") or []),
        missing_properties=list(report.get("missing_properties") or []),
        class_percent=100.0 * classes_covered / classes_total if classes_total else 0.0,
        property_percent=100.0 * properties_covered / properties_total if properties_total else 0.0,
    )


@router.get("/{run_id}/rdf/catalog", response_model=GraphCatalogResponse)
async def graph_catalog(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> GraphCatalogResponse:
    """Full-corpus node/edge counts for filter chips (not capped by viewport)."""
    await _lookup_run_with_access(db, run_id, auth)
    await _ensure_ttl_on_disk(run_id, db)
    ttl = rdf_output_path_for_run(str(run_id))
    if not ttl.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No RDF graph yet for this run — POST /rdf/build first.",
        )

    import asyncio  # noqa: PLC0415
    from app.pipeline.graph_index import ensure_index  # noqa: PLC0415

    catalog = await asyncio.to_thread(ensure_index, ttl, ttl.parent)
    return GraphCatalogResponse(
        total_nodes=catalog.total_nodes,
        total_edges=catalog.total_edges,
        node_types=catalog.node_types,
        edge_predicates=catalog.edge_predicates,
        manuscript_count=catalog.manuscript_count,
    )


async def _viewport_response(
    run_id: uuid.UUID,
    db: AsyncSession,
    *,
    types: list[str],
    predicates: list[str],
    q: str,
    seed: str,
    radius: int,
    max_nodes: int,
    layout: str,
    manuscripts_only: bool,
) -> CytoscapeGraphResponse:
    if layout not in LAYOUT_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown layout={layout!r}; valid: {list(LAYOUT_KINDS)}",
        )

    await _ensure_ttl_on_disk(run_id, db)
    ttl = rdf_output_path_for_run(str(run_id))
    if not ttl.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No RDF graph yet for this run — POST /rdf/build first.",
        )

    import asyncio  # noqa: PLC0415
    import json  # noqa: PLC0415
    from app.pipeline.graph_index import (  # noqa: PLC0415
        ViewportParams,
        build_viewport_payload,
        ensure_index,
        viewport_cache_path,
    )

    run_dir = ttl.parent
    await asyncio.to_thread(ensure_index, ttl, run_dir)

    params = ViewportParams(
        types=types,
        predicates=predicates,
        q=q,
        seed=seed,
        radius=radius,
        max_nodes=max_nodes,
        layout=layout,
        manuscripts_only=manuscripts_only,
    )
    cache_path = viewport_cache_path(run_dir, params)
    if cache_path.exists() and cache_path.stat().st_mtime >= ttl.stat().st_mtime:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return CytoscapeGraphResponse(**cached)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    payload = await asyncio.to_thread(build_viewport_payload, run_dir, params)
    response = CytoscapeGraphResponse(
        nodes=[CytoscapeNode(**n) for n in payload["nodes"]],
        edges=[CytoscapeEdge(**e) for e in payload["edges"]],
        truncated=bool(payload["truncated"]),
        total_nodes=int(payload["total_nodes"]),
        total_edges=int(payload["total_edges"]),
        layout=payload.get("layout"),
        manuscript_count=payload.get("manuscript_count"),
        manuscripts_in_view=payload.get("manuscripts_in_view"),
    )
    try:
        cache_path.write_text(response.model_dump_json(), encoding="utf-8")
    except OSError:
        pass
    return response


@router.get("/{run_id}/rdf/viewport", response_model=CytoscapeGraphResponse)
async def graph_viewport(
    run_id: uuid.UUID,
    types: list[str] = Query(default=[]),
    predicates: list[str] = Query(default=[]),
    q: str = "",
    seed: str = "",
    radius: int = 0,
    max_nodes: int = 500,
    layout: str = "spring",
    manuscripts_only: bool = False,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> CytoscapeGraphResponse:
    """Filtered, budget-capped Cytoscape payload. Manuscripts always included in default view."""
    await _lookup_run_with_access(db, run_id, auth)
    return await _viewport_response(
        run_id,
        db,
        types=types,
        predicates=predicates,
        q=q,
        seed=seed,
        radius=max(0, min(radius, 5)),
        max_nodes=max(50, min(max_nodes, 2000)),
        layout=layout,
        manuscripts_only=manuscripts_only,
    )


@router.get("/{run_id}/rdf/ego", response_model=CytoscapeGraphResponse)
async def graph_ego(
    run_id: uuid.UUID,
    center: str = Query(..., description="Node URI at the centre of the ego network"),
    radius: int = 2,
    max_nodes: int = 500,
    layout: str = "spring",
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> CytoscapeGraphResponse:
    """Ego network around one node (e.g. click-through from list view)."""
    await _lookup_run_with_access(db, run_id, auth)
    return await _viewport_response(
        run_id,
        db,
        types=[],
        predicates=[],
        q="",
        seed=center,
        radius=max(1, min(radius, 5)),
        max_nodes=max(50, min(max_nodes, 2000)),
        layout=layout,
        manuscripts_only=False,
    )


@router.get("/{run_id}/rdf/graph", response_model=CytoscapeGraphResponse)
async def graph(
    run_id: uuid.UUID,
    max_nodes: int = 500,
    layout: str = "spring",
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> CytoscapeGraphResponse:
    """Return default viewport (all manuscripts + top neighbours). Back-compat alias."""
    await _lookup_run_with_access(db, run_id, auth)
    return await _viewport_response(
        run_id,
        db,
        types=[],
        predicates=[],
        q="",
        seed="",
        radius=0,
        max_nodes=max(50, min(max_nodes, 2000)),
        layout=layout,
        manuscripts_only=False,
    )


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
    await _ensure_ttl_on_disk(run_id, db)
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

    await _ensure_ttl_on_disk(run_id, db)
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

    await _ensure_ttl_on_disk(run_id, db)
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


@router.patch("/{run_id}/rdf/triple", response_model=TripleOverrideResponse)
async def save_triple_override(
    run_id: uuid.UUID,
    body: TripleOverrideRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> TripleOverrideResponse:
    """Save a curator edit to an RDF literal value.

    Upserts: if an override for (run_id, subject_uri, predicate_uri) already
    exists, updates it. Otherwise creates a new row. The override is applied
    the next time POST /rdf/build is called.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)

    existing = (
        await db.execute(
            select(RdfTripleOverride)
            .where(RdfTripleOverride.run_id == run_id)
            .where(RdfTripleOverride.subject_uri == body.subject_uri)
            .where(RdfTripleOverride.predicate_uri == body.predicate_uri)
        )
    ).scalar_one_or_none()

    if existing:
        existing.new_value = body.new_value
        existing.new_datatype = body.new_datatype
        existing.new_lang = body.new_lang
        override = existing
    else:
        override = RdfTripleOverride(
            run_id=run_id,
            subject_uri=body.subject_uri,
            predicate_uri=body.predicate_uri,
            new_value=body.new_value,
            new_datatype=body.new_datatype,
            new_lang=body.new_lang,
            created_by=getattr(auth, "user_id", None),
        )
        db.add(override)

    await db.commit()
    await db.refresh(override)

    # Bust Cytoscape JSON cache and research LRU so the edit is visible
    # immediately on the next graph/research load without a manual rebuild.
    ttl_path = rdf_output_path_for_run(str(run_id))
    for cache_file in ttl_path.parent.glob("graph_*.json"):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        from app.pipeline.research_graph import invalidate_cache as _inval  # noqa: PLC0415
        _inval(str(run_id))
    except Exception:  # noqa: BLE001
        pass

    return TripleOverrideResponse(
        id=str(override.id),
        subject_uri=override.subject_uri,
        predicate_uri=override.predicate_uri,
        new_value=override.new_value,
        new_datatype=override.new_datatype,
        new_lang=override.new_lang,
        old_value=override.old_value,
        created_at=override.created_at.isoformat(),
    )


@router.get("/{run_id}/rdf/overrides", response_model=list[TripleOverrideResponse])
async def list_triple_overrides(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[TripleOverrideResponse]:
    """List all curator triple overrides for a run."""
    await _lookup_run_with_access(db, run_id, auth)
    rows = (
        await db.execute(
            select(RdfTripleOverride)
            .where(RdfTripleOverride.run_id == run_id)
            .order_by(RdfTripleOverride.created_at.asc())
        )
    ).scalars().all()
    return [
        TripleOverrideResponse(
            id=str(r.id),
            subject_uri=r.subject_uri,
            predicate_uri=r.predicate_uri,
            new_value=r.new_value,
            new_datatype=r.new_datatype,
            new_lang=r.new_lang,
            old_value=r.old_value,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


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
