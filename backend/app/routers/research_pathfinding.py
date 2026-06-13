"""Relationship drill / path-finding endpoints (Phase C / Feature 6).

GET /api/projects/{project_id}/research/neighbors?uri=<uri>
  → [{uri, label, type, edge_type}]

GET /api/projects/{project_id}/research/path?from=<uri>&to=<uri>
  → {path: [{uri, label, type}], edges: [{source, target, label}]}

Note: FastAPI's path param naming cannot use the bare Python keyword ``from``,
so the `path` endpoint reads ``from`` directly from ``request.query_params``.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.pipeline.research_graph_ops import build_nx_graph, find_shortest_path, get_neighbors
from app.routers.research import _load_or_404

router = APIRouter(tags=["research"])


@router.get("/projects/{project_id}/research/neighbors")
async def research_neighbors(
    project_id: uuid.UUID,
    uri: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Immediate neighbors of *uri* in the project's entity graph."""
    graph = await _load_or_404(project_id, auth, db)
    G = await asyncio.to_thread(build_nx_graph, graph)
    return await asyncio.to_thread(get_neighbors, G, uri)


@router.get("/projects/{project_id}/research/path")
async def research_path(
    request: Request,
    project_id: uuid.UUID,
    to: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Shortest path between two URIs in the project's entity graph.

    Query params: ?from=<uri>&to=<uri>
    """
    from_uri = request.query_params.get("from", "")
    graph = await _load_or_404(project_id, auth, db)
    G = await asyncio.to_thread(build_nx_graph, graph)
    return await asyncio.to_thread(find_shortest_path, G, from_uri, to)
