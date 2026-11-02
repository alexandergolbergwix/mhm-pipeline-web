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
from app.models.run import AuthorityMatch
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

_SUMMARY_ALGORITHM_VERSION = "linked-data-overview-v3"
_TTL_RESTORE_LOCK = asyncio.Lock()


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

    Rule W-246: select only (run_id, built_at, triples_count) — never call
    ``db.get(RdfArtifact, ...)``, which loads the multi-MB ``ttl_content``
    column on every cache check.
    """
    run_uuids = [uuid.UUID(r) for r in run_ids]
    meta_rows = (
        await db.execute(
            select(
                RdfArtifact.run_id,
                RdfArtifact.built_at,
                RdfArtifact.triples_count,
            ).where(RdfArtifact.run_id.in_(run_uuids))
        )
    ).all() if run_uuids else []
    meta_by_run = {str(r_id): (built_at, triples_count) for r_id, built_at, triples_count in meta_rows}

    parts: list[str] = []
    for run_id in sorted(run_ids):
        meta = meta_by_run.get(run_id)
        if meta is None:
            parts.append(f"{run_id}:none")
        else:
            built_at, triples_count = meta
            built = built_at.isoformat() if built_at else "?"
            parts.append(f"{run_id}:{built}:{triples_count or 0}")
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
    manuscripts = summary.get("total_manuscripts") or 0
    works = summary.get("total_works") or 0
    if triples > 0 and entities == 0:
        return False
    if triples > 0 and manuscripts == 0:
        return False
    # A large HMO graph always materialises F1 works. Zero works beside
    # manuscripts is the same vocab-miss as zero manuscripts (Rule W-230).
    if triples >= 1000 and manuscripts > 0 and works == 0:
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

    Uses a scalar query so SQLAlchemy does not retain the multi-MB
    ``RdfArtifact`` ORM instance in the session identity map, and a lock so
    concurrent requests on a cold dyno do not fetch and write the same file
    simultaneously.
    """
    if not force and all(rdf_output_path_for_run(r).exists() for r in run_ids):
        return
    async with _TTL_RESTORE_LOCK:
        for run_id in run_ids:
            ttl_path = rdf_output_path_for_run(run_id)
            if ttl_path.exists() and not force:
                continue
            ttl_content = (
                await db.execute(
                    select(RdfArtifact.ttl_content).where(RdfArtifact.run_id == uuid.UUID(run_id))
                )
            ).scalar_one_or_none()
            if ttl_content is None:
                continue
            ttl_path.parent.mkdir(parents=True, exist_ok=True)
            ttl_path.write_text(ttl_content, encoding="utf-8")


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
    # Rule W-40 / Rule W-285: release the pooled DB connection before
    # CPU-heavy rdflib parsing and SPARQL queries so concurrent requests
    # never starve the 1+2 QueuePool.
    await db.close()
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
    """Aggregate statistics: total manuscripts, works, persons, places."""
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
        await db.close()
        return cached

    # Fetch DB inputs BEFORE releasing the DB connection for rdflib/network work.
    try:
        studio_items = await studio_items_for_project(run_ids, db)
    except Exception as exc:
        logger.warning("studio items unavailable for project %s: %s", project_id, exc)
        studio_items = []

    authority_match_rows = await db.execute(
        select(
            AuthorityMatch.entity_kind,
            AuthorityMatch.entity_text,
            AuthorityMatch.matched_name,
            AuthorityMatch.role,
            AuthorityMatch.wikidata_qid,
            AuthorityMatch.viaf_id,
            AuthorityMatch.mazal_id,
        ).where(
            AuthorityMatch.run_id.in_([uuid.UUID(run_id) for run_id in run_ids]),
            AuthorityMatch.approved.is_(True),
        )
    )
    authority_matches = [
        {
            "entity_kind": r[0],
            "entity_text": r[1],
            "matched_name": r[2],
            "role": r[3],
            "wikidata_qid": r[4],
            "viaf_id": r[5],
            "mazal_id": r[6],
        }
        for r in authority_match_rows.all()
    ]

    await _restore_missing_ttls(run_ids, db)
    # Release the DB connection while loading the RDF graph, querying
    # Wikibase over HTTP, and running union-find aggregation in a thread.
    await db.close()

    graph = await load_merged_graph(run_ids)
    if len(graph) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No RDF data found — build the graph for each run first.",
        )

    wikibase_entities = []
    if wikibase_url:
        try:
            wikibase_entities = await wikibase_provider(wikibase_url, run_wikibase_sparql)
        except Exception as exc:
            logger.warning("wikibase source unavailable for project %s: %s", project_id, exc)

    fresh = await asyncio.to_thread(
        compute_aggregated_summary, graph, studio_items, wikibase_entities, authority_matches,
        wikibase_configured=bool(wikibase_url),
    )

    if not _is_coherent_summary(fresh):
        await _restore_missing_ttls(run_ids, db, force=True)
        await db.close()
        graph = await load_merged_graph(run_ids)
        fresh = await asyncio.to_thread(
            compute_aggregated_summary, graph, studio_items, wikibase_entities, authority_matches,
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
    """Pre-aggregated work co-occurrence graph."""
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
    """Place associations with lat/lon for map rendering."""
    graph = await _load_or_404(project_id, auth, db)
    if mode == "heatmap":
        return await asyncio.to_thread(query_geography_heatmap, graph)
    return await asyncio.to_thread(query_geography, graph)
