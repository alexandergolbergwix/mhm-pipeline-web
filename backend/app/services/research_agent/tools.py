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
from app.models.run import Run
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
    "wikidata_uploaded_items",
    "wikidata_fetch_items",
    "show_movement_map",
    "show_link_types",
    "show_wikidata_places",
    "wikidata_pack",
    "export_pdf",
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
    if name in ("research_movement_map", "research_sparql", "fetch_rdf_ttl", "data_info", "data_select", "data_distinct", "data_search", "data_agg", "wikidata_uploaded_items", "wikidata_fetch_items", "show_movement_map", "show_link_types", "show_wikidata_places", "wikidata_pack", "export_pdf"):
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
    if fmt not in ("json", "csv", "md", "ttl", "bibtex", "ris", "pdf"):
        raise HTTPException(status_code=400, detail="Unsupported download format.")
    path = (
        f"/api/research-agent/threads/{ctx.thread.id}/artifacts/{key}/download"
        f"?format={fmt}"
    )
    return {"download_path": path, "format": fmt, "artifact_key": key}


# ── Visualization + export skills (Rule R25) ─────────────────────────────

async def _tool_show_movement_map(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Put the provenance movement map on the canvas and return a digest."""
    from app.routers.research_provenance import (
        get_provenance_map,
        list_manuscripts,
    )

    cn = str(args.get("cn") or args.get("control_number") or "").strip()
    include_unapproved = bool(args.get("include_unapproved") or False)

    if cn:
        result = await get_provenance_map(
            ctx.claims.project_id, cn, include_unapproved, ctx.auth, ctx.db,
        )
        data = result.model_dump() if hasattr(result, "model_dump") else result
        label = str(data.get("ms_label") or cn)
        stops = data.get("stops") or []
        places = [
            str(s.get("label") or "") for s in stops
            if s.get("lat") is not None and s.get("lon") is not None
        ]
        years = [s["year"] for s in stops if s.get("year") is not None]
        content = {
            "cn": cn,
            "ms_label": label,
            "stop_count": len(stops),
            "places": places[:40],
            "dropped": len(data.get("dropped") or []),
        }
        title = f"Movement map: {label}"[:80]
    else:
        rows = await list_manuscripts(ctx.claims.project_id, ctx.auth, ctx.db)
        picks = [
            r.model_dump() if hasattr(r, "model_dump") else r for r in rows
        ]
        content = {
            "cn": None,
            "ms_label": "Corpus movement map",
            "manuscript_count": len(picks),
            "manuscripts": [
                {
                    "cn": p.get("control_number"),
                    "label": _cell_str(p.get("label"), 120),
                    "production_year": p.get("production_year"),
                }
                for p in picks[:40]
            ],
        }
        title = "Movement map (corpus)"[:80]

    await upsert_artifact(
        ctx.db, ctx.thread,
        artifact_key="movement-map",
        kind="map",
        title=title,
        content=content,
        created_by="agent",
    )
    await ctx.db.commit()
    digest = {"artifact_key": "movement-map", "kind": "map", **content}
    digest["note"] = (
        "Movement map placed on the canvas (Movement map tab). "
        "Summarize the places; do not re-fetch the full map data."
    )
    return digest


async def _tool_export_pdf(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Render a saved artifact as a PDF and return its download path."""
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
        raise HTTPException(status_code=404, detail=f"No artifact '{key}' on this thread.")
    # Fail early if PDF rendering is impossible (fonts, fpdf missing).
    content = row.content if isinstance(row.content, dict) else {}
    text = str(content.get("text") or json.dumps(content, ensure_ascii=False, indent=2))
    from app.services.research_agent.pdf import render_pdf

    render_pdf(str(row.title or key), text)
    path = (
        f"/api/research-agent/threads/{ctx.thread.id}/artifacts/{key}/download"
        f"?format=pdf"
    )
    return {
        "artifact_key": key,
        "title": row.title,
        "download_path": path,
        "note": "PDF ready at download_path (cookie-authenticated).",
    }


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


# ── Wikidata upload skills (Rule R26) ────────────────────────────────────
# Questions about "items uploaded to Wikidata" MUST come from the DB
# publication/Studio records, then live Wikidata API — never from guessing
# at SPARQL sources.

_WIKIDATA_UPLOADS_KEY = "wikidata-uploads"
_WIKIDATA_ITEMS_KEY = "wikidata-items"


async def _tool_wikidata_uploaded_items(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    """Save every uploaded Wikidata item as a dataset.

    Sources, in order of authority: (1) succeeded publication execution
    actions (what actually reached Wikidata), then (2) the project's
    Studio caches — both `legacy` and `canonical`, any approval flag —
    for items carrying `existing_qid`. Reading only one cache source
    misses uploads (that exact bug returned 0 for a project with 231
    live QIDs), so all three are merged here (Rule R26).
    """
    from app.models.publication import (
        Publication,
        PublicationExecution,
        PublicationExecutionAction,
    )
    from app.routers.wikidata_studio import studio_items_for_project

    qid_row: dict[str, dict[str, Any]] = {}

    run_rows = (
        await ctx.db.execute(
            select(Run.id).where(Run.project_id == ctx.claims.project_id)
        )
    ).scalars().all()
    # asyncpg returns pgproto.UUID, and uuid.UUID(pgproto.UUID) raises
    # AttributeError ('replace') — consumers expect str run ids.
    project_run_ids = [str(r) for r in run_rows]

    if project_run_ids:
        succeeded = (
            await ctx.db.execute(
                select(
                    PublicationExecutionAction.result_qid,
                    PublicationExecutionAction.entity_key,
                    PublicationExecutionAction.action,
                )
                .join(PublicationExecution, PublicationExecution.id == PublicationExecutionAction.execution_id)
                .join(Publication, Publication.id == PublicationExecution.publication_id)
                .join(Run, Run.id == Publication.run_id)
                .where(
                    Run.project_id == ctx.claims.project_id,
                    PublicationExecutionAction.state == "succeeded",
                    PublicationExecutionAction.result_qid.is_not(None),
                )
            )
        ).all()
        for qid, entity_key, action in succeeded:
            qid_row.setdefault(str(qid), {
                "qid": str(qid),
                "local_id": _cell_str(entity_key, 120),
                "entity_type": None,
                "label": None,
                "sources": {f"publication:{action}"},
            })

    for source in ("legacy", "canonical"):
        if not project_run_ids:
            break
        from app.routers.wikidata_studio import studio_items_for_project as _items

        items = await _items(project_run_ids, ctx.db, approved_only=False, source=source)
        for it in items:
            qid = str(it.get("existing_qid") or "").strip()
            if not qid:
                continue
            labels = it.get("labels") or {}
            entry = qid_row.setdefault(str(qid), {
                "qid": qid,
                "local_id": _cell_str(it.get("local_id"), 120),
                "entity_type": _cell_str(it.get("entity_type"), 40),
                "label": _cell_str((labels.get("en") or labels.get("he") or ""), 160),
                "statements": len(it.get("statements") or []),
                "sources": set(),
            })
            entry.setdefault("sources", set())
            if isinstance(entry.get("sources"), set):
                entry["sources"].add(f"studio:{source}")

    rows: list[list[str | None]] = []
    for entry in sorted(qid_row.values(), key=lambda e: e["qid"]):
        sources = entry.get("sources") or {"publication"}
        source_text = "+".join(sorted(s for s in sources if s)) or "publication"
        statements = entry.get("statements")
        rows.append([
            entry["qid"], entry.get("local_id"), entry.get("entity_type"),
            entry.get("label"), statements if statements is not None else None,
            _cell_str(source_text, 60),
        ])

    await upsert_artifact(
        ctx.db, ctx.thread,
        artifact_key=_WIKIDATA_UPLOADS_KEY,
        kind="sparql",
        title="Uploaded to Wikidata",
        content={
            "columns": ["qid", "local_id", "entity_type", "label", "statements", "source"],
            "rows": rows,
        },
        created_by="agent",
    )
    await ctx.db.commit()
    by_type: dict[str, int] = {}
    for row in rows:
        t = row[2] or "unknown"
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "artifact_key": _WIKIDATA_UPLOADS_KEY,
        "uploaded_count": len(rows),
        "by_type": by_type,
        "preview": _cap_rows(rows, _SPARQL_DIGEST_ROWS),
        "note": (
            "Saved as dataset artifact 'wikidata-uploads'. Use "
            "wikidata_fetch_items to pull live claims, then the data_* "
            "skills on 'wikidata-items' to analyze links."
        ),
    }


async def _tool_wikidata_fetch_items(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Fetch live Wikidata entities for the uploaded QIDs; save one row per claim."""
    from app.services.research_agent.wiki import fetch_wikidata_entities_batch

    key = str(args.get("artifact_key") or _WIKIDATA_UPLOADS_KEY).strip()
    _, columns, rows = await _dataset_rows(ctx, {"artifact_key": key})
    if "qid" not in columns:
        raise HTTPException(status_code=400, detail=f"Artifact '{key}' has no qid column.")
    qcol = columns.index("qid")
    qids: list[str] = []
    for row in rows:
        qid = _cell_str(row[qcol]) if 0 <= qcol < len(row) else None
        if qid and qid not in qids:
            qids.append(qid)
    qids = qids[:300]
    if not qids:
        raise HTTPException(status_code=404, detail=f"No QIDs stored in '{key}'. Run wikidata_uploaded_items first.")

    entities = await fetch_wikidata_entities_batch(qids, bot_token=ctx.wiki_token)
    ours = set(qids)  # every fetched QID is one of this project's uploads
    out: list[list[str | None]] = []
    for qid in qids:
        entity = entities.get(qid) or {}
        label = (entity.get("labels") or {}).get("en") or (entity.get("labels") or {}).get("he") or ""
        claim_values = entity.get("claim_values") or {}
        if entity.get("missing") or not (claim_values or entity.get("claim_properties")):
            out.append([qid, _cell_str(label, 160), None, "missing or no claims", ""])
            continue
        for prop, values in (claim_values or {}).items():
            for value in values:
                out.append([
                    qid, _cell_str(label, 160), _cell_str(prop, 24), value[:200],
                    "ours" if value[:200] in ours else "",
                ])
    await upsert_artifact(
        ctx.db, ctx.thread,
        artifact_key=_WIKIDATA_ITEMS_KEY,
        kind="sparql",
        title="Wikidata items (live claims)",
        content={
            "columns": ["qid", "label", "property", "value", "target_is_ours"],
            "rows": out,
        },
        created_by="agent",
    )
    await ctx.db.commit()
    prop_counts: dict[str, int] = {}
    internal_links = 0
    for row in out:
        if row[2]:
            prop_counts[str(row[2])] = prop_counts.get(str(row[2]), 0) + 1
        if row[4] == "ours":
            internal_links += 1
    top = sorted(prop_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
    return {
        "artifact_key": _WIKIDATA_ITEMS_KEY,
        "fetched": len(qids),
        "claim_rows": len(out),
        "distinct_properties": len(prop_counts),
        "internal_links": internal_links,
        "top_properties": top,
        "note": (
            "Live Wikidata claims with real datavalues saved as dataset artifact "
            "'wikidata-items' (target_is_ours marks claims pointing at our own "
            "uploaded QIDs). Analyze with data_distinct / data_select / data_search."
        ),
    }


async def _tool_show_link_types(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Aggregate the 'wikidata-items' claim dataset into a link-type chart.

    Reads the saved claim rows server-side, groups every distinct property
    into a family (Wikidata P-properties, MHM ontology, FRBRoo/LRMoo,
    CIDOC CRM, RDF/RDFS, other) and places a bar-chart artifact on the
    canvas (Rule R25 — the planner receives only a digest).
    """
    key = str(args.get("artifact_key") or _WIKIDATA_ITEMS_KEY).strip()
    _, columns, rows = await _dataset_rows(ctx, {"artifact_key": key})
    prop_col = "property" if "property" in columns else ("p" if "p" in columns else None)
    if prop_col is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Dataset '{key}' has no property column. Run "
                "wikidata_fetch_items first (or research_sparql against the "
                "wikidata source) to save claim properties."
            ),
        )
    p_idx = columns.index(prop_col)
    q_idx = columns.index("qid") if "qid" in columns else None
    v_idx = columns.index("value") if "value" in columns else None
    ours_idx = columns.index("target_is_ours") if "target_is_ours" in columns else None
    counts: dict[str, int] = {}
    internal_counts: dict[str, int] = {}
    qids: set[str] = set()
    for row in rows:
        prop = _cell_str(row[p_idx]) if 0 <= p_idx < len(row) else None
        if not prop:
            continue
        counts[prop] = counts.get(prop, 0) + 1
        if q_idx is not None and 0 <= q_idx < len(row):
            qids.add(_cell_str(row[q_idx]) or "")
        if (
            ours_idx is not None and 0 <= ours_idx < len(row)
            and _cell_str(row[ours_idx]) == "ours"
        ):
            internal_counts[prop] = internal_counts.get(prop, 0) + 1
    qids.discard("")
    if not counts:
        raise HTTPException(status_code=404, detail=f"Dataset '{key}' has no claim rows.")

    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:30]
    groups: dict[str, list[dict[str, Any]]] = {}
    for prop, count in top:
        groups.setdefault(_link_family(prop), []).append({
            "label": _link_label(prop),
            "count": count,
            "internal": internal_counts.get(prop, 0),
        })
    internal_total = sum(internal_counts.values())
    content = {
        "chart": "bar",
        "title": str(args.get("title") or "Types of links on our uploaded Wikidata items")[:120],
        "total_claims": sum(counts.values()),
        "distinct_properties": len(counts),
        "items_counted": len(qids),
        "internal_links": internal_total,
        "groups": [
            {"name": name, "items": items}
            for name, items in sorted(groups.items(), key=lambda kv: -sum(i["count"] for i in kv[1]))
        ],
    }
    await upsert_artifact(
        ctx.db, ctx.thread,
        artifact_key="link-types",
        kind="chart",
        title=content["title"],
        content=content,
        created_by="agent",
    )
    await ctx.db.commit()
    digest = [
        {"label": item["label"], "count": item["count"], "internal": item["internal"]}
        for group in content["groups"] for item in group["items"][:8]
    ]
    return {
        "artifact_key": "link-types",
        "claim_rows": content["total_claims"],
        "distinct_properties": content["distinct_properties"],
        "items_counted": content["items_counted"],
        "internal_links": internal_total,
        "groups": [
            {"name": g["name"], "properties": len(g["items"]), "claims": sum(i["count"] for i in g["items"])}
            for g in content["groups"]
        ],
        "top_links": digest,
        "note": "Placed the link-type chart on the canvas as 'link-types'. Use data_* skills on '" + key + "' for details.",
    }


async def _wikidata_sparql_query(query: str) -> dict[str, Any]:
    """Run one SELECT against the public Wikidata query service."""
    import httpx

    resp = await httpx.AsyncClient(timeout=30.0).get(
        "https://query.wikidata.org/sparql",
        params={"query": query, "format": "json"},
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "MHM-Pipeline-Web/1.0 (research-agent; contact via project admin)",
        },
    )
    resp.raise_for_status()
    return resp.json()


_PACK_DIGEST_SKIP = {"preview", "sample", "rows", "values"}


async def _tool_wikidata_pack(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """One round-trip: refresh the claim dataset, then place the link-type
    chart AND the place-mentions map.

    The planner would otherwise spend three tool turns (fetch → chart →
    map) and pay a model round-trip between each; the pack runs the same
    three handlers server-side and returns compact digests (Rule R25).
    Steps that fail are reported per step — a missing dataset 404s only
    that step, not the pack.
    """
    steps: dict[str, Any] = {}
    errors: dict[str, str] = {}
    plan = (
        ("wikidata_fetch_items", {"artifact_key": str(args.get("artifact_key") or "wikidata-uploads")}),
        ("show_link_types", {}),
        ("show_wikidata_places", {}),
    )
    for name, tool_args in plan:
        handler = TOOL_HANDLERS[name]
        try:
            result = await handler(ctx, tool_args)
            steps[name] = {
                k: v for k, v in result.items()
                if k not in _PACK_DIGEST_SKIP and not isinstance(v, (list, dict))
            }
        except HTTPException as exc:
            errors[name] = str(exc.detail)
    if not steps and errors:
        raise HTTPException(status_code=400, detail="; ".join(f"{k}: {v}" for k, v in errors.items()))
    return {
        "pack": "wikidata",
        "steps": steps,
        "errors": errors,
        "note": (
            "Refreshed claims and placed both the link-type chart "
            "('link-types') and the place-mentions map ('wikidata-places') "
            "on the canvas. Per-step errors appear under 'errors'."
        ),
    }


async def _tool_show_wikidata_places(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Plot every place-valued claim on our uploaded items on an interactive map.

    Reads the source QIDs from the claim dataset, runs one read-only WDQS
    query for claims whose object carries P625 coordinates, and places a
    point-map artifact where each popup links to the Wikidata entities
    (Rule R25 — the planner receives only a digest).
    """
    import re

    key = str(args.get("artifact_key") or _WIKIDATA_ITEMS_KEY).strip()
    _, columns, rows = await _dataset_rows(ctx, {"artifact_key": key})
    if "qid" not in columns:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{key}' has no qid column. Run wikidata_fetch_items first.",
        )
    q_idx = columns.index("qid")
    qids: list[str] = []
    for row in rows:
        qid = _cell_str(row[q_idx]) if 0 <= q_idx < len(row) else None
        if qid and qid.startswith("Q") and qid not in qids:
            qids.append(qid)
    qids = qids[:150]
    if not qids:
        raise HTTPException(status_code=404, detail=f"No QIDs stored in '{key}'. Run wikidata_fetch_items first.")

    values = " ".join(f"wd:{q}" for q in qids)
    query = (
        "SELECT ?item ?itemLabel ?prop ?propLabel ?place ?placeLabel ?coord WHERE {"
        f" VALUES ?item {{ {values} }}"
        " ?item ?p ?place ."
        " ?place wdt:P625 ?coord ."
        " ?prop wikibase:directClaim ?p ."
        ' SERVICE wikibase:label { bd:serviceParam wikibase:language "en,he". }'
        "} LIMIT 3000"
    )
    data = await _wikidata_sparql_query(query)
    coord_re = re.compile(r"Point\(([-\d.]+) ([-\d.]+)\)")
    # Location-semantics properties only. Without this filter, ANY claim
    # whose target happens to carry P625 matches (e.g. P407 language of
    # work → an entity with stray coordinates) and pollutes the map
    # (production feedback 2026-09-14: "Modern Greek" dots). P195
    # (collection) stays in: holding institutions are the dominant real
    # "location" lens in the manuscript claims — dropping it collapsed
    # the map to 2 dots.
    _PLACE_PROPS = {
        "P17", "P131", "P159", "P189", "P276", "P706", "P1071", "P495", "P5566",
        "P195",
    }
    points: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for binding in data.get("results", {}).get("bindings", []):
        prop = str(binding.get("prop", {}).get("value") or "").rsplit("/", 1)[-1]
        if prop not in _PLACE_PROPS:
            continue
        coord = str(binding.get("coord", {}).get("value") or "")
        match = coord_re.search(coord)
        if not match:
            continue
        lon, lat = float(match.group(1)), float(match.group(2))
        item_uri = str(binding.get("item", {}).get("value") or "")
        place_uri = str(binding.get("place", {}).get("value") or "")
        item_qid = item_uri.rsplit("/", 1)[-1]
        place_qid = place_uri.rsplit("/", 1)[-1]
        dedupe = (item_qid, place_qid)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        points.append({
            "item_qid": item_qid,
            "item_label": str(binding.get("itemLabel", {}).get("value") or item_qid)[:160],
            "property": str(binding.get("prop", {}).get("value") or "").rsplit("/", 1)[-1],
            "property_label": str(binding.get("propLabel", {}).get("value") or "")[:80],
            "place_qid": place_qid,
            "place_label": str(binding.get("placeLabel", {}).get("value") or place_qid)[:160],
            "lat": lat,
            "lon": lon,
        })
    if not points:
        raise HTTPException(
            status_code=404,
            detail="No place-valued claims with coordinates found for this project's items.",
        )
    content = {
        "map": "points",
        "title": str(args.get("title") or "Places mentioned in our Wikidata items")[:120],
        "points": points,
    }
    await upsert_artifact(
        ctx.db, ctx.thread,
        artifact_key="wikidata-places",
        kind="map",
        title=content["title"],
        content=content,
        created_by="agent",
    )
    await ctx.db.commit()
    place_counts: dict[str, int] = {}
    for point in points:
        place_counts[point["place_label"]] = place_counts.get(point["place_label"], 0) + 1
    top_places = sorted(place_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    return {
        "artifact_key": "wikidata-places",
        "point_count": len(points),
        "distinct_places": len(place_counts),
        "items_with_places": len({p["item_qid"] for p in points}),
        "top_places": [{"place": name, "claims": count} for name, count in top_places],
        "note": (
            "Placed the place-mentions map on the canvas as 'wikidata-places' — "
            "each popup links to the item and the place on Wikidata. It shows "
            "only place-valued claims with P625 coordinates."
        ),
    }


_COMMON_PROP_LABELS = {
    "P31": "instance of",
    "P50": "author",
    "P569": "date of birth",
    "P570": "date of death",
    "P17": "country",
    "P1476": "title",
    "P921": "main subject",
    "P170": "creator",
    "P123": "publisher",
    "P577": "publication date",
}


def _link_family(prop: str) -> str:
    p = prop.strip()
    if p.startswith("P") and p[1:].isdigit():
        return "Wikidata properties"
    if "lrmoo" in p or "frbroo" in p:
        return "FRBRoo / LRMoo (IFLA)"
    if "cidoc" in p:
        return "CIDOC CRM"
    if "rdf-syntax" in p or p.startswith("rdf:") or p.startswith("rdfs:"):
        return "RDF / RDFS"
    if "w3id.org/mhm/ontology" in p or p.startswith("mhm:"):
        return "MHM ontology"
    return "Other"


def _link_label(prop: str) -> str:
    p = prop.strip()
    if p.startswith("P") and p[1:].isdigit():
        meaning = _COMMON_PROP_LABELS.get(p)
        return f"{p} — {meaning}" if meaning else p
    if "w3id.org/mhm/ontology#" in p:
        return f"mhm:{p.rsplit('#', 1)[-1]}"
    for prefix, marker in (
        ("lrmoo", "lrmoo"), ("cidoc", "cidoc"), ("rdfs", "rdf-syntax"),
    ):
        if marker in p:
            sep = "#" if "#" in p else "/"
            return f"{prefix}:{p.rsplit(sep, 1)[-1]}"
    return p.rsplit("/", 1)[-1] if p.startswith("http") else p


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
    "wikidata_uploaded_items": _tool_wikidata_uploaded_items,
    "wikidata_fetch_items": _tool_wikidata_fetch_items,
    "show_movement_map": _tool_show_movement_map,
    "show_link_types": _tool_show_link_types,
    "show_wikidata_places": _tool_show_wikidata_places,
    "wikidata_pack": _tool_wikidata_pack,
    "export_pdf": _tool_export_pdf,
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
    if fmt == "pdf":
        from app.services.research_agent.pdf import render_pdf

        title = str(content.get("title") or "Research artifact")
        text = str(content.get("text") or json.dumps(content, ensure_ascii=False, indent=2))
        return render_pdf(title, text), "application/pdf", "artifact.pdf"
    body = json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
    return body, "application/json; charset=utf-8", "artifact.json"
