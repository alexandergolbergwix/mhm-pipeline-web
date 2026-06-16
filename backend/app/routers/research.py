"""Research Explorer — analytics endpoints.

Each endpoint merges all run-level TTL files for a project, then runs a
pre-defined SPARQL query over the merged graph.

All endpoints require project viewer access.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.project import Membership
from app.models.rdf_artifact import RdfArtifact
from app.models.run import Run
from app.pipeline.inference_cache import (
    read_from_inference_cache,
    write_to_inference_cache,
)
from app.pipeline.research_aggregate import compute_aggregated_summary, wikibase_provider
from app.pipeline.research_graph import load_merged_graph
from app.pipeline.rdf_build import rdf_output_path_for_run
from app.pipeline.research_queries import (
    query_co_occurrence,
    query_geography,
    query_geography_heatmap,
    query_ownership_chains,
    query_people_network,
)
from app.routers.linked_data_explorer import run_wikibase_sparql
from app.routers.wikidata_studio import (
    studio_fingerprints_for_project,
    studio_items_for_project,
)
from app.settings import get_settings

router = APIRouter(tags=["research"])
logger = logging.getLogger(__name__)

_SUMMARY_ALGORITHM_VERSION = "linked-data-overview-v2"


# ── Helpers ──────────────────────────────────────────────────────────

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
    rows = await db.execute(
        select(Run.id).where(Run.project_id == project_id)
    )
    return [str(r) for r in rows.scalars().all()]


async def _summary_fingerprint(
    run_ids: list[str],
    db: AsyncSession,
    *,
    studio_fps: dict[str, str],
    wikibase_url: str,
) -> str:
    """Cache key that changes whenever any source's CONTENT changes.

    Keying on the run-id set alone (the old behaviour) meant an RDF
    *rebuild* — which changes content but not the run-id set — never
    invalidated the cached summary, so a zero computed in a bad window
    stayed pinned. The key now folds in, per run, the RDF artifact's
    ``built_at`` + triple count AND the Wikidata Studio cache fingerprint
    (so approving a match invalidates), plus a Wikibase-endpoint marker (so
    configuring Wikibase invalidates).
    """
    parts: list[str] = []
    for run_id in sorted(run_ids):
        row = await db.get(RdfArtifact, uuid.UUID(run_id))
        if row is None:
            parts.append(f"{run_id}:none")
        else:
            built = row.built_at.isoformat() if row.built_at else "?"
            parts.append(f"{run_id}:{built}:{row.triples_count or 0}")
        parts.append(f"studio:{run_id}:{studio_fps.get(run_id, 'none')}")
    parts.append(f"wb:{hashlib.sha256(wikibase_url.encode()).hexdigest()[:12]}")
    parts.append(f"summary:{_SUMMARY_ALGORITHM_VERSION}")
    return "|".join(parts)


def _is_coherent_summary(summary: dict[str, Any]) -> bool:
    """A summary with triples but zero entities is incoherent (a bad-window
    read) — never serve or cache it. An empty graph (0 triples) is handled
    upstream by the 404 path, so it never reaches here.

    For the aggregated shape, also require ``max(by_source) <= total <=
    sum(by_source)`` per type — a malformed merge violates that."""
    if not isinstance(summary, dict):
        return False
    triples = summary.get("triples") or 0
    entities = (
        (summary.get("total_manuscripts") or 0)
        + (summary.get("total_works") or 0)
        + (summary.get("total_persons") or 0)
        + (summary.get("total_places") or 0)
    )
    if triples > 0 and entities == 0:
        return False
    if triples > 0 and (summary.get("total_manuscripts") or 0) == 0:
        return False
    by_type = summary.get("by_type")
    if isinstance(by_type, dict):
        for agg in by_type.values():
            if not isinstance(agg, dict):
                continue
            total = agg.get("total") or 0
            vals = [v or 0 for v in (agg.get("by_source") or {}).values()]
            if vals and not (max(vals) <= total <= sum(vals)):
                return False
    return True


async def _restore_missing_ttls(
    run_ids: list[str], db: AsyncSession, *, force: bool = False,
) -> None:
    """Restore any TTL files missing from the local cache by reading rdf_artifacts.

    Heroku dynos have an ephemeral filesystem — after a deploy or restart the
    per-run manuscripts.ttl files are gone. This seeds them back from Postgres
    so research queries see the same data without requiring a full rebuild.
    """
    for run_id in run_ids:
        ttl_path = rdf_output_path_for_run(run_id)
        if ttl_path.exists() and not force:
            continue
        row = await db.get(RdfArtifact, uuid.UUID(run_id))
        if row is None:
            continue
        ttl_path.parent.mkdir(parents=True, exist_ok=True)
        # rdf_artifacts (Postgres) is authoritative; on force we overwrite a
        # possibly-truncated local file seeded during an earlier bad window.
        ttl_path.write_text(row.ttl_content, encoding="utf-8")


async def _load_or_404(
    project_id: uuid.UUID,
    auth: AuthContext,
    db: AsyncSession,
):
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


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/research/summary")
async def research_summary(
    project_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Aggregate statistics: total manuscripts, works, persons, places.

    Cached in Redis/Postgres (kind=research.summary) so a good result
    survives dyno restarts. Two safeguards (added 2026-06-14) stop the
    recurring 0/0/0/0-with-N-triples bug:

    * the cache key folds in each run's ``RdfArtifact.built_at`` + triple
      count, so an RDF *rebuild* invalidates it (run-id set alone did not);
    * an incoherent summary (triples > 0 but every entity count 0 — the
      signature of a bad-window read) is never served from cache nor
      written to it. On a stale-incoherent hit we recompute from the live
      graph (which the other research tabs read directly and prove healthy)
      and overwrite the cache with the good value.
    """
    await _require_viewer(project_id, auth, db)
    run_ids = await _run_ids_for_project(project_id, db)
    if not run_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No runs found for this project — build the RDF first.",
        )

    wikibase_url: str = getattr(get_settings(), "wikibase_sparql_url", "") or ""
    studio_fps = await studio_fingerprints_for_project(run_ids, db)
    fingerprint = await _summary_fingerprint(
        run_ids, db, studio_fps=studio_fps, wikibase_url=wikibase_url,
    )
    cache_key = {"project": str(project_id), "fp": fingerprint}

    cached = await read_from_inference_cache(
        db, kind="research.summary", query_summary=cache_key,
    )
    if cached is not None and _is_coherent_summary(cached):
        return cached

    # Miss, or a stale incoherent cached zero → recompute from all sources.
    graph = await _load_or_404(project_id, auth, db)

    # Wikidata source: the run's already-built Studio items (no rebuild).
    # Wikibase source: live SPARQL, only when configured. Either failing
    # never aborts the request — that source just contributes nothing.
    try:
        studio_items = await studio_items_for_project(run_ids, db)
    except Exception as exc:
        logger.warning("studio items unavailable for project %s: %s", project_id, exc)
        studio_items = []

    wikibase_entities = []
    if wikibase_url:
        try:
            wikibase_entities = await wikibase_provider(wikibase_url, run_wikibase_sparql)
        except Exception as exc:
            logger.warning("wikibase source unavailable for project %s: %s", project_id, exc)

    fresh = await asyncio.to_thread(
        compute_aggregated_summary, graph, studio_items, wikibase_entities,
        wikibase_configured=bool(wikibase_url),
    )

    # If the live graph itself is incoherent (triples but no entities), the
    # local TTL was likely seeded truncated during a bad window — re-seed it
    # from the authoritative rdf_artifacts copy and recompute once.
    if not _is_coherent_summary(fresh):
        await _restore_missing_ttls(run_ids, db, force=True)
        graph = await load_merged_graph(run_ids)
        fresh = await asyncio.to_thread(
            compute_aggregated_summary, graph, studio_items, wikibase_entities,
            wikibase_configured=bool(wikibase_url),
        )

    if _is_coherent_summary(fresh):
        await write_to_inference_cache(
            db, kind="research.summary", query_summary=cache_key,
            result=fresh, user_id=auth.user.id,
        )
    else:
        logger.warning(
            "research summary incoherent for project %s (triples=%s); "
            "not caching", project_id, fresh.get("triples"),
        )
    return fresh


@router.get("/projects/{project_id}/research/co-occurrence")
async def research_co_occurrence(
    project_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Pre-aggregated work co-occurrence graph.

    Returns {nodes: [{id, label, degree}], edges: [{work1, work2,
    shared_ms_count, ms_list}]}.  The frontend renders this directly
    without any further adjacency-building work.
    """
    graph = await _load_or_404(project_id, auth, db)
    return await asyncio.to_thread(query_co_occurrence, graph)


@router.get("/projects/{project_id}/research/people-network")
async def research_people_network(
    project_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Social network of scribes, authors, and owners."""
    graph = await _load_or_404(project_id, auth, db)
    return await asyncio.to_thread(query_people_network, graph)


@router.get("/projects/{project_id}/research/ownership")
async def research_ownership(
    project_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Per-manuscript ownership chains."""
    graph = await _load_or_404(project_id, auth, db)
    return await asyncio.to_thread(query_ownership_chains, graph)


@router.get("/projects/{project_id}/research/geography")
async def research_geography(
    project_id: uuid.UUID,
    mode: str | None = None,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Place associations with lat/lon for map rendering.

    ?mode=heatmap  → weighted points [{lat,lon,weight,type,place,place_label}]
    (default)      → per-place aggregated [{place,place_label,lat,lon,type,ms_count,ms_labels}]
    """
    graph = await _load_or_404(project_id, auth, db)
    if mode == "heatmap":
        return await asyncio.to_thread(query_geography_heatmap, graph)
    return await asyncio.to_thread(query_geography, graph)
