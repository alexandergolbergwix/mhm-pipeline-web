"""Wikidata Studio router — builds items + QuickStatements for a run.

The endpoint reads the run's MARC records and approved authority
matches, drives the *real* desktop ``WikidataItemBuilder`` +
``QuickStatementsExporter`` in a threadpool, and returns the structured
items (every label / description / claim / qualifier / reference) plus
the QuickStatements TSV blob ready for download.

Only the **approved** matches feed the builder — this is the curator
workflow's unit of truth (see Rule 54 in the desktop CLAUDE.md).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline import wikidata_studio
from app.routers.runs import _lookup_run_with_access  # noqa: PLF401 — module-internal

router = APIRouter(prefix="/runs", tags=["wikidata-studio"])


class StudioSummary(BaseModel):
    total_items: int
    manuscripts: int
    persons: int
    works: int
    statements: int


class StudioBuildResponse(BaseModel):
    items: list[dict[str, Any]]
    quickstatements: str
    summary: StudioSummary
    approved_match_count: int
    record_count: int


@router.get("/{run_id}/wikidata-studio", response_model=StudioBuildResponse)
async def build_studio(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StudioBuildResponse:
    await _lookup_run_with_access(db, run_id, auth)

    # Pull every record + every *approved* match for the run.
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    matches = (
        await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.run_id == run_id, AuthorityMatch.approved.is_(True),
            )
        )
    ).scalars().all()

    marc_records = [dict(r.marc) for r in records]
    approved_matches = [
        {
            "id": str(m.id),
            "control_number": m.control_number,
            "entity_text": m.entity_text,
            "role": m.role,
            "matched_name": m.matched_name,
            "mazal_id": m.mazal_id,
            "viaf_id": m.viaf_id,
            "wikidata_qid": m.wikidata_qid,
            "confidence": m.confidence,
            "source": m.source,
            "payload": m.payload or {},
        }
        for m in matches
    ]

    result = await wikidata_studio.build_items_for_run(
        marc_records=marc_records, approved_matches=approved_matches,
    )

    return StudioBuildResponse(
        items=result["items"],
        quickstatements=result["quickstatements"],
        summary=StudioSummary(**result["summary"]),
        approved_match_count=len(approved_matches),
        record_count=len(marc_records),
    )


@router.get("/{run_id}/wikidata-studio/quickstatements.txt", response_class=PlainTextResponse)
async def download_quickstatements(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Plain-text download of the QuickStatements TSV — what the curator
    pastes into https://quickstatements.toolforge.org."""
    studio = await build_studio(run_id, auth, db)  # type: ignore[arg-type]
    return PlainTextResponse(
        studio.quickstatements,
        headers={
            "Content-Disposition": (
                f'attachment; filename="run-{run_id}-quickstatements.txt"'
            ),
        },
    )
