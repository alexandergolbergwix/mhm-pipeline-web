"""HMO Wikibase Studio router.

Builds and uploads IIIF Presentation API 3.0 manifests for every
manuscript in a run's RDF graph, plus reports HMO → Wikidata projection
coverage. Manifests are hosted on the project's own Wikibase Cloud
(``mhm-hmo.wikibase.cloud``), which is a separate trust boundary from
Wikidata itself (see :mod:`app.pipeline.hmo_studio` for the rationale).

Endpoints, all under ``/api/runs/{run_id}/hmo-studio/``::

    POST /build-manifests   →  enqueue ``hmo_manifest_build`` job (201)
    POST /upload-manifests  →  enqueue ``hmo_manifest_upload`` job (201)
    GET  /coverage          →  HMO class → Wikidata projection report
    GET  /status            →  idle | built | uploaded | error + counts
    POST /build-items       →  enqueue ``hmo_item_build`` job (201)
    GET  /authority-conflicts → approved AuthorityMatch ID collisions
    POST /authority-conflicts/resolve → keep one / unapprove rest (W-109)

Bot credentials are no longer per-user — the server holds OAuth config
(Heroku env vars). Live writes require ``wikibase_cloud_configured`` on
the deployment; curators are attributed via ``wikibase_cloud_writes``.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.event import (
    ENTITY_TYPE_WIKIBASE_ITEM,
    OP_CREATE,
    OP_PATCH,
    ProjectEvent,
)
from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.models.run_job import (
    JOB_KIND_HMO_ITEM_BUILD,
    JOB_KIND_HMO_ITEM_UPLOAD,
    JOB_KIND_HMO_MANIFEST_BUILD,
    JOB_KIND_HMO_MANIFEST_UPLOAD,
)
from app.pipeline import hmo_studio as hmo_pipeline
from app.pipeline.hmo_authority_conflict_resolve import (
    load_run_authority_matches,
    resolve_authority_conflicts,
)
from app.pipeline.hmo_authority_gate import build_authority_conflict_report
from app.pipeline.run_job_params import prepare_job_params
from app.pipeline.run_job_service import ActiveJobError, create_job, serialise_job
from app.pipeline.rdf_build import (
    ensure_ttl_on_disk,
    rdf_output_path_for_run,
)
from app.routers.runs import (
    _apply_approval,
    _lookup_run_with_access,
    _record_match_event,
)
from app.services.wikibase_audit import WikibaseAuditContext
from app.settings import get_settings
from app.versioning import apply_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/runs", tags=["hmo-studio"])


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
    wikibase_configured: bool
    canonical_live_count: int = 0
    canonical_ready: bool = False


class HmoUploadRequest(BaseModel):
    dry_run: bool = Field(
        default=True,
        description="Default True — describes what would happen without "
                    "writing. Set False for live; live requires server "
                    "Wikibase Cloud OAuth to be configured.",
    )


class HmoResolvedClaimDto(BaseModel):
    property_id: str
    datatype: str
    value: Any


class HmoDeferredLinkDto(BaseModel):
    source_local_id: str
    property_id: str
    target_local_id: str


class HmoResolvedEntityDto(BaseModel):
    local_id: str
    labels: dict[str, str]
    descriptions: dict[str, str]
    class_qid: str
    source_uri: str
    claims: list[HmoResolvedClaimDto]
    deferred_links: list[HmoDeferredLinkDto]
    skipped_statements: list[str]


class HmoItemBuildResponse(BaseModel):
    from_cache: bool
    entity_count: int
    deferred_link_count: int
    skipped_statement_count: int
    entities: list[HmoResolvedEntityDto]


class HmoItemUploadRequest(BaseModel):
    dry_run: bool = Field(
        default=True,
        description="Default True — describes what would happen without "
                    "writing. Set False for live; live requires server "
                    "Wikibase Cloud OAuth to be configured.",
    )
    update_existing: bool = Field(
        default=False,
        description="When True, refresh labels/descriptions and merge new "
                    "claims on already-mapped items instead of skipping them.",
    )
    allow_shacl_errors: bool = Field(
        default=False,
        description="When True, allow upload of items with SHACL Violation/Error.",
    )


class AuthorityConflictOwnerDto(BaseModel):
    match_id: str
    entity_text: str
    matched_name: str = ""
    control_number: str = ""
    entity_kind: str = ""
    role: str = ""
    confidence: str = ""
    source: str = ""
    mazal_id: str = ""
    viaf_id: str = ""
    wikidata_qid: str = ""
    approved: bool = True


class AuthorityConflictGroupDto(BaseModel):
    kind: str
    identifier: str
    owners: list[AuthorityConflictOwnerDto]


class AuthorityInvalidDto(BaseModel):
    match_id: str
    entity_text: str
    kind: str
    identifier: str
    reason: str
    matched_name: str = ""
    control_number: str = ""
    role: str = ""
    approved: bool = True


class AuthorityConflictsResponse(BaseModel):
    ready: bool
    conflict_count: int = 0
    invalid_count: int = 0
    conflicts: list[AuthorityConflictGroupDto]
    invalid: list[AuthorityInvalidDto]
    unapproved_match_ids: list[str] = Field(default_factory=list)
    message: str = ""


class AuthorityConflictsResolveRequest(BaseModel):
    keep_match_ids: list[uuid.UUID] = Field(default_factory=list)
    unapprove_match_ids: list[uuid.UUID] = Field(default_factory=list)


class HmoItemUploadOutcomeDto(BaseModel):
    local_id: str
    source_uri: str
    status: str
    wikibase_id: str | None = None
    message: str = ""


class HmoDeferredLinkOutcomeDto(BaseModel):
    source_local_id: str
    property_id: str
    target_local_id: str
    status: str
    message: str = ""


class HmoItemUploadResponse(BaseModel):
    dry_run: bool
    created: int
    updated: int
    skipped: int
    failed: int
    blocked: int = 0
    linked: int
    unresolved_links: int
    outcomes: list[HmoItemUploadOutcomeDto]
    link_outcomes: list[HmoDeferredLinkOutcomeDto]


class HmoItemStatusResponse(BaseModel):
    build_present: bool
    entity_count: int
    deferred_link_count: int
    uploaded_count: int
    built_at: str | None


# ── Build ──────────────────────────────────────────────────────────────


@router.post(
    "/{run_id}/hmo-studio/build-manifests",
)
async def build_manifests(
    run_id: uuid.UUID,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enqueue IIIF manifest generation as a background job.

    Writes one ``MS_<shelfmark>.json`` per manuscript into the run's
    ``iiif_manifests/`` directory. Overwrites any existing manifests —
    the builder is deterministic, so re-running on the same TTL is safe.

    Authority-identifier conflicts (Rule W-95) gate Wikibase *item*
    upload, not local IIIF JSON generation from RDF.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    ttl_path = rdf_output_path_for_run(str(run_id))
    await ensure_ttl_on_disk(ttl_path, run_id, db)
    if not ttl_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No RDF graph for this run yet. Build the RDF (RDF Graph) "
                "before generating IIIF manifests."
            ),
        )
    params = await prepare_job_params(
        db, auth, run_id=run_id, kind=JOB_KIND_HMO_MANIFEST_BUILD, params={},
    )
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run_id,
            kind=JOB_KIND_HMO_MANIFEST_BUILD,
            params=params,
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "a manifest build job is already running",
                "job_id": str(exc.job_id),
            },
        ) from exc
    response.status_code = status.HTTP_201_CREATED
    return serialise_job(job)


# ── Upload ─────────────────────────────────────────────────────────────


@router.post(
    "/{run_id}/hmo-studio/upload-manifests",
)
async def upload_manifests(
    run_id: uuid.UUID,
    payload: HmoUploadRequest,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enqueue IIIF manifest upload / dry-run as a background job.

    Live writes require server-held Wikibase Cloud OAuth (validated in
    ``prepare_job_params``). Dry-run previews need no credentials but still
    run as a job so large corpora get progress + cancel (Rule W-107).
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    manifest_dir = hmo_pipeline.manifest_dir_for_run(str(run_id))
    if not manifest_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No IIIF manifests for this run yet. Click "
                "“Build manifests” first."
            ),
        )

    params = await prepare_job_params(
        db, auth, run_id=run_id, kind=JOB_KIND_HMO_MANIFEST_UPLOAD,
        params={"dry_run": payload.dry_run},
    )
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run_id,
            kind=JOB_KIND_HMO_MANIFEST_UPLOAD,
            params=params,
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "a manifest upload job is already running",
                "job_id": str(exc.job_id),
            },
        ) from exc
    response.status_code = status.HTTP_201_CREATED
    return serialise_job(job)


# ── Coverage report ────────────────────────────────────────────────────


async def _enqueue_coverage_job(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
) -> uuid.UUID:
    from app.models.run_job import JOB_KIND_HMO_COVERAGE  # noqa: PLC0415
    from app.pipeline.run_job_service import (  # noqa: PLC0415
        ActiveJobError,
        create_job,
        find_active_job,
    )

    active = await find_active_job(db, run_id=run_id, kind=JOB_KIND_HMO_COVERAGE)
    if active is not None:
        return active.id
    try:
        job = await create_job(
            db, project_id=project_id, run_id=run_id,
            kind=JOB_KIND_HMO_COVERAGE, params={}, created_by=user_id,
        )
        return job.id
    except ActiveJobError as exc:
        return exc.job_id


def _coverage_in_progress_detail(job_id: uuid.UUID) -> dict[str, str]:
    return {
        "code": "hmo_coverage_in_progress",
        "message": "HMO coverage report is building in the background.",
        "job_id": str(job_id),
    }


@router.get("/{run_id}/hmo-studio/coverage")
async def coverage(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the HMO → Wikidata projection-coverage JSON.

    Cached on disk after the first build. The cached file is returned
    verbatim if present; on a local cache miss (e.g. a Heroku deploy or
    dyno restart wiped the ephemeral filesystem) it falls back to the
    durable Postgres cache (``HmoCoverageCache``, keyed by a hash of the
    RDF TTL bytes) so an unchanged graph never pays for a rebuild twice.
    Only a genuine cache miss — no cached report anywhere, or the RDF
    graph changed since the last build — enqueues a background job and
    returns 409 with the job id; rebuilding inline used to hold the
    request (and its DB connection) open well past Heroku's 30s router
    timeout on a large run. The frontend polls
    ``GET /runs/{run_id}/jobs/{job_id}`` and re-fetches this endpoint
    once the job succeeds (the job writes both caches).
    """
    run = await _lookup_run_with_access(db, run_id, auth)
    cache = hmo_pipeline.coverage_path_for_run(str(run_id))
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(
                "Cached coverage at %s unreadable (%s); regenerating", cache, exc,
            )

    ttl_path = rdf_output_path_for_run(str(run_id))
    await ensure_ttl_on_disk(ttl_path, run_id, db)
    if not ttl_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No RDF graph for this run yet. Build the RDF (RDF Graph) "
                "before requesting coverage."
            ),
        )

    fingerprint = await hmo_pipeline.compute_coverage_fingerprint(ttl_path)
    db_report = await hmo_pipeline.load_cached_coverage_from_db(db, run_id, fingerprint)
    if db_report is not None:
        hmo_pipeline.cache_coverage_report(str(run_id), db_report)
        return db_report

    job_id = await _enqueue_coverage_job(
        db, project_id=run.project_id, run_id=run_id, user_id=auth.user.id,
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=_coverage_in_progress_detail(job_id),
    )


# ── Authority conflicts (HMO upload gate) ────────────────────────────────


def _authority_conflicts_response(payload: dict[str, Any]) -> AuthorityConflictsResponse:
    return AuthorityConflictsResponse(
        ready=bool(payload.get("ready")),
        conflict_count=int(payload.get("conflict_count") or 0),
        invalid_count=int(payload.get("invalid_count") or 0),
        conflicts=[
            AuthorityConflictGroupDto(
                kind=str(c.get("kind") or ""),
                identifier=str(c.get("identifier") or ""),
                owners=[AuthorityConflictOwnerDto(**o) for o in (c.get("owners") or [])],
            )
            for c in (payload.get("conflicts") or [])
        ],
        invalid=[
            AuthorityInvalidDto(
                match_id=str(i.get("match_id") or ""),
                entity_text=str(i.get("entity_text") or ""),
                kind=str(i.get("kind") or ""),
                identifier=str(i.get("identifier") or ""),
                reason=str(i.get("reason") or ""),
                matched_name=str(i.get("matched_name") or ""),
                control_number=str(i.get("control_number") or ""),
                role=str(i.get("role") or ""),
                approved=bool(i.get("approved", True)),
            )
            for i in (payload.get("invalid") or [])
        ],
        unapproved_match_ids=[str(x) for x in (payload.get("unapproved_match_ids") or [])],
        message=str(payload.get("message") or ""),
    )


@router.get(
    "/{run_id}/hmo-studio/authority-conflicts",
    response_model=AuthorityConflictsResponse,
)
async def get_authority_conflicts(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> AuthorityConflictsResponse:
    """List approved AuthorityMatch identifier collisions for this run.

    Read-only; does not reopen the retired standalone Authority mutation UI
    (Rule W-93). Used by the HMO Studio conflict resolver before upload.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)
    rows = await load_run_authority_matches(db, run_id)
    return _authority_conflicts_response(build_authority_conflict_report(rows))


@router.post(
    "/{run_id}/hmo-studio/authority-conflicts/resolve",
    response_model=AuthorityConflictsResponse,
)
async def resolve_hmo_authority_conflicts(
    run_id: uuid.UUID,
    payload: AuthorityConflictsResolveRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> AuthorityConflictsResponse:
    """Keep selected matches and unapprove the rest of each collision group.

    HMO-scoped escape hatch for Rule W-95 upload blocking — versioned
    unapprove via the same ``authority_match`` event path as legacy approve,
    without enabling ``legacy_authority_mutations_enabled``.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    if not payload.keep_match_ids and not payload.unapprove_match_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide keep_match_ids and/or unapprove_match_ids.",
        )
    try:
        result = await resolve_authority_conflicts(
            db,
            run_id=run_id,
            project_id=run.project_id,
            actor_id=auth.user.id,
            keep_match_ids=list(payload.keep_match_ids),
            unapprove_match_ids=list(payload.unapprove_match_ids),
            apply_approval=_apply_approval,
            record_event=_record_match_event,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await db.commit()
    return _authority_conflicts_response(result)


# ── Build items (Phase 4) ────────────────────────────────────────────────


@router.post(
    "/{run_id}/hmo-studio/build-items",
)
async def build_items(
    run_id: uuid.UUID,
    response: Response,
    force_rebuild: bool = Query(False, description="Bypass HmoStudioItemCache and re-export from RDF"),
    refresh_authority: bool = Query(True, description="Refresh authority evidence as part of every HMO entity build"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enqueue HMO Wikibase item build as a background job.

    Authority refresh + RDF rebuild + item export routinely exceed Heroku's
    30s HTTP timeout, so this endpoint always returns a ``run_jobs`` snapshot
    (poll ``GET /runs/{run_id}/jobs/{job_id}``). Every class/property
    referenced by the RDF graph must already have a live Wikibase id from
    the schema bootstrap — the worker fails closed with that message if not.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    if not refresh_authority:
        ttl_path = rdf_output_path_for_run(str(run_id))
        await ensure_ttl_on_disk(ttl_path, run_id, db)
        if not ttl_path.exists():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "No RDF graph for this run yet. Build the RDF (RDF Graph) "
                    "before building HMO Wikibase items."
                ),
            )

    params = await prepare_job_params(
        db, auth, run_id=run_id, kind=JOB_KIND_HMO_ITEM_BUILD,
        params={
            "force_rebuild": force_rebuild,
            "refresh_authority": refresh_authority,
        },
    )
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run_id,
            kind=JOB_KIND_HMO_ITEM_BUILD,
            params=params,
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "an HMO item build job is already running",
                "job_id": str(exc.job_id),
            },
        ) from exc
    response.status_code = status.HTTP_201_CREATED
    return serialise_job(job)


# ── Upload items (Phase 5) ───────────────────────────────────────────────


@router.post("/{run_id}/hmo-studio/upload-items")
async def upload_items(
    run_id: uuid.UUID,
    payload: HmoItemUploadRequest,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enqueue HMO item upload / dry-run as a background job.

    Requires ``build-items`` to have run first. Dry-run and live both run
    as ``hmo_item_upload`` so large corpora get progress + cancel (Rule
    W-107). Live writes require server-held Wikibase Cloud OAuth.

    An already-uploaded item is skipped unless ``update_existing=True``,
    in which case its labels/descriptions/claims are refreshed in place.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    cache_row = (
        await db.execute(select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == run_id))
    ).scalar_one_or_none()
    if cache_row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No item build exists for run {run_id}. Call build-items first."
            ),
        )

    params = await prepare_job_params(
        db, auth, run_id=run_id, kind=JOB_KIND_HMO_ITEM_UPLOAD,
        params={
            "dry_run": payload.dry_run,
            "update_existing": payload.update_existing,
            "allow_shacl_errors": payload.allow_shacl_errors,
        },
    )
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run_id,
            kind=JOB_KIND_HMO_ITEM_UPLOAD,
            params=params,
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "an item upload job is already running",
                "job_id": str(exc.job_id),
            },
        ) from exc
    response.status_code = status.HTTP_201_CREATED
    return serialise_job(job)


@router.get("/{run_id}/hmo-studio/item-status", response_model=HmoItemStatusResponse)
async def item_status(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoItemStatusResponse:
    """Build-cache presence + upload counts for this run's items."""
    await _lookup_run_with_access(db, run_id, auth)

    cache_row = (
        await db.execute(
            select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == run_id)
        )
    ).scalar_one_or_none()
    uploaded_count = (
        await db.execute(
            select(func.count(WikibaseEntityMapping.id)).where(
                WikibaseEntityMapping.run_id == run_id,
                WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
            )
        )
    ).scalar_one()

    return HmoItemStatusResponse(
        build_present=cache_row is not None,
        entity_count=cache_row.entity_count if cache_row else 0,
        deferred_link_count=cache_row.deferred_link_count if cache_row else 0,
        uploaded_count=uploaded_count,
        built_at=cache_row.built_at.isoformat() if cache_row else None,
    )


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
    await ensure_ttl_on_disk(ttl_path, run_id, db)
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

    wikibase_configured = get_settings().wikibase_cloud_configured
    cache_row = (await db.execute(select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == run_id))).scalar_one_or_none()
    canonical_live_count = sum(1 for item in (cache_row.resolved_entities if cache_row else []) if item.get("canonical_live"))
    canonical_ready = bool(cache_row and cache_row.resolved_entities and canonical_live_count == len(cache_row.resolved_entities))

    return HmoStatus(
        state=state,
        rdf_present=ttl_path.exists(),
        manifest_count=manifest_count,
        coverage_present=coverage_cache.exists(),
        last_upload_at=last_upload_at,
        last_upload=last_upload,
        wikibase_configured=wikibase_configured,
        canonical_live_count=canonical_live_count,
        canonical_ready=canonical_ready,
    )


# ── Helpers ────────────────────────────────────────────────────────────


async def _audit_manifest_upload_intent(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    manifest_dir: Path,
    dry_run: bool,
) -> None:
    """Emit one ``wikibase_item`` versioning event per manifest the
    upload pipeline will try to write.

    Called BEFORE the actual HTTP write so the audit trail records
    *intent*, not outcome — useful for forensic recovery when a remote
    write fails or the network drops mid-batch. A failure in this
    helper must never 500 the surrounding upload request, so every
    error path is logged and swallowed.

    The helper commits its own writes (the surrounding handler does
    not own a transaction); the commit happens once at the end so the
    audit batch lands atomically even if one row in the middle fails
    to serialise.
    """
    manifest_paths = sorted(manifest_dir.glob("MS_*.json"))
    if not manifest_paths:
        return

    emitted = 0
    for manifest_path in manifest_paths:
        shelfmark = manifest_path.stem[len("MS_"):]
        page_title = f"IIIF:MS_{shelfmark}/manifest.json"
        try:
            payload_dict = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(
                "Skipping audit for unreadable manifest %s: %s",
                manifest_path, exc,
            )
            continue

        try:
            has_history = (
                await db.execute(
                    select(ProjectEvent.id)
                    .where(
                        ProjectEvent.entity_type == ENTITY_TYPE_WIKIBASE_ITEM,
                        ProjectEvent.entity_id == page_title,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none() is not None
            op_kind = OP_PATCH if has_history else OP_CREATE
            await apply_event(
                db,
                project_id=project_id,
                entity_type=ENTITY_TYPE_WIKIBASE_ITEM,
                entity_id=page_title,
                op=op_kind,
                new_state=payload_dict,
                actor_id=actor_id,
                message=(
                    f"wikibase manifest upload intent "
                    f"({'dry-run' if dry_run else 'live'})"
                ),
            )
            emitted += 1
        except Exception as exc:  # noqa: BLE001 — versioning must never 500
            logger.warning(
                "apply_event failed for wikibase_item %s: %s",
                page_title, exc,
            )

    if emitted == 0:
        return
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — never break the upload on audit failure
        logger.warning(
            "Failed to commit %d wikibase_item audit events: %s", emitted, exc,
        )
        try:
            await db.rollback()
        except Exception as rollback_exc:  # noqa: BLE001
            logger.warning(
                "Rollback after audit commit failure also failed: %s",
                rollback_exc,
            )


def _iso_mtime(path: Path) -> str:
    """Filesystem-mtime as an ISO 8601 string (UTC)."""
    from datetime import datetime, timezone  # noqa: PLC0415

    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
