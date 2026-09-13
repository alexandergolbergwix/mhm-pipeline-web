"""Parameterized SPARQL templates. The planner never executes raw user SPARQL.

The privileged agent emits a template id + typed params. This module renders
a SELECT with a mandatory LIMIT. Raw SPARQL still passes ``_validate_query``
and receives a LIMIT cap.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+\s*$", re.IGNORECASE | re.DOTALL)
_SAFE_IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"{}|\\^`]+$")
_SAFE_QID = re.compile(r"^Q\d+$", re.IGNORECASE)
_SAFE_LITERAL = re.compile(r"^[\w\s\-'.:,;()/]+$", re.UNICODE)

TEMPLATES: dict[str, str] = {
    "sample_triples": (
        "SELECT ?s ?p ?o WHERE {{ ?s ?p ?o }} LIMIT {limit}"
    ),
    "entity_label": (
        "SELECT ?s ?label WHERE {{ ?s <http://www.w3.org/2000/01/rdf-schema#label> ?label . "
        "FILTER(CONTAINS(LCASE(STR(?label)), LCASE(\"{needle}\"))) }} LIMIT {limit}"
    ),
    "wikidata_item": (
        "SELECT ?item ?itemLabel WHERE {{ BIND(wd:{qid} AS ?item) "
        "SERVICE wikibase:label {{ bd:serviceParam wikibase:language \"en,he\". }} }} LIMIT {limit}"
    ),
}

ALLOWED_SOURCES = frozenset({"hmo", "wikibase", "wikidata"})
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 25


def clamp_limit(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return max(1, min(_MAX_LIMIT, value))


def enforce_limit(query: str, *, limit: int | None = None) -> str:
    capped = clamp_limit(limit if limit is not None else _DEFAULT_LIMIT)
    stripped = query.strip().rstrip(";")
    if _LIMIT_RE.search(stripped):
        return _LIMIT_RE.sub(f"LIMIT {capped}", stripped)
    return f"{stripped}\nLIMIT {capped}"


def render_template(template_id: str, params: dict[str, Any] | None) -> tuple[str, str]:
    """Return (query, source). Raises 400 on unknown template or unsafe params."""
    params = params or {}
    source = str(params.get("source") or "hmo").lower()
    if source not in ALLOWED_SOURCES:
        raise HTTPException(status_code=400, detail="SPARQL source must be hmo, wikibase, or wikidata.")
    body = TEMPLATES.get(template_id)
    if body is None:
        raise HTTPException(status_code=400, detail=f"Unknown SPARQL template '{template_id}'.")
    limit = clamp_limit(params.get("limit"))
    needle = str(params.get("needle") or "")
    if needle and not _SAFE_LITERAL.match(needle):
        raise HTTPException(status_code=400, detail="needle contains unsupported characters.")
    qid = str(params.get("qid") or "Q5")
    if template_id == "wikidata_item":
        if not _SAFE_QID.match(qid):
            raise HTTPException(status_code=400, detail="qid must look like Q123.")
        source = "wikidata"
    query = body.format(limit=limit, needle=needle.replace('"', ""), qid=qid.upper())
    return query, source
