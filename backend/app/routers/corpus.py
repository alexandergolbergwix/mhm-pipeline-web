"""Cross-project corpus-wide SPARQL endpoint (Phase C / Feature 7).

POST /api/research/corpus/sparql
  Body: {query: str}
  → {columns: [...], rows: [...]}   same shape as project-scoped SPARQL

Federates over ALL projects the authenticated user is a member of.
Each result row has an extra `_source_project` column (UUID string)
indicating which project graph the row came from.

Same read-only guard and 1000-row/30-second caps as the per-project
SPARQL endpoint.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.project import Membership
from app.models.run import Run
from app.pipeline.research_graph import load_merged_graph
from app.pipeline.rdf_build import rdf_output_path_for_run
from app.models.rdf_artifact import RdfArtifact
from app.routers.linked_data_explorer import _validate_query, _execute_rdflib_query

import uuid as uuid_mod

router = APIRouter(tags=["corpus"])
logger = logging.getLogger(__name__)

_MAX_ROWS = 1000


class CorpusSparqlRequest(BaseModel):
    query: str


async def _run_ids_for_project(project_id: uuid_mod.UUID, db: AsyncSession) -> list[str]:
    rows = await db.execute(select(Run.id).where(Run.project_id == project_id))
    return [str(r) for r in rows.scalars().all()]


async def _restore_missing_ttls(run_ids: list[str], db: AsyncSession) -> None:
    for run_id in run_ids:
        ttl_path = rdf_output_path_for_run(run_id)
        if ttl_path.exists():
            continue
        row = await db.get(RdfArtifact, uuid_mod.UUID(run_id))
        if row is None:
            continue
        ttl_path.parent.mkdir(parents=True, exist_ok=True)
        ttl_path.write_text(row.ttl_content, encoding="utf-8")


async def _member_project_ids(user_id: uuid_mod.UUID, db: AsyncSession) -> list[uuid_mod.UUID]:
    rows = await db.execute(
        select(Membership.project_id).where(Membership.user_id == user_id)
    )
    return list(rows.scalars().all())


def _execute_corpus_query(
    graphs: list[tuple[str, Any]],
    query: str,
) -> dict[str, Any]:
    """Run *query* against each (project_id_str, rdflib.Graph) pair and merge results.

    Returns {columns: [...], rows: [...]}.
    Rows are plain dicts (keyed by column name + '_source_project') so callers
    can access them by name rather than by position index.
    """
    all_rows: list[dict[str, Any]] = []
    columns_seen: list[str] = []

    for project_id_str, graph in graphs:
        if len(graph) == 0:
            continue
        try:
            response = _execute_rdflib_query(graph, query)
        except Exception as exc:
            logger.warning("Corpus SPARQL error on project %s: %s", project_id_str, exc)
            continue

        if not columns_seen:
            columns_seen = list(response.columns)

        for row_list in response.rows:
            row_dict: dict[str, Any] = {}
            for col, val in zip(response.columns, row_list):
                row_dict[col] = val
            row_dict["_source_project"] = project_id_str
            all_rows.append(row_dict)

        if len(all_rows) >= _MAX_ROWS:
            all_rows = all_rows[:_MAX_ROWS]
            break

    final_columns = columns_seen + (["_source_project"] if "_source_project" not in columns_seen else [])

    return {"columns": final_columns, "rows": all_rows}


@router.post("/research/corpus/sparql")
async def corpus_sparql(
    body: CorpusSparqlRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Execute a read-only SPARQL query across all projects the user is a member of."""
    _validate_query(body.query)

    project_ids = await _member_project_ids(auth.user.id, db)

    if not project_ids:
        return {"columns": [], "rows": []}

    # Load each project's graph
    graphs: list[tuple[str, Any]] = []
    for project_id in project_ids:
        run_ids = await _run_ids_for_project(project_id, db)
        if not run_ids:
            continue
        await _restore_missing_ttls(run_ids, db)
        try:
            graph = await load_merged_graph(run_ids)
            graphs.append((str(project_id), graph))
        except Exception as exc:
            logger.warning("Could not load graph for project %s: %s", project_id, exc)

    if not graphs:
        return {"columns": [], "rows": []}

    return await asyncio.to_thread(_execute_corpus_query, graphs, body.query)
