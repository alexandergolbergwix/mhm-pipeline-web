"""Research Explorer — analytics endpoints.

Each endpoint merges all run-level TTL files for a project, then runs a
pre-defined SPARQL query over the merged graph.

All endpoints require project viewer access.
"""
from __future__ import annotations

import asyncio
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
from app.pipeline.research_graph import load_merged_graph
from app.pipeline.rdf_build import rdf_output_path_for_run
from app.pipeline.research_queries import (
    query_co_occurrence,
    query_geography,
    query_ownership_chains,
    query_people_network,
    query_summary,
)

router = APIRouter(tags=["research"])
logger = logging.getLogger(__name__)


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


async def _restore_missing_ttls(run_ids: list[str], db: AsyncSession) -> None:
    """Restore any TTL files missing from the local cache by reading rdf_artifacts.

    Heroku dynos have an ephemeral filesystem — after a deploy or restart the
    per-run manuscripts.ttl files are gone. This seeds them back from Postgres
    so research queries see the same data without requiring a full rebuild.
    """
    for run_id in run_ids:
        ttl_path = rdf_output_path_for_run(run_id)
        if ttl_path.exists():
            continue
        row = await db.get(RdfArtifact, uuid.UUID(run_id))
        if row is None:
            continue
        ttl_path.parent.mkdir(parents=True, exist_ok=True)
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
    """Aggregate statistics: total manuscripts, works, persons, places."""
    graph = await _load_or_404(project_id, auth, db)
    return await asyncio.to_thread(query_summary, graph)


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
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Place associations with lat/lon for map rendering."""
    graph = await _load_or_404(project_id, auth, db)
    return await asyncio.to_thread(query_geography, graph)
