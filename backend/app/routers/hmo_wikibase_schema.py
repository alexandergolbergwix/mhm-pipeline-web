"""Global HMO Wikibase schema bootstrap router.

Unlike ``hmo_studio.py`` (per-run manifest build/upload, prefixed under
``/runs/{run_id}/hmo-studio/``), the ontology schema is global — one
ontology file, one Wikibase Cloud instance — so this router has no
``run_id`` in its path:

    GET  /api/hmo-wikibase-schema/status     →  ontology counts vs. mapped
    POST /api/hmo-wikibase-schema/bootstrap  →  create missing classes/properties

Gated on ``current_auth`` only (any signed-in user), matching the
``/me/api-keys`` router's pattern for account-scoped rather than
project-scoped operations — this endpoint has no project to scope to.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.api_key import ApiKey
from app.models.run_job import JOB_KIND_HMO_SCHEMA_BOOTSTRAP
from app.pipeline import hmo_schema_bootstrap as pipeline
from app.pipeline.run_job_params import prepare_job_params
from app.pipeline.run_job_service import ActiveJobError, create_job, serialise_job
from app.routers.runs import _lookup_run_with_access

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hmo-wikibase-schema", tags=["hmo-wikibase-schema"])


class SchemaStatusResponse(BaseModel):
    total_classes: int
    total_properties: int
    mapped_classes: int
    mapped_properties: int
    missing_sample: list[str]
    bot_username_set: bool
    bot_password_set: bool


class SchemaBootstrapEntryDto(BaseModel):
    ontology_uri: str
    entity_kind: str
    label: str
    wikibase_id: str | None
    status: str
    message: str = ""


class SchemaBootstrapResponse(BaseModel):
    dry_run: bool
    created: int
    skipped: int
    failed: int
    would_create: int = 0
    entries: list[SchemaBootstrapEntryDto]


class SchemaBootstrapRequest(BaseModel):
    dry_run: bool = Field(
        default=True,
        description="Default True — reports what would be created without "
                    "writing. Set False for live; live also requires the "
                    "user to have both wikibase_cloud_bot_username and "
                    "wikibase_cloud_bot_password stored in Settings.",
    )
    run_id: uuid.UUID | None = Field(
        default=None,
        description="Required when dry_run=False. The schema is global, "
                    "not tied to any run — this only anchors the background "
                    "job (which needs a run_id for its progress-polling row) "
                    "to whichever run's page the curator was on.",
    )


@router.get("/status", response_model=SchemaStatusResponse)
async def get_schema_status(
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SchemaStatusResponse:
    """Ontology class/property counts vs. how many already have a live mapping."""
    result = await pipeline.schema_status(db)
    return SchemaStatusResponse(
        total_classes=result.total_classes,
        total_properties=result.total_properties,
        mapped_classes=result.mapped_classes,
        mapped_properties=result.mapped_properties,
        missing_sample=result.missing_sample,
        bot_username_set=await _has_user_secret(db, auth, "wikibase_cloud_bot_username"),
        bot_password_set=await _has_user_secret(db, auth, "wikibase_cloud_bot_password"),
    )


@router.post("/bootstrap")
async def bootstrap_schema(
    payload: SchemaBootstrapRequest,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SchemaBootstrapResponse | dict[str, Any]:
    """Create every missing HMO ontology class/property on the Wikibase Cloud.

    Dry-run (the default) is a fast, read-only pass with no network calls —
    it stays synchronous and returns the full result immediately.

    A live run makes one sequential write per missing class/property
    (~380 today), comfortably over Heroku's 30s HTTP timeout — it spawns a
    ``run_jobs`` background job and returns ``{job_id, ...}`` right away;
    poll ``GET /runs/{run_id}/jobs/{job_id}`` (or subscribe to the
    project's WebSocket room) for progress. Requires both bot username +
    password in the user's encrypted-secret store, and a ``run_id`` to
    anchor the job row to (the schema itself is global).
    """
    if payload.dry_run:
        result = await pipeline.bootstrap_schema(db, writer=None, dry_run=True)
        pipeline.cache_schema_bootstrap_report(result)
        return SchemaBootstrapResponse(
            dry_run=result.dry_run,
            created=result.created,
            skipped=result.skipped,
            failed=result.failed,
            would_create=result.would_create,
            entries=[SchemaBootstrapEntryDto(**entry.__dict__) for entry in result.entries],
        )

    if payload.run_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="run_id is required for a live bootstrap (anchors the background job).",
        )
    run = await _lookup_run_with_access(db, payload.run_id, auth, write=True)

    params = await prepare_job_params(
        db, auth, run_id=payload.run_id, kind=JOB_KIND_HMO_SCHEMA_BOOTSTRAP,
        params={"dry_run": False},
    )
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=payload.run_id,
            kind=JOB_KIND_HMO_SCHEMA_BOOTSTRAP,
            params=params,
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "a schema bootstrap job is already running",
                "job_id": str(exc.job_id),
            },
        ) from exc
    response.status_code = status.HTTP_201_CREATED
    return serialise_job(job)


# ── Helpers (mirrors hmo_studio.py's credential-unwrap pattern) ─────────


async def _has_user_secret(db: AsyncSession, auth: AuthContext, key_name: str) -> bool:
    row = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == auth.user.id, ApiKey.key_name == key_name,
            )
        )
    ).scalar_one_or_none()
    return row is not None
