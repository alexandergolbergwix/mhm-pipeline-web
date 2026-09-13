"""Dispatch research-agent tools onto the existing research routers.

Every tool reuses SPARQL guards, membership checks, and query builders.
The Modal container only sees JSON results — never wiki passwords.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext
from app.models.project import Membership
from app.models.research_agent import ResearchAgentArtifact, ResearchAgentThread
from app.models.session import Session as SessionRow
from app.models.user import User
from app.pipeline.rdf_build import rdf_output_path_for_run
from app.services.research_agent.grants import GrantClaims

logger = logging.getLogger(__name__)

TOOL_NAMES = (
    "research_summary",
    "research_cooccurrence",
    "research_network",
    "research_ownership",
    "research_geography",
    "research_provenance",
    "research_movement_map",
    "research_manuscripts",
    "research_shortest_path",
    "research_neighbors",
    "research_sparql",
    "research_entity",
    "research_evidence",
    "fetch_rdf_ttl",
    "data_info",
    "data_select",
    "data_distinct",
    "data_search",
    "data_agg",
    "canvas_upsert_artifact",
    "canvas_list_versions",
    "create_download_link",
    "wikidata_entity",
    "wikibase_entity",
)

_SLIM_LIST_CAP = 40
# Data-skill output caps: keep every answer small enough for the planner
# context — extraction happens server-side, the planner sees slices only.
_DATASET_PREVIEW_ROWS = 5
_DATASET_ROW_LIMIT_DEFAULT = 20
_DATASET_ROW_LIMIT_MAX = 50
_DATASET_CELL_LIMIT = 200
_SPARQL_DIGEST_ROWS = 8


@dataclass
class ToolContext:
    db: AsyncSession
    claims: GrantClaims
    wiki_token: str | None
    auth: AuthContext
    thread: ResearchAgentThread


def slim_result(value: Any, *, cap: int = _SLIM_LIST_CAP) -> Any:
    """Cap large lists so the judge/agent prompt stays small."""
    if isinstance(value, list):
        truncated = len(value) > cap
        head = [slim_result(item, cap=cap) for item in value[:cap]]
        if truncated:
            return {"items": head, "truncated": True, "total": len(value)}
        return head
    if isinstance(value, dict):
        out = {k: slim_result(v, cap=cap) for k, v in value.items()}
        return out
    return value


async def _membership_role(db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID) -> str:
    row = await db.execute(
        select(Membership).where(
            Membership.project_id == project_id,
            Membership.user_id == user_id,
        )
    )
    membership = row.scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this project.")
    return membership.role


async def build_tool_context(
    db: AsyncSession,
    claims: GrantClaims,
    wiki_token: str | None,
) -> ToolContext:
    user = await db.get(User, claims.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Grant user is gone.")
    thread = await db.get(ResearchAgentThread, claims.thread_id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research thread not found.")
    if thread.project_id != claims.project_id or thread.user_id != claims.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Grant does not match thread.")
    await _membership_role(db, claims.project_id, claims.user_id)
    session_row = (
        await db.execute(
            select(SessionRow).where(SessionRow.user_id == user.id).limit(1)
        )
    ).scalar_one_or_none()
    if session_row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No live session for grant user.")
    auth = AuthContext(user=user, session=session_row, kek=b"\x00" * 32)
    return ToolContext(db=db, claims=claims, wiki_token=wiki_token, auth=auth, thread=thread)


async def dispatch_tool(ctx: ToolContext, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    arguments = arguments or {}
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown tool '{name}'.")
    result = await handler(ctx, arguments)
    if name in ("canvas_upsert_artifact", "canvas_list_versions", "create_download_link"):
        return result if isinstance(result, dict) else {"result": result}
    if name in ("research_movement_map", "research_sparql", "fetch_rdf_ttl", "data_info", "data_select", "data_distinct", "data_search", "data_agg"):
        return result if isinstance(result, dict) else {"result": result}
    slimmed = slim_result(result)
    return slimmed if isinstance(slimmed, dict) else {"result": slimmed}


async def _tool_summary(ctx: ToolContext, _args: dict[str, Any]) -> Any:
    from app.routers.research import research_summary
    return await research_summary(ctx.claims.project_id, ctx.auth, ctx.db)


async def _tool_cooccurrence(ctx: ToolContext, _args: dict[str, Any]) -> Any:
    from app.routers.research import research_co_occurrence
    return await research_co_occurrence(ctx.claims.project_id, ctx.auth, ctx.db)


async def _tool_network(ctx: ToolContext, _args: dict[str, Any]) -> Any:
    from app.routers.research import research_people_network
    return await research_people_network(ctx.claims.project_id, ctx.auth, ctx.db)


async def _tool_ownership(ctx: ToolContext, _args: dict[str, Any]) -> Any:
    from app.routers.research import research_ownership
    return await research_ownership(ctx.claims.project_id, ctx.auth, ctx.db)


async def _tool_geography(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.routers.research import research_geography
    mode = args.get("mode")
    return await research_geography(ctx.claims.project_id, mode, ctx.auth, ctx.db)


async def _tool_provenance(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.routers.research_provenance import get_provenance_timeline
    ms = str(args.get("ms") or "")
    if not ms:
        raise HTTPException(status_code=400, detail="ms (manuscript URI) is required.")
    overlay = args.get("overlay")
    return await get_provenance_timeline(ctx.claims.project_id, ms, overlay, ctx.auth, ctx.db)


async def _tool_movement_map(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.routers.research_provenance import get_provenance_map
    cn = str(args.get("cn") or args.get("control_number") or "")
    if not cn:
        raise HTTPException(status_code=400, detail="cn (control number) is required.")
    include_unapproved = bool(args.get("include_unapproved") or False)
    result = await get_provenance_map(
        ctx.claims.project_id, cn, include_unapproved, ctx.auth, ctx.db,
    )
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return result


async def _tool_manuscripts(ctx: ToolContext, _args: dict[str, Any]) -> Any:
    from app.routers.research_provenance import list_manuscripts
    rows = await list_manuscripts(ctx.claims.project_id, ctx.auth, ctx.db)
    return [r.model_dump() if hasattr(r, "model_dump") else r for r in rows]


async def _tool_path(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.pipeline.research_graph_ops import build_nx_graph, find_shortest_path
    from app.routers.research import _load_or_404
    import asyncio

    from_uri = str(args.get("from") or args.get("from_uri") or "")
    to_uri = str(args.get("to") or args.get("to_uri") or "")
    if not from_uri or not to_uri:
        raise HTTPException(status_code=400, detail="from and to URIs are required.")
    graph = await _load_or_404(ctx.claims.project_id, ctx.auth, ctx.db)
    nx_graph = await asyncio.to_thread(build_nx_graph, graph)
    return await asyncio.to_thread(find_shortest_path, nx_graph, from_uri, to_uri)


async def _tool_neighbors(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.pipeline.research_graph_ops import build_nx_graph, get_neighbors
    from app.routers.research import _load_or_404
    import asyncio

    uri = str(args.get("uri") or "")
    if not uri:
        raise HTTPException(status_code=400, detail="uri is required.")
    graph = await _load_or_404(ctx.claims.project_id, ctx.auth, ctx.db)
    nx_graph = await asyncio.to_thread(build_nx_graph, graph)
    return await asyncio.to_thread(get_neighbors, nx_graph, uri)


async def _tool_sparql(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.routers.linked_data_explorer import (
        SparqlRequest,
        sparql_hmo_graph,
        sparql_wikibase,
        sparql_wikidata,
    )

    from app.services.research_agent.sparql_templates import enforce_limit, render_template

    template_id = str(args.get("template_id") or "").strip()
    if template_id:
        query, source = render_template(template_id, args)
    else:
        query = str(args.get("query") or "")
        source = str(args.get("source") or "hmo").lower()
        if not query:
            raise HTTPException(status_code=400, detail="query or template_id is required.")
        query = enforce_limit(query, limit=args.get("limit"))
    body = SparqlRequest(query=query)
    if source == "wikidata":
        result = await sparql_wikidata(ctx.claims.project_id, body, ctx.auth, ctx.db)
    elif source == "wikibase":
        result = await sparql_wikibase(ctx.claims.project_id, body, ctx.auth, ctx.db)
    else:
        result = await sparql_hmo_graph(ctx.claims.project_id, body, ctx.auth, ctx.db)
    dumped = result.model_dump() if hasattr(result, "model_dump") else result
    dumped["source"] = source
    dumped["query"] = query

    # Always persist the raw result as a dataset artifact; the planner gets
    # a digest only and extracts facts with the data_* skills (Rule R24).
    rows = dumped.get("rows") or []
    columns = dumped.get("columns") or []
    if rows:
        key = "sparql-results"
        await upsert_artifact(
            ctx.db, ctx.thread,
            artifact_key=key,
            kind="sparql",
            title=f"SPARQL results ({source})",
            content={"columns": columns, "rows": rows, "truncated": dumped.get("truncated"), "query": query, "source": source},
            created_by="agent",
        )
        await ctx.db.commit()
    if len(rows) > _SPARQL_DIGEST_ROWS:
        return {
            "artifact_key": "sparql-results",
            "columns": columns,
            "row_count": len(rows),
            "truncated": dumped.get("truncated"),
            "preview_rows": [
                [_cell_str(v) for v in row] for row in rows[:_SPARQL_DIGEST_ROWS]
            ],
            "note": (
                "Full result saved as dataset artifact 'sparql-results'. "
                "Use data_info / data_select / data_distinct / data_search / "
                "data_agg with this artifact_key instead of re-running SPARQL."
            ),
        }
    return dumped


def _cell_str(value: Any, limit: int = _DATASET_CELL_LIMIT) -> str | None:
    text = "" if value is None else str(value)
    return text[:limit]


async def _tool_entity(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.routers.research_entity import get_entity_detail
    uri = str(args.get("uri") or "")
    if not uri:
        raise HTTPException(status_code=400, detail="uri is required.")
    result = await get_entity_detail(ctx.claims.project_id, uri, ctx.auth, ctx.db)
    return result.model_dump() if hasattr(result, "model_dump") else result


async def _tool_evidence(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.routers.research_evidence import get_evidence
    uri = str(args.get("uri") or "")
    if not uri:
        raise HTTPException(status_code=400, detail="uri is required.")
    result = await get_evidence(ctx.claims.project_id, uri, ctx.auth, ctx.db)
    return result.model_dump() if hasattr(result, "model_dump") else result


async def _tool_fetch_ttl(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.pipeline.rdf_build import ensure_ttl_on_disk

    run_id = ctx.claims.run_id
    raw = args.get("run_id")
    if raw:
        run_id = uuid.UUID(str(raw))
    if run_id is None:
        raise HTTPException(status_code=400, detail="run_id is required.")
    ttl = rdf_output_path_for_run(str(run_id))
    await ensure_ttl_on_disk(ttl, run_id, ctx.db)
    if not ttl.exists():
        raise HTTPException(status_code=404, detail="No RDF graph yet for this run.")
    text = ttl.read_text(encoding="utf-8")
    preview = text[:8000]
    return {
        "run_id": str(run_id),
        "bytes": len(text.encode("utf-8")),
        "preview": preview,
        "truncated": len(text) > 8000,
        "download_path": f"/api/runs/{run_id}/rdf/download.ttl",
    }


async def _next_version(db: AsyncSession, thread_id: uuid.UUID, artifact_key: str) -> int:
    rows = await db.execute(
        select(ResearchAgentArtifact.version).where(
            ResearchAgentArtifact.thread_id == thread_id,
            ResearchAgentArtifact.artifact_key == artifact_key,
        )
    )
    versions = [int(v) for v in rows.scalars().all()]
    return (max(versions) + 1) if versions else 1


async def upsert_artifact(
    db: AsyncSession,
    thread: ResearchAgentThread,
    *,
    artifact_key: str,
    kind: str,
    title: str,
    content: dict[str, Any],
    created_by: str,
) -> ResearchAgentArtifact:
    version = await _next_version(db, thread.id, artifact_key)
    row = ResearchAgentArtifact(
        thread_id=thread.id,
        artifact_key=artifact_key,
        kind=kind,
        title=title,
        version=version,
        content=content,
        created_by=created_by,
    )
    db.add(row)
    canvas = dict(thread.canvas_state or {})
    artifacts = list(canvas.get("artifacts") or [])
    entry = {
        "key": artifact_key,
        "kind": kind,
        "title": title,
        "version": version,
        "updated_by": created_by,
    }
    artifacts = [a for a in artifacts if a.get("key") != artifact_key]
    artifacts.append(entry)
    canvas["artifacts"] = artifacts
    canvas["active_key"] = artifact_key
    thread.canvas_state = canvas
    await db.flush()
    return row


async def _tool_canvas_upsert(ctx: ToolContext, args: dict[str, Any]) -> Any:
    key = str(args.get("artifact_key") or args.get("key") or "").strip()
    kind = str(args.get("kind") or "markdown").strip() or "markdown"
    title = str(args.get("title") or key or "Untitled")
    content = args.get("content") if isinstance(args.get("content"), dict) else {"text": str(args.get("text") or "")}
    created_by = str(args.get("created_by") or "agent")
    if not key:
        raise HTTPException(status_code=400, detail="artifact_key is required.")
    row = await upsert_artifact(
        ctx.db, ctx.thread,
        artifact_key=key, kind=kind, title=title, content=content, created_by=created_by,
    )
    await ctx.db.commit()
    await ctx.db.refresh(row)
    return {
        "id": str(row.id),
        "artifact_key": row.artifact_key,
        "kind": row.kind,
        "title": row.title,
        "version": row.version,
        "canvas_state": ctx.thread.canvas_state,
    }


async def _tool_canvas_list(ctx: ToolContext, args: dict[str, Any]) -> Any:
    key = str(args.get("artifact_key") or args.get("key") or "").strip()
    stmt = select(ResearchAgentArtifact).where(ResearchAgentArtifact.thread_id == ctx.thread.id)
    if key:
        stmt = stmt.where(ResearchAgentArtifact.artifact_key == key)
    stmt = stmt.order_by(ResearchAgentArtifact.artifact_key, ResearchAgentArtifact.version)
    rows = (await ctx.db.execute(stmt)).scalars().all()
    return {
        "artifacts": [
            {
                "id": str(r.id),
                "artifact_key": r.artifact_key,
                "kind": r.kind,
                "title": r.title,
                "version": r.version,
                "created_by": r.created_by,
            }
            for r in rows
        ],
        "canvas_state": ctx.thread.canvas_state,
    }


async def _tool_download_link(ctx: ToolContext, args: dict[str, Any]) -> Any:
    key = str(args.get("artifact_key") or args.get("key") or "").strip()
    fmt = str(args.get("format") or "json").lower()
    if not key:
        raise HTTPException(status_code=400, detail="artifact_key is required.")
    if fmt not in ("json", "csv", "md", "ttl", "bibtex", "ris"):
        raise HTTPException(status_code=400, detail="Unsupported download format.")
    path = (
        f"/api/research-agent/threads/{ctx.thread.id}/artifacts/{key}/download"
        f"?format={fmt}"
    )
    return {"download_path": path, "format": fmt, "artifact_key": key}


async def _tool_wikidata(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.services.research_agent.wiki import wikidata_entity
    qid = str(args.get("qid") or args.get("id") or "").strip()
    if not qid:
        raise HTTPException(status_code=400, detail="qid is required.")
    try:
        entity = await wikidata_entity(qid, ctx.wiki_token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Wikidata read failed: {exc}") from exc
    entity["authenticated"] = bool(ctx.wiki_token)
    entity["source"] = "wikidata"
    return entity


async def _tool_wikibase(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from app.services.research_agent.wiki import project_wikibase_entity, wikibase_item_url
    qid = str(args.get("qid") or args.get("id") or "").strip()
    if not qid:
        raise HTTPException(status_code=400, detail="qid is required.")
    try:
        entity = await project_wikibase_entity(qid, ctx.wiki_token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Wikibase read failed: {exc}") from exc
    entity["authenticated"] = bool(ctx.wiki_token)
    entity["source"] = "wikibase"
    entity["page_url"] = wikibase_item_url(qid)
    return entity


# ── Data skills: extract facts from stored dataset artifacts ────────────
# SPARQL (and other bulky) results are saved as dataset artifacts; these
# skills query them server-side so the planner only ever sees small slices.

async def _dataset_rows(
    ctx: ToolContext, args: dict[str, Any],
) -> tuple[str, list[str], list[list[Any]]]:
    key = str(args.get("artifact_key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="artifact_key is required.")
    row = (
        await ctx.db.execute(
            select(ResearchAgentArtifact)
            .where(
                ResearchAgentArtifact.thread_id == ctx.thread.id,
                ResearchAgentArtifact.artifact_key == key,
            )
            .order_by(ResearchAgentArtifact.version.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No dataset artifact '{key}' on this thread.")
    content = row.content if isinstance(row.content, dict) else {}
    columns = [str(c) for c in (content.get("columns") or [])]
    rows = content.get("rows")
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail=f"Artifact '{key}' holds no dataset rows.")
    return key, columns, rows


def _cap_rows(rows: list[list[Any]], limit: Any) -> list[list[str | None]]:
    try:
        n = max(1, min(int(limit), _DATASET_ROW_LIMIT_MAX))
    except (TypeError, ValueError):
        n = _DATASET_ROW_LIMIT_DEFAULT
    return [
        [_cell_str(v) for v in row]
        for row in rows[:n]
    ]


def _parse_limit(raw: Any) -> int:
    try:
        return max(1, min(int(raw), _DATASET_ROW_LIMIT_MAX))
    except (TypeError, ValueError):
        return _DATASET_ROW_LIMIT_DEFAULT


async def _tool_data_info(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    key, columns, rows = await _dataset_rows(ctx, args)
    return {
        "artifact_key": key,
        "columns": columns,
        "row_count": len(rows),
        "sample": _cap_rows(rows, _DATASET_PREVIEW_ROWS),
    }


async def _tool_data_select(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    key, columns, rows = await _dataset_rows(ctx, args)
    column = str(args.get("column") or "")
    op = str(args.get("op") or "eq")
    value = args.get("value")
    if not column:
        raise HTTPException(status_code=400, detail="column is required.")
    needle = _cell_str(value)
    values = [_cell_str(v) for v in value] if isinstance(value, list) else None
    out: list[list[Any]] = []
    col_idx = columns.index(column) if column in columns else -1
    for row in rows:
        cell = row[col_idx] if 0 <= col_idx < len(row) else None
        cell_text = _cell_str(cell)
        match = (
            (op == "eq" and cell_text == needle)
            or (op == "contains" and needle.lower() in (cell_text or "").lower())
            or (op == "in" and values is not None and cell_text in values)
        )
        if match:
            out.append(row)
    return {
        "artifact_key": key,
        "column": column,
        "op": op,
        "match_count": len(out),
        "rows": _cap_rows(out, args.get("limit")),
    }


async def _tool_data_distinct(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    key, columns, rows = await _dataset_rows(ctx, args)
    column = str(args.get("column") or "")
    if not column:
        raise HTTPException(status_code=400, detail="column is required.")
    col_idx = columns.index(column) if column in columns else -1
    counts: dict[str, int] = {}
    for row in rows:
        cell = _cell_str(row[col_idx]) if 0 <= col_idx < len(row) else None
        counts[cell or ""] = counts.get(cell or "", 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    return {
        "artifact_key": key,
        "column": column,
        "distinct_count": len(counts),
        "values": [{"value": v, "count": c} for v, c in ranked],
    }


async def _tool_data_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    key, _columns, rows = await _dataset_rows(ctx, args)
    needle = _cell_str(args.get("needle") or "").lower()
    if not needle:
        raise HTTPException(status_code=400, detail="needle is required.")
    out = [
        row for row in rows
        if any(needle in (_cell_str(cell) or "").lower() for cell in row)
    ]
    return {
        "artifact_key": key,
        "needle": needle,
        "match_count": len(out),
        "rows": _cap_rows(out, args.get("limit")),
    }


async def _tool_data_agg(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    key, columns, rows = await _dataset_rows(ctx, args)
    column = str(args.get("column") or "")
    fn = str(args.get("fn") or "count")
    if not column:
        raise HTTPException(status_code=400, detail="column is required.")
    if fn not in ("count", "min", "max", "sum", "avg"):
        raise HTTPException(status_code=400, detail="fn must be count, min, max, sum, or avg.")
    col_idx = columns.index(column) if column in columns else -1
    cells = [
        _cell_str(row[col_idx]) for row in rows
        if 0 <= col_idx < len(row) and row[col_idx] is not None
    ]
    if fn == "count":
        return {"artifact_key": key, "column": column, "fn": fn, "result": len(cells)}
    numbers: list[float] = []
    for cell in cells:
        try:
            numbers.append(float(str(cell).replace(",", "")))
        except ValueError:
            continue
    if not numbers:
        return {"artifact_key": key, "column": column, "fn": fn, "result": None}
    result: float | None
    if fn == "min":
        result = min(numbers)
    elif fn == "max":
        result = max(numbers)
    elif fn == "sum":
        result = sum(numbers)
    else:
        result = sum(numbers) / len(numbers)
    return {"artifact_key": key, "column": column, "fn": fn, "numeric_cells": len(numbers), "result": result}


TOOL_HANDLERS = {
    "research_summary": _tool_summary,
    "research_cooccurrence": _tool_cooccurrence,
    "research_network": _tool_network,
    "research_ownership": _tool_ownership,
    "research_geography": _tool_geography,
    "research_provenance": _tool_provenance,
    "research_movement_map": _tool_movement_map,
    "research_manuscripts": _tool_manuscripts,
    "research_shortest_path": _tool_path,
    "research_neighbors": _tool_neighbors,
    "research_sparql": _tool_sparql,
    "research_entity": _tool_entity,
    "research_evidence": _tool_evidence,
    "fetch_rdf_ttl": _tool_fetch_ttl,
    "data_info": _tool_data_info,
    "data_select": _tool_data_select,
    "data_distinct": _tool_data_distinct,
    "data_search": _tool_data_search,
    "data_agg": _tool_data_agg,
    "canvas_upsert_artifact": _tool_canvas_upsert,
    "canvas_list_versions": _tool_canvas_list,
    "create_download_link": _tool_download_link,
    "wikidata_entity": _tool_wikidata,
    "wikibase_entity": _tool_wikibase,
}


def artifact_export_bytes(kind: str, content: dict[str, Any], fmt: str) -> tuple[bytes, str, str]:
    """Return (body, media_type, filename) for a canvas artifact."""
    if fmt == "md":
        text = str(content.get("text") or json.dumps(content, ensure_ascii=False, indent=2))
        return text.encode("utf-8"), "text/markdown; charset=utf-8", "artifact.md"
    if fmt == "ttl":
        text = str(content.get("text") or content.get("preview") or "")
        return text.encode("utf-8"), "text/turtle; charset=utf-8", "artifact.ttl"
    if fmt == "csv":
        columns = content.get("columns") or []
        rows = content.get("rows") or []
        if not columns and isinstance(content.get("items"), list):
            items = content["items"]
            if items and isinstance(items[0], dict):
                columns = list(items[0].keys())
                rows = [[str(it.get(c, "")) for c in columns] for it in items]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([str(c) for c in columns])
        for row in rows:
            writer.writerow(["" if cell is None else str(cell) for cell in row])
        return buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8", "artifact.csv"
    if fmt == "bibtex":
        text = str(content.get("bibtex") or json.dumps(content, ensure_ascii=False, indent=2))
        return text.encode("utf-8"), "application/x-bibtex", "artifact.bib"
    if fmt == "ris":
        text = str(content.get("ris") or "")
        return text.encode("utf-8"), "application/x-research-info-systems", "artifact.ris"
    body = json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
    return body, "application/json; charset=utf-8", "artifact.json"
