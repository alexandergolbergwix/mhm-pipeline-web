"""HMO Wikibase Studio router.

Builds and uploads IIIF Presentation API 3.0 manifests for every
manuscript in a run's RDF graph, plus reports HMO → Wikidata projection
coverage. Manifests are hosted on the project's own Wikibase Cloud
(``mhm-hmo.wikibase.cloud``), which is a separate trust boundary from
Wikidata itself (see :mod:`app.pipeline.hmo_studio` for the rationale).

Endpoints, all under ``/api/runs/{run_id}/hmo-studio/``::

    POST /build-manifests   →  generate manifests from manuscripts.ttl
    POST /upload-manifests  →  publish to wikibase.cloud (dry-run by default)
    GET  /coverage          →  HMO class → Wikidata projection report
    GET  /status            →  idle | built | uploaded | error + counts

Bot credentials (username + password) are loaded from the calling
user's encrypted-secret store; if either is missing the router responds
``400`` with a friendly redirect to ``Settings → Credentials``.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.crypto import secrets as secrets_mod
from app.db import get_session
from app.models.api_key import ApiKey
from app.pipeline import hmo_studio as hmo_pipeline
from app.pipeline.rdf_build import rdf_output_path_for_run
from app.routers.runs import _lookup_run_with_access

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/runs", tags=["hmo-studio"])


# Default bot login name (the second half of the ``User@Bot`` pair).
# Operators create a bot password at
# https://mhm-hmo.wikibase.cloud/wiki/Special:BotPasswords and pick this
# name themselves. The desktop pipeline calls it the "bot name"; we
# keep the same default and let environments override via a stored key.
_DEFAULT_BOT_NAME = "mhm-pipeline"


# ── Response models ────────────────────────────────────────────────────


class HmoBuildResponse(BaseModel):
    manifest_count: int
    total_canvases: int
    total_ranges: int
    total_annotations: int
    manifest_dir: str
    manifests: list[dict[str, Any]]


class HmoUploadOutcomeDto(BaseModel):
    shelfmark: str
    page_url: str
    status: str
    message: str
    edit_id: int | None
    new_revid: int | None
    canvas_count: int
    range_count: int
    annotation_count: int


class HmoUploadResponse(BaseModel):
    dry_run: bool
    uploaded: int
    unchanged: int
    failed: int
    outcomes: list[HmoUploadOutcomeDto]


class HmoStatus(BaseModel):
    state: Literal["idle", "built", "uploaded", "error"]
    rdf_present: bool
    manifest_count: int
    coverage_present: bool
    last_upload_at: str | None
    last_upload: HmoUploadResponse | None
    bot_username_set: bool
    bot_password_set: bool


class HmoUploadRequest(BaseModel):
    dry_run: bool = Field(
        default=True,
        description="Default True — describes what would happen without "
                    "writing. Set False for live; live also requires the "
                    "user to have both wikibase_cloud_bot_username and "
                    "wikibase_cloud_bot_password stored in Settings.",
    )


# ── Build ──────────────────────────────────────────────────────────────


@router.post(
    "/{run_id}/hmo-studio/build-manifests",
    response_model=HmoBuildResponse,
)
async def build_manifests(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoBuildResponse:
    """Generate IIIF manifests for every manuscript in the run's RDF graph.

    Writes one ``MS_<shelfmark>.json`` per manuscript into the run's
    ``iiif_manifests/`` directory. Overwrites any existing manifests —
    the builder is deterministic, so re-running on the same TTL is safe.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)
    ttl_path = rdf_output_path_for_run(str(run_id))
    if not ttl_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No RDF graph for this run yet. Build the RDF (RDF Graph) "
                "before generating IIIF manifests."
            ),
        )
    manifest_dir = hmo_pipeline.manifest_dir_for_run(str(run_id))
    try:
        result = await hmo_pipeline.build_manifests_for_run(
            ttl_path=ttl_path, manifest_dir=manifest_dir,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        ) from exc
    return HmoBuildResponse(**result.__dict__)


# ── Upload ─────────────────────────────────────────────────────────────


@router.post(
    "/{run_id}/hmo-studio/upload-manifests",
    response_model=HmoUploadResponse,
)
async def upload_manifests(
    run_id: uuid.UUID,
    payload: HmoUploadRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoUploadResponse:
    """Upload generated manifests to the HMO Wikibase Cloud.

    Live writes require both bot username + password in the user's
    encrypted-secret store. Dry-run is allowed without credentials
    (handy for previewing what would be sent).
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)

    manifest_dir = hmo_pipeline.manifest_dir_for_run(str(run_id))
    if not manifest_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No IIIF manifests for this run yet. Click "
                "“Build manifests” first."
            ),
        )

    bot_username = await _unwrap_user_secret(
        db, auth, "wikibase_cloud_bot_username",
    )
    bot_password = await _unwrap_user_secret(
        db, auth, "wikibase_cloud_bot_password",
    )

    if not payload.dry_run:
        missing = [
            name for name, val in (
                ("wikibase_cloud_bot_username", bot_username),
                ("wikibase_cloud_bot_password", bot_password),
            )
            if not val
        ]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Add Wikibase bot credentials in Settings → "
                    "Credentials, then retry. Missing: "
                    + ", ".join(missing)
                ),
            )

    result = await hmo_pipeline.upload_manifests_for_run(
        manifest_dir=manifest_dir,
        bot_username=bot_username or "",
        bot_password=bot_password or "",
        bot_name=_DEFAULT_BOT_NAME,
        dry_run=payload.dry_run,
    )

    # Cache the report on disk so /status can surface "last upload" info.
    hmo_pipeline.cache_upload_report(str(run_id), result)

    return HmoUploadResponse(
        dry_run=result.dry_run,
        uploaded=result.uploaded,
        unchanged=result.unchanged,
        failed=result.failed,
        outcomes=[HmoUploadOutcomeDto(**o.__dict__) for o in result.outcomes],
    )


# ── Coverage report ────────────────────────────────────────────────────


@router.get("/{run_id}/hmo-studio/coverage")
async def coverage(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the HMO → Wikidata projection-coverage JSON.

    Cached on disk after the first build. The cached file is returned
    verbatim if present; otherwise rebuilt against the run's TTL.
    """
    await _lookup_run_with_access(db, run_id, auth)
    cache = hmo_pipeline.coverage_path_for_run(str(run_id))
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(
                "Cached coverage at %s unreadable (%s); regenerating", cache, exc,
            )

    ttl_path = rdf_output_path_for_run(str(run_id))
    if not ttl_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No RDF graph for this run yet. Build the RDF (RDF Graph) "
                "before requesting coverage."
            ),
        )
    try:
        report = await hmo_pipeline.coverage_report_for_run(ttl_path=ttl_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        ) from exc
    hmo_pipeline.cache_coverage_report(str(run_id), report)
    return report


# ── Status ─────────────────────────────────────────────────────────────


@router.get("/{run_id}/hmo-studio/status", response_model=HmoStatus)
async def studio_status(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoStatus:
    """Idle / built / uploaded / error summary + counts."""
    await _lookup_run_with_access(db, run_id, auth)

    ttl_path = rdf_output_path_for_run(str(run_id))
    manifest_dir = hmo_pipeline.manifest_dir_for_run(str(run_id))
    coverage_cache = hmo_pipeline.coverage_path_for_run(str(run_id))
    upload_report = hmo_pipeline.upload_report_path_for_run(str(run_id))

    manifest_count = (
        sum(1 for _ in manifest_dir.glob("MS_*.json")) if manifest_dir.exists() else 0
    )

    last_upload: HmoUploadResponse | None = None
    last_upload_at: str | None = None
    if upload_report.exists():
        try:
            raw = json.loads(upload_report.read_text(encoding="utf-8"))
            last_upload = HmoUploadResponse(
                dry_run=bool(raw.get("dry_run", True)),
                uploaded=int(raw.get("uploaded", 0)),
                unchanged=int(raw.get("unchanged", 0)),
                failed=int(raw.get("failed", 0)),
                outcomes=[
                    HmoUploadOutcomeDto(**o) for o in (raw.get("outcomes") or [])
                ],
            )
            last_upload_at = _iso_mtime(upload_report)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Cached upload report %s unreadable: %s", upload_report, exc,
            )

    state: Literal["idle", "built", "uploaded", "error"]
    if last_upload is not None:
        state = "error" if last_upload.failed else "uploaded"
    elif manifest_count > 0:
        state = "built"
    else:
        state = "idle"

    bot_username_set = await _has_user_secret(db, auth, "wikibase_cloud_bot_username")
    bot_password_set = await _has_user_secret(db, auth, "wikibase_cloud_bot_password")

    return HmoStatus(
        state=state,
        rdf_present=ttl_path.exists(),
        manifest_count=manifest_count,
        coverage_present=coverage_cache.exists(),
        last_upload_at=last_upload_at,
        last_upload=last_upload,
        bot_username_set=bot_username_set,
        bot_password_set=bot_password_set,
    )


# ── Helpers ────────────────────────────────────────────────────────────


def _iso_mtime(path: Path) -> str:
    """Filesystem-mtime as an ISO 8601 string (UTC)."""
    from datetime import datetime, timezone  # noqa: PLC0415

    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


async def _has_user_secret(
    db: AsyncSession, auth: AuthContext, key_name: str,
) -> bool:
    """True iff the user has *anything* saved under *key_name*.

    Does not unwrap — just checks for the row's presence so the Studio
    UI can show "configured ✓" before the user attempts an upload.
    """
    row = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == auth.user.id, ApiKey.key_name == key_name,
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def _unwrap_user_secret(
    db: AsyncSession, auth: AuthContext, key_name: str,
) -> str | None:
    """Unwrap the user's stored secret under *key_name* with the request's KEK.

    Returns ``None`` when the user hasn't saved one (or unwrap fails —
    e.g. KEK rotated). Mirrors the pattern in :mod:`ai_verifier`.
    """
    row = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == auth.user.id, ApiKey.key_name == key_name,
            )
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
        logger.warning(
            "Failed to unwrap %s for user %s", key_name, auth.user.id,
        )
        return None
