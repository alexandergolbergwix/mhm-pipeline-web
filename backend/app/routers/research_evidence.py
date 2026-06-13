"""Evidence panel endpoint (Feature 8).

GET /api/projects/{project_id}/research/evidence?uri=<entity_uri>

Resolves a Linked-Data URI (manuscript or person, as minted by the RDF
builder) back to its MARC source record, the approval trail, and any
authority matches — giving scholars a direct path from a SPARQL result
to the original cataloguing evidence.

URI → control-number extraction:
  The RDF builder mints manuscript URIs as ``…/MS_<cn_uri_safe>`` where
  ``cn_uri_safe`` is the MARC control number with non-word characters replaced
  by underscores.  We reverse this by stripping the last path segment after
  ``MS_``.  If the URI's local name matches a run_records row, we surface that
  record.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.extraction_approval import ExtractionApproval
from app.models.run import AuthorityMatch, Run, RunRecord
from app.routers.linked_data_explorer import _load_graph_or_404

router = APIRouter(tags=["research"])


class ApprovalOut(BaseModel):
    source: str
    entity_text: str
    approved_by: uuid.UUID | None
    approved_at: str | None

    model_config = {"from_attributes": True}


class AuthorityMatchOut(BaseModel):
    entity_text: str | None
    entity_kind: str | None
    matched_name: str | None
    wikidata_qid: str | None
    viaf_id: str | None
    confidence: str | None
    source: str | None


class EvidenceOut(BaseModel):
    uri: str
    control_number: str | None
    marc: dict[str, Any] | None
    approvals: list[ApprovalOut]
    authority_matches: list[AuthorityMatchOut]


def _extract_control_number(uri: str) -> str | None:
    """Best-effort: extract the MARC control number from a manuscript URI.

    The minting pattern (from rdf_build.py) is:
      cn_uri = re.sub(r"[^\\w.\\-]", "_", cn.strip(...)).strip("_") or cn
    We look for ``MS_`` anywhere in the URI (after the last delimiter).
    """
    # Extract local name after the last /, #, or : separator
    local = re.split(r"[/#:]", uri)[-1]
    if local.upper().startswith("MS_"):
        return local[3:]  # keep the full cn_uri_safe form for DB lookup
    return None


async def _find_run_record(
    project_id: uuid.UUID,
    control_number: str,
    db: AsyncSession,
) -> tuple[RunRecord | None, uuid.UUID | None]:
    """Find a run record for the project matching the given control number."""
    # Collect all run IDs for the project
    run_rows = await db.execute(select(Run.id).where(Run.project_id == project_id))
    run_ids = [r for r in run_rows.scalars().all()]
    if not run_ids:
        return None, None

    # Try exact match first, then suffix match (cn_uri_safe may differ slightly)
    for run_id in run_ids:
        row = await db.get(RunRecord, (run_id, control_number))
        if row is not None:
            return row, run_id

        # Try suffix: cn_uri_safe is control_number with unsafe chars → underscores.
        # We try loading any record whose control_number matches when normalised.
        normalised = re.sub(r"[^\w.\-]", "_", control_number).strip("_")
        rows = await db.execute(
            select(RunRecord).where(
                RunRecord.run_id == run_id,
                RunRecord.control_number == normalised,
            )
        )
        rec = rows.scalar_one_or_none()
        if rec is not None:
            return rec, run_id

    return None, None


@router.get("/projects/{project_id}/research/evidence", response_model=EvidenceOut)
async def get_evidence(
    project_id: uuid.UUID,
    uri: str = Query(..., description="The entity URI to look up."),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    """Resolve a URI to its MARC source + approval trail."""
    # Membership + graph existence check (reuses existing guard)
    graph = await _load_graph_or_404(project_id, auth, db)

    # Verify the URI actually exists in the merged graph
    from rdflib import URIRef
    if (URIRef(uri), None, None) not in graph:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URI not found in project graph.")

    control_number = _extract_control_number(uri)
    marc: dict[str, Any] | None = None
    approvals: list[ApprovalOut] = []
    authority_matches: list[AuthorityMatchOut] = []
    run_id: uuid.UUID | None = None

    if control_number:
        run_record, run_id = await _find_run_record(project_id, control_number, db)
        if run_record is not None:
            marc = run_record.marc

    if run_id is not None and control_number:
        # Approvals
        app_rows = await db.execute(
            select(ExtractionApproval).where(
                ExtractionApproval.run_id == run_id,
                ExtractionApproval.control_number == control_number,
                ExtractionApproval.approved.is_(True),
            )
        )
        approvals = [
            ApprovalOut(
                source=row.source,
                entity_text=row.text,
                approved_by=row.approved_by,
                approved_at=row.approved_at.isoformat() if row.approved_at else None,
            )
            for row in app_rows.scalars().all()
        ]

        # Authority matches
        auth_rows = await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.run_id == run_id,
                AuthorityMatch.control_number == control_number,
            )
        )
        for am in auth_rows.scalars().all():
            authority_matches.append(
                AuthorityMatchOut(
                    entity_text=am.entity_text,
                    entity_kind=am.entity_kind,
                    matched_name=am.matched_name or None,
                    wikidata_qid=am.wikidata_qid or None,
                    viaf_id=am.viaf_id or None,
                    confidence=am.confidence or None,
                    source=am.source or None,
                )
            )

    return EvidenceOut(
        uri=uri,
        control_number=control_number,
        marc=marc,
        approvals=approvals,
        authority_matches=authority_matches,
    )
