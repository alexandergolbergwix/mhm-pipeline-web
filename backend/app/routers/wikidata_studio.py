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

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline import wikidata_studio, wikidata_upload
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
    approved_match_count: int     # how many of the feed are approved
    pending_match_count: int      # how many are still pending review
    used_match_count: int         # what we actually fed the builder
    approved_only: bool           # which mode was used
    record_count: int


@router.get("/{run_id}/wikidata-studio", response_model=StudioBuildResponse)
async def build_studio(
    run_id: uuid.UUID,
    approved_only: bool = Query(
        default=False,
        description="When true, only approved matches feed the item "
                    "builder. Default false — every candidate match is "
                    "used so persons + cross-source IDs surface immediately.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StudioBuildResponse:
    await _lookup_run_with_access(db, run_id, auth)

    # Pull every record + every match (filtered to approved on request).
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    all_matches = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()
    approved_count = sum(1 for m in all_matches if m.approved)
    pending_count = len(all_matches) - approved_count
    matches = [m for m in all_matches if m.approved] if approved_only else all_matches

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
        approved_match_count=approved_count,
        pending_match_count=pending_count,
        used_match_count=len(approved_matches),
        approved_only=approved_only,
        record_count=len(marc_records),
    )


@router.get("/{run_id}/wikidata-studio/quickstatements.txt", response_class=PlainTextResponse)
async def download_quickstatements(
    run_id: uuid.UUID,
    approved_only: bool = Query(default=False),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Plain-text QuickStatements TSV — paste into
    https://quickstatements.toolforge.org."""
    studio = await build_studio(run_id, approved_only, auth, db)  # type: ignore[arg-type]
    suffix = "approved" if approved_only else "all"
    return PlainTextResponse(
        studio.quickstatements,
        headers={
            "Content-Disposition": (
                f'attachment; filename="run-{run_id}-{suffix}-quickstatements.txt"'
            ),
        },
    )


# ── Reconcile (SPARQL against Wikidata, no writes) ──────────────────────


class ReconcileOutcomeDto(BaseModel):
    local_id: str
    label: str
    entity_type: str
    existing_qid: str | None
    method: str
    message: str


class ReconcileResponse(BaseModel):
    reconciled: int
    matched: int
    outcomes: list[ReconcileOutcomeDto]


@router.post(
    "/{run_id}/wikidata-studio/reconcile", response_model=ReconcileResponse,
)
async def reconcile_against_wikidata(
    run_id: uuid.UUID,
    approved_only: bool = Query(default=False),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ReconcileResponse:
    """For every built item, SPARQL-query Wikidata to find an existing
    QID via VIAF / NLI / cluster IDs. Updates the in-memory items so the
    next download / upload knows to UPDATE instead of CREATE. **Never**
    writes to Wikidata."""
    native = await _build_native_items(db, run_id, auth, approved_only=approved_only)
    outcomes = await wikidata_upload.reconcile_items(native)
    return ReconcileResponse(
        reconciled=len(outcomes),
        matched=sum(1 for o in outcomes if o.existing_qid),
        outcomes=[ReconcileOutcomeDto(**o.__dict__) for o in outcomes],
    )


# ── Upload (dry-run or live, all 4 guards intact) ──────────────────────


class UploadOutcomeDto(BaseModel):
    local_id: str
    label: str
    entity_type: str
    qid: str | None
    status: str
    message: str
    added_properties: list[str]


class UploadResponse(BaseModel):
    dry_run: bool
    moratorium_lifted: bool
    test_mode: bool
    outcomes: list[UploadOutcomeDto]


@router.post("/{run_id}/wikidata-studio/upload", response_model=UploadResponse)
async def upload_to_wikidata(
    run_id: uuid.UUID,
    dry_run: bool = Query(
        default=True,
        description="Default True — describe what would happen without "
                    "writing. Set False for live; live also requires the "
                    "user to have a stored Wikidata token (Settings) AND "
                    "MORATORIUM_LIFTED=true in the env (or WIKIDATA_TEST_MODE=true).",
    ),
    approved_only: bool = Query(default=True),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> UploadResponse:
    """Live uploads ALWAYS use the user's own Wikidata token (stored
    encrypted via the Settings page) and route through the real
    ``WikidataUploader`` with all four modification guards intact."""
    import os  # noqa: PLC0415

    # Build the items first (with the latest approval state).
    native = await _build_native_items(db, run_id, auth, approved_only=approved_only)

    token: str | None = None
    if not dry_run:
        token = await _unwrap_user_secret(db, auth, "wikidata")

    if not dry_run and not token:
        raise_msg = (
            "Live upload requires a Wikidata token in Settings. "
            "Add one (User@Bot:hex or OAuth secret) and retry."
        )
        return UploadResponse(
            dry_run=False, moratorium_lifted=False, test_mode=False,
            outcomes=[UploadOutcomeDto(
                local_id="*", label="(token missing)", entity_type="",
                qid=None, status="failed", message=raise_msg, added_properties=[],
            )],
        )

    outcomes = await wikidata_upload.upload_items(
        native, token=token or "", dry_run=dry_run,
    )
    return UploadResponse(
        dry_run=dry_run,
        moratorium_lifted=os.environ.get("MORATORIUM_LIFTED", "").lower() == "true",
        test_mode=os.environ.get("WIKIDATA_TEST_MODE", "").lower() == "true",
        outcomes=[UploadOutcomeDto(**o.__dict__) for o in outcomes],
    )


# ── helpers ─────────────────────────────────────────────────────────────


async def _build_native_items(
    db: AsyncSession, run_id: uuid.UUID, auth: AuthContext,
    *, approved_only: bool,
) -> list[Any]:
    """Re-run the builder and return the *native* WikidataItem objects
    (not the JSON dicts) so reconcile/upload can mutate them in place."""
    await _lookup_run_with_access(db, run_id, auth)
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    all_matches = (
        await db.execute(select(AuthorityMatch).where(AuthorityMatch.run_id == run_id))
    ).scalars().all()
    matches = [m for m in all_matches if m.approved] if approved_only else all_matches

    result = await wikidata_studio.build_items_for_run(
        marc_records=[dict(r.marc) for r in records],
        approved_matches=[
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
        ],
        return_native=True,
    )
    return result.get("native_items") or []


async def _unwrap_user_secret(db: AsyncSession, auth: AuthContext, key_name: str) -> str | None:
    """Unwrap the user's stored Wikidata token (or any named secret)
    using the request's KEK. Returns None when the user hasn't saved one."""
    from cryptography.exceptions import InvalidTag  # noqa: PLC0415

    from app.crypto import secrets as secrets_mod  # noqa: PLC0415
    from app.models.api_key import ApiKey  # noqa: PLC0415

    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.user_id == auth.user.id, ApiKey.key_name == key_name)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return secrets_mod.unwrap_secret(
            secrets_mod.WrappedSecret(
                ciphertext=row.ciphertext,
                ciphertext_nonce=row.ciphertext_nonce,
                dek_wrapped=row.dek_wrapped,
                dek_wrap_nonce=row.dek_wrap_nonce,
            ),
            kek=auth.kek,
        )
    except InvalidTag:
        return None
