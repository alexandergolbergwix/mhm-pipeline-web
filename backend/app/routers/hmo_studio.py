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
    POST /build-items       →  resolve RDF instances against the live
                                schema (Phase 4) into real-PID/QID-shaped
                                item drafts, cached per-run

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

from fastapi import APIRouter, Depends, HTTPException, Response, status
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
from app.models.run_job import JOB_KIND_HMO_ITEM_UPLOAD
from app.pipeline import hmo_item_build
from app.pipeline import hmo_item_upload
from app.pipeline import hmo_studio as hmo_pipeline
from app.pipeline.run_job_params import prepare_job_params
from app.pipeline.run_job_service import ActiveJobError, create_job, serialise_job
from app.pipeline.rdf_build import rdf_output_path_for_run
from app.routers.runs import _lookup_run_with_access
from app.services.wikibase_audit import WikibaseAuditContext
from app.services.wikibase_credentials import build_server_wikibase_writer
from app.settings import get_settings
from app.versioning import apply_event
from converter.wikibase.resolved_models import UnmappedOntologyUriError

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
    skipped: int
    failed: int
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

    Live writes require server-held Wikibase Cloud OAuth. Dry-run is
    allowed without credentials (handy for previewing what would be sent).
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

    writer = None
    if not payload.dry_run:
        writer = build_server_wikibase_writer()

    # Audit upload intent BEFORE the network call — one versioning event
    # per manifest the pipeline will try to write. We persist the audit
    # trail even if the remote write later fails (or never happens, in
    # dry-run mode). Failure of the audit must NEVER 500 the upload.
    await _audit_manifest_upload_intent(
        db,
        project_id=run.project_id,
        actor_id=auth.user.id,
        manifest_dir=manifest_dir,
        dry_run=payload.dry_run,
    )

    audit_ctx = None
    if not payload.dry_run:
        from app.models.wikibase_cloud_write import CHANNEL_MANIFEST_UPLOAD  # noqa: PLC0415

        audit_ctx = WikibaseAuditContext(
            actor_user_id=auth.user.id,
            project_id=run.project_id,
            run_id=run_id,
            channel=CHANNEL_MANIFEST_UPLOAD,
        )

    result = await hmo_pipeline.upload_manifests_for_run(
        manifest_dir=manifest_dir,
        writer=writer,
        dry_run=payload.dry_run,
        db=db,
        audit_ctx=audit_ctx,
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
    verbatim if present; on a cache miss this used to rebuild inline
    (parsing the TTL twice + building Wikidata item drafts), which on a
    large run took well over Heroku's 30s router timeout and pinned
    this request's DB connection the whole time. It now enqueues a
    background job and returns 409 with the job id — the frontend polls
    ``GET /runs/{run_id}/jobs/{job_id}`` and re-fetches this endpoint
    once the job succeeds (the job also writes the same on-disk cache).
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
    if not ttl_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No RDF graph for this run yet. Build the RDF (RDF Graph) "
                "before requesting coverage."
            ),
        )

    job_id = await _enqueue_coverage_job(
        db, project_id=run.project_id, run_id=run_id, user_id=auth.user.id,
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=_coverage_in_progress_detail(job_id),
    )


# ── Build items (Phase 4) ────────────────────────────────────────────────


@router.post(
    "/{run_id}/hmo-studio/build-items",
    response_model=HmoItemBuildResponse,
)
async def build_items(
    run_id: uuid.UUID,
    force_rebuild: bool = False,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoItemBuildResponse:
    """Resolve the run's RDF instances against the live schema mapping.

    Every class/property referenced by the RDF graph must already have
    a live Wikibase id from the schema bootstrap
    (``/api/hmo-wikibase-schema/bootstrap``) — a 409 here means the
    ontology grew since the last bootstrap; re-run it and retry.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)
    ttl_path = rdf_output_path_for_run(str(run_id))
    if not ttl_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No RDF graph for this run yet. Build the RDF (RDF Graph) "
                "before building Wikibase items."
            ),
        )
    try:
        result = await hmo_item_build.build_items_for_run(
            db, run_id, ttl_path, force_rebuild=force_rebuild,
        )
    except UnmappedOntologyUriError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The RDF graph references ontology classes/properties with "
                "no live Wikibase mapping yet. Run the schema bootstrap "
                f"first. Missing: {', '.join(exc.missing_uris[:10])}"
            ),
        ) from exc
    return HmoItemBuildResponse(
        from_cache=result.from_cache,
        entity_count=result.entity_count,
        deferred_link_count=result.deferred_link_count,
        skipped_statement_count=result.skipped_statement_count,
        entities=[
            HmoResolvedEntityDto(
                local_id=e.local_id,
                labels=e.labels,
                descriptions=e.descriptions,
                class_qid=e.class_qid,
                source_uri=e.source_uri,
                claims=[HmoResolvedClaimDto(**c.to_dict()) for c in e.claims],
                deferred_links=[HmoDeferredLinkDto(**d.to_dict()) for d in e.deferred_links],
                skipped_statements=e.skipped_statements,
            )
            for e in result.entities
        ],
    )


# ── Upload items (Phase 5) ───────────────────────────────────────────────


@router.post("/{run_id}/hmo-studio/upload-items")
async def upload_items(
    run_id: uuid.UUID,
    payload: HmoItemUploadRequest,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any] | HmoItemUploadResponse:
    """Upload the run's most recent item build (create-only, two-pass).

    Requires ``build-items`` to have run first. Dry-run (the default) is a
    fast, no-network preview and stays synchronous. A live upload makes
    one sequential Wikibase Cloud write per item + deferred link —
    thousands of calls, far over Heroku's 30s HTTP timeout — so it spawns
    a ``run_jobs`` background job and returns the job snapshot right away;
    poll ``GET /runs/{run_id}/jobs/{job_id}`` for progress.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    if not payload.dry_run:
        params = await prepare_job_params(
            db, auth, run_id=run_id, kind=JOB_KIND_HMO_ITEM_UPLOAD, params={},
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

    try:
        result = await hmo_item_upload.upload_items_for_run(
            db, run_id, writer=None, dry_run=True,
        )
    except hmo_item_upload.ItemBuildMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        ) from exc

    return HmoItemUploadResponse(
        dry_run=result.dry_run,
        created=result.created,
        skipped=result.skipped,
        failed=result.failed,
        linked=result.linked,
        unresolved_links=result.unresolved_links,
        outcomes=[HmoItemUploadOutcomeDto(**o.__dict__) for o in result.outcomes],
        link_outcomes=[HmoDeferredLinkOutcomeDto(**o.__dict__) for o in result.link_outcomes],
    )


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

    return HmoStatus(
        state=state,
        rdf_present=ttl_path.exists(),
        manifest_count=manifest_count,
        coverage_present=coverage_cache.exists(),
        last_upload_at=last_upload_at,
        last_upload=last_upload,
        wikibase_configured=wikibase_configured,
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
