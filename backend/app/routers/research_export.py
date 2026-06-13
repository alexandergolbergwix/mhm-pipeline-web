"""SPARQL result export for the Linked Data Explorer (Feature 1).

POST /api/projects/{project_id}/research/sparql/export
  - Validates the query is read-only (reuses ``_validate_query``).
  - Checks project membership (reuses ``_require_viewer``).
  - Runs the query against the merged HMO graph (reuses ``_load_graph_or_404``).
  - Streams the result rows as CSV, JSON, BibTeX, or RIS.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.routers.linked_data_explorer import (
    _execute_rdflib_query,
    _load_graph_or_404,
    _validate_query,
    _TIMEOUT_S,
)
from fastapi import HTTPException, status

router = APIRouter(tags=["research"])
logger = logging.getLogger(__name__)

ExportFormat = Literal["csv", "json", "bibtex", "ris"]


class ExportRequest(BaseModel):
    query: str
    format: ExportFormat


def _to_csv(columns: list[str], rows: list[list[str | None]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows([("" if cell is None else cell) for cell in row] for row in rows)
    return buf.getvalue()


def _to_json(columns: list[str], rows: list[list[str | None]]) -> str:
    return json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, indent=2)


def _to_bibtex(columns: list[str], rows: list[list[str | None]]) -> str:
    parts: list[str] = []
    for i, row in enumerate(rows):
        row_dict = dict(zip(columns, row))
        uri = row_dict.get("s") or row_dict.get("uri") or f"row_{i}"
        # Derive a safe cite key from the URI or row index
        cite_key = uri.rstrip("/").rsplit("/", 1)[-1].replace(":", "_") or f"row_{i}"
        label = row_dict.get("label") or row_dict.get("name") or ""
        fields = [f"  note = {{{uri}}}"]
        if label:
            fields.append(f"  title = {{{label}}}")
        for col, val in row_dict.items():
            if col in ("s", "uri", "label", "name") or val is None:
                continue
            fields.append(f"  {col} = {{{val}}}")
        parts.append(f"@misc{{{cite_key},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(parts)


def _to_ris(columns: list[str], rows: list[list[str | None]]) -> str:
    parts: list[str] = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        lines = ["TY  - GEN"]
        label = row_dict.get("label") or row_dict.get("name")
        if label:
            lines.append(f"TI  - {label}")
        uri = row_dict.get("s") or row_dict.get("uri")
        if uri:
            lines.append(f"UR  - {uri}")
        for col, val in row_dict.items():
            if col in ("s", "uri", "label", "name") or val is None:
                continue
            lines.append(f"N1  - {col}: {val}")
        lines.append("ER  - ")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


_CONTENT_TYPES: dict[str, str] = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "bibtex": "application/x-bibtex; charset=utf-8",
    "ris": "application/x-research-info-systems; charset=utf-8",
}

_EXTENSIONS: dict[str, str] = {
    "csv": "csv",
    "json": "json",
    "bibtex": "bib",
    "ris": "ris",
}


@router.post("/projects/{project_id}/research/sparql/export")
async def export_sparql_results(
    project_id: uuid.UUID,
    body: ExportRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Run a read-only SPARQL query and stream the results in the requested format."""
    _validate_query(body.query)
    graph = await _load_graph_or_404(project_id, auth, db)
    try:
        sparql_resp = await asyncio.wait_for(
            asyncio.to_thread(_execute_rdflib_query, graph, body.query),
            timeout=_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail="Query timed out after 30 s.",
        )
    except Exception as exc:
        logger.warning("Export SPARQL query failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query error: {exc}",
        )

    fmt = body.format
    if fmt == "csv":
        content = _to_csv(sparql_resp.columns, sparql_resp.rows)
    elif fmt == "json":
        content = _to_json(sparql_resp.columns, sparql_resp.rows)
    elif fmt == "bibtex":
        content = _to_bibtex(sparql_resp.columns, sparql_resp.rows)
    else:  # ris
        content = _to_ris(sparql_resp.columns, sparql_resp.rows)

    ext = _EXTENSIONS[fmt]
    return StreamingResponse(
        iter([content]),
        media_type=_CONTENT_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="sparql-export.{ext}"'},
    )
