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

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.event import (
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    OP_CREATE,
    OP_PATCH,
    ProjectEvent,
)
from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, Run, RunRecord
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.models.run_job import JOB_KIND_WIKIDATA_STUDIO_BUILD, JOB_KIND_WIKIDATA_VERIFY
from app.pipeline.verify_session_store import load_verify_session
from app.pipeline import agent_actions, wikidata_actions, wikidata_studio, wikidata_upload
from app.pipeline.agent_runner import (
    AgentEvent,
    locate_eval_agent,
    list_verify_sessions,
    new_session_id,
    persist_session_event,
    read_run_verdicts,
    read_verify_session,
    resolve_verify_session_dir,
    resolve_verify_state_dir,
    spawn_eval_agent_run,
    sse_stream,
)
from app.pipeline.inference_cache import read_from_inference_cache, write_to_inference_cache
from app.routers.runs import _lookup_run_with_access  # noqa: PLF401 — module-internal
from app.versioning import apply_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["wikidata-studio"])


async def studio_items_for_project(
    run_ids: list[str], db: AsyncSession, *, approved_only: bool = True,
) -> list[dict[str, Any]]:
    """All built Wikidata Studio item dicts cached across a project's runs.

    Reads the ``WikidataStudioCache`` rows only — it never triggers a
    (slow, network-bound) rebuild. Runs without a cached build contribute
    nothing. Each item dict already carries ``local_id`` + ``existing_qid``
    (stamped in ``build_studio`` before the cache upsert).
    """
    if not run_ids:
        return []
    rows = (
        await db.execute(
            select(WikidataStudioCache).where(
                WikidataStudioCache.run_id.in_([uuid.UUID(r) for r in run_ids]),
                WikidataStudioCache.approved_only == approved_only,
            )
        )
    ).scalars().all()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.extend(row.result_items or [])
    return items


async def studio_fingerprints_for_project(
    run_ids: list[str], db: AsyncSession, *, approved_only: bool = True,
) -> dict[str, str]:
    """Per-run Wikidata Studio cache fingerprint, for summary cache-keying.

    Folding this into the research-summary cache key makes approving a match
    (which changes the Studio fingerprint) invalidate the aggregated summary.
    """
    if not run_ids:
        return {}
    rows = (
        await db.execute(
            select(WikidataStudioCache).where(
                WikidataStudioCache.run_id.in_([uuid.UUID(r) for r in run_ids]),
                WikidataStudioCache.approved_only == approved_only,
            )
        )
    ).scalars().all()
    return {str(row.run_id): row.input_fingerprint for row in rows}


class StudioSummary(BaseModel):
    total_items: int
    manuscripts: int
    persons: int
    works: int
    statements: int


class PropertyInfo(BaseModel):
    id: str
    label: str


class StudioBuildResponse(BaseModel):
    items: list[dict[str, Any]]
    quickstatements: str
    summary: StudioSummary
    approved_match_count: int     # how many of the feed are approved
    pending_match_count: int      # how many are still pending review
    used_match_count: int         # what we actually fed the builder
    approved_only: bool           # which mode was used
    record_count: int
    # Server-side slicing metadata
    total: int                    # total items matching current slice params
    page: int
    page_size: int
    # Precomputed aggregates to replace client-side scans
    approved_item_count: int      # items with approved==True in the full build
    properties: list[PropertyInfo]        # distinct P-ids in the full build
    property_labels: dict[str, str]       # P/Q id → label map for label-store seeding


class VerifyStartRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=64)
    item_ids: list[str] | None = None
    approved_only: bool = False  # AI verification audits all items, not just approved-match ones
    override_cache: bool = False
    tier_model: str | None = Field(default=None, max_length=64)


@router.get("/{run_id}/wikidata-studio/ai-verify/actions")
async def list_verify_actions(
    run_id: uuid.UUID,
    scope_kind: str = Query("selection", pattern=r"^(single|selection|all)$"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return [
        wikidata_actions.to_dict(a)
        for a in wikidata_actions.list_actions(scope_kind=scope_kind)  # type: ignore[arg-type]
    ]


@router.post("/{run_id}/wikidata-studio/ai-verify/start-stream")
async def start_verify_stream(
    run_id: uuid.UUID,
    payload: VerifyStartRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _lookup_run_with_access(db, run_id, auth, write=False)

    action = wikidata_actions.get_action(payload.action_id)
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown action_id {payload.action_id!r}",
        )

    items, marc_records = await _fetch_wikidata_verify_items(
        db, run_id, auth,
        item_ids=payload.item_ids,
        approved_only=payload.approved_only,
    )
    if not items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no Wikidata Studio items in scope",
        )
    if len(items) < action.min_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"action {action.id!r} requires at least "
                f"{action.min_candidates} candidates; got {len(items)}"
            ),
        )

    from app.pipeline.ai_verifier import GEMINI_MODEL  # noqa: PLC0415

    judge_model = payload.tier_model or GEMINI_MODEL
    pre_cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
    uncached: list[dict[str, Any]] = []
    if not payload.override_cache:
        for item in items:
            hit = await read_from_inference_cache(
                db,
                kind="ai_verdict",
                query_summary=_wikidata_verdict_query_summary(item, judge_model),
            )
            if hit is not None:
                pre_cached.append((item, hit))
            else:
                uncached.append(item)
    else:
        uncached = list(items)

    api_key = await _resolve_gemini_key(db, auth)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No Gemini API key configured. Open Settings -> "
                "Credentials and add one from "
                "https://aistudio.google.com/app/apikey."
            ),
        )

    session_id = new_session_id()
    return StreamingResponse(
        sse_stream(_wikidata_verify_event_stream(
            run_id=str(run_id),
            session_id=session_id,
            action=action,
            items=items,
            uncached_items=uncached,
            pre_cached=pre_cached,
            marc_records=marc_records,
            api_key=api_key,
            override_cache=payload.override_cache,
            tier_model=payload.tier_model,
        )),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id":      session_id,
        },
    )


@router.get("/{run_id}/wikidata-studio/ai-verify/sessions")
async def list_wikidata_verify_sessions(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return _list_wikidata_sessions(str(run_id))


@router.get("/{run_id}/wikidata-studio/ai-verify/sessions/{session_id}")
async def get_wikidata_verify_session(
    run_id: uuid.UUID,
    session_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    data = await load_verify_session(
        db,
        run_id=run_id,
        session_id=session_id,
        channel=_WIKIDATA_VERIFY_CHANNEL,
        job_kind=JOB_KIND_WIKIDATA_VERIFY,
    )
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        )
    return data


def _collect_hebrew_label_candidates(
    marc_records: list[dict[str, Any]],
) -> list[str]:
    """Collect every Hebrew string the synchronous item builder will hand to
    ``english_label_for_hebrew`` (work titles + person P2093 names).

    Over-collecting is harmless: the pre-warm cache is keyed by the same
    normalised Hebrew key the waterfall uses, so extra entries only add cache
    warmth. We mirror the builder's title cleaning so the keys match.
    """
    from app.pipeline.marc_ingest import prepare_record_for_pipeline  # noqa: PLC0415
    from converter.wikidata.hebrew_translit import _has_hebrew  # noqa: PLC0415
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        _clean_work_title,
        _is_noise_work_title,
    )

    out: set[str] = set()

    def _add(value: Any) -> None:
        s = str(value or "").strip()
        if s and _has_hebrew(s):
            out.add(s)

    for rec in marc_records:
        prepared = prepare_record_for_pipeline(dict(rec))
        _add(prepared.get("title"))
        for slot in ("authors", "contributors", "subjects"):
            for entry in prepared.get(slot) or []:
                if isinstance(entry, dict):
                    _add(entry.get("name"))
                elif isinstance(entry, str):
                    _add(entry)
        for entry in prepared.get("contents") or []:
            raw_title = ""
            if isinstance(entry, dict):
                raw_title = str(entry.get("title") or "").strip()
            elif isinstance(entry, str):
                raw_title = entry.strip()
            if not raw_title:
                continue
            _add(raw_title)
            cleaned = _clean_work_title(raw_title)
            if cleaned and not _is_noise_work_title(cleaned):
                _add(cleaned)
        for ent in prepared.get("entities") or []:
            if isinstance(ent, dict):
                _add(ent.get("text"))

    return [s for s in out if s]


async def _prewarm_transliterations(
    db: AsyncSession,
    *,
    marc_records: list[dict[str, Any]],
    user_id: Any,
    concurrency: int = 12,
) -> dict[str, str | None]:
    """Concurrently compute every Hebrew→Latin label the build needs.

    Each computation runs the full waterfall in a worker thread (so the
    blocking SPARQL/Modal HTTP calls run in parallel) and is wrapped in the
    inference cache (kind=``translit.label``) so the work is incremental
    across retries: a build that times out still persists the labels it
    finished, and the next attempt resumes from the cache.

    Returns a ``{raw_hebrew: latin_or_None}`` mapping.
    """
    import asyncio  # noqa: PLC0415

    from app.pipeline.inference_cache import cache_lookup_or_call  # noqa: PLC0415
    from converter.wikidata.hebrew_translit import (  # noqa: PLC0415
        english_label_for_hebrew,
    )

    candidates = _collect_hebrew_label_candidates(marc_records)
    if not candidates:
        return {}

    sem = asyncio.Semaphore(concurrency)

    async def _one(raw: str) -> tuple[str, str | None]:
        async def _fetch() -> str | None:
            return await asyncio.to_thread(
                english_label_for_hebrew, raw, None, allow_algorithmic=False,
            )

        async with sem:
            try:
                label = await cache_lookup_or_call(
                    db,
                    kind="translit.label",
                    query_summary={"backend": "waterfall", "text": raw},
                    fetch=_fetch,
                    user_id=user_id,
                )
            except Exception:  # noqa: BLE001 — never let one string break the build
                label = None
        return raw, label

    results = await asyncio.gather(*(_one(c) for c in candidates))
    return dict(results)


async def _load_studio_build_rows(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> tuple[
    list[RunRecord],
    list[AuthorityMatch],
    list[ExtractionApproval],
    list[WikidataItemOverride],
]:
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
    entity_rows = (
        await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
        )
    ).scalars().all()
    override_rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()
    return records, list(all_matches), list(entity_rows), list(override_rows)


async def _get_studio_cache_row(
    db: AsyncSession,
    run_id: uuid.UUID,
    approved_only: bool,
) -> WikidataStudioCache | None:
    return (
        await db.execute(
            select(WikidataStudioCache).where(
                WikidataStudioCache.run_id == run_id,
                WikidataStudioCache.approved_only == approved_only,
            )
        )
    ).scalar_one_or_none()


async def execute_studio_build(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    approved_only: bool,
    force_rebuild: bool,
    run_user_id: uuid.UUID | None,
) -> WikidataStudioCache:
    """Run the full item builder and upsert the Postgres cache.

    Background jobs call this directly so the work never runs inside a
    Heroku HTTP request (30 s router timeout).
    """
    records, all_matches, entity_rows, override_rows = await _load_studio_build_rows(
        db, run_id,
    )
    fingerprint = wikidata_studio.compute_build_fingerprint(
        records, all_matches, entity_rows, override_rows, approved_only,
    )
    cached = await _get_studio_cache_row(db, run_id, approved_only)

    if not force_rebuild and cached is not None and cached.input_fingerprint == fingerprint:
        return cached

    approved_count = sum(1 for m in all_matches if m.approved)
    pending_count = len(all_matches) - approved_count
    matches = [m for m in all_matches if m.approved] if approved_only else list(all_matches)

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

    overrides = {
        r.local_id: {
            "labels":            r.labels,
            "descriptions":      r.descriptions,
            "aliases":           r.aliases,
            "add_statements":    r.add_statements,
            "remove_statements": r.remove_statements,
            "statement_edits":   r.statement_edits,
        }
        for r in override_rows
    }
    entities_by_cn = _group_entity_rows(entity_rows, approved_only)
    overrides_approved = {r.local_id: r.approved for r in override_rows}

    from converter.wikidata import hebrew_translit  # noqa: PLC0415

    prewarmed = await _prewarm_transliterations(
        db, marc_records=marc_records, user_id=run_user_id,
    )
    hebrew_translit.set_prewarmed_labels(prewarmed)
    hebrew_translit.set_sync_network_disabled(True)
    try:
        result = await wikidata_studio.build_items_for_run(
            marc_records=marc_records, approved_matches=approved_matches,
            entities_by_cn=entities_by_cn,
            overrides=overrides, return_native=True,
        )
    finally:
        hebrew_translit.set_sync_network_disabled(False)
        hebrew_translit.clear_prewarmed_labels()

    if result.get("native_items"):
        for it_dict, it_native in zip(
            result["items"], result["native_items"], strict=True,
        ):
            lid = wikidata_studio.local_id_for_item(it_native)
            it_dict["local_id"] = lid
            it_dict["approved"] = overrides_approved.get(lid)

    summary_dict = result["summary"]
    await _upsert_studio_cache(
        db, run_id=run_id, approved_only=approved_only,
        fingerprint=fingerprint,
        items=result["items"],
        quickstatements=result["quickstatements"],
        summary=summary_dict,
        approved_match_count=approved_count,
        pending_match_count=pending_count,
        used_match_count=len(approved_matches),
        record_count=len(marc_records),
        existing=cached,
    )
    await db.commit()
    row = await _get_studio_cache_row(db, run_id, approved_only)
    if row is None:
        raise RuntimeError(f"studio cache missing after build for run {run_id}")
    return row


async def _enqueue_studio_build_job(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    approved_only: bool,
    force_rebuild: bool,
    user_id: uuid.UUID,
) -> uuid.UUID:
    from app.pipeline.run_job_service import (  # noqa: PLC0415
        ActiveJobError,
        create_job,
        find_active_job,
    )

    active = await find_active_job(
        db, run_id=run_id, kind=JOB_KIND_WIKIDATA_STUDIO_BUILD,
    )
    if active is not None:
        return active.id
    try:
        job = await create_job(
            db,
            project_id=project_id,
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_STUDIO_BUILD,
            params={
                "approved_only": approved_only,
                "force_rebuild": force_rebuild,
            },
            created_by=user_id,
        )
        return job.id
    except ActiveJobError as exc:
        return exc.job_id


def _studio_build_in_progress_detail(job_id: uuid.UUID) -> dict[str, str]:
    return {
        "code": "studio_build_in_progress",
        "message": "Wikidata Studio build is running in the background.",
        "job_id": str(job_id),
    }


@router.get("/{run_id}/wikidata-studio", response_model=StudioBuildResponse)
async def build_studio(
    run_id: uuid.UUID,
    approved_only: bool = Query(
        default=True,
        description="When true (default), only approved authority matches "
                    "and NER entities feed the item builder, matching the "
                    "'ship this in the final output' semantics of the "
                    "approval stores. Pass false to preview all candidates.",
    ),
    force_rebuild: bool = Query(
        default=False,
        description="When true, skip the fingerprint cache and rebuild from "
                    "scratch. The result is still written to cache so the next "
                    "normal GET is fast. Does not affect the inference cache "
                    "(VIAF / authority calls).",
    ),
    # ── server-side slicing params (applied AFTER cache load) ──────────
    entity_type: str | None = Query(
        default=None,
        description="Filter by entity_type (manuscript / person / work). "
                    "Omit or pass null for all types.",
    ),
    q: str | None = Query(
        default=None,
        description="Substring search across labels, descriptions, aliases, "
                    "existing_qid, and entity_type.",
    ),
    sort: str = Query(
        default="label",
        description="Sort key: label | statements | entity_type | wikidata.",
    ),
    sort_dir: str = Query(
        default="asc",
        description="Sort direction: asc | desc.",
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    page_size: int = Query(
        default=50, ge=1, le=500,
        description="Items per page. Max 500.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StudioBuildResponse:
    run = await _lookup_run_with_access(db, run_id, auth)

    records, all_matches, entity_rows, override_rows = await _load_studio_build_rows(
        db, run_id,
    )

    fingerprint = wikidata_studio.compute_build_fingerprint(
        records, all_matches, entity_rows, override_rows, approved_only,
    )

    cached = await _get_studio_cache_row(db, run_id, approved_only)

    if not force_rebuild and cached is not None and cached.input_fingerprint == fingerprint:
        logger.debug("wikidata-studio cache hit for run %s (fp=%s)", run_id, fingerprint[:8])
        sliced, total, props, plabels, approved_item_count = _slice_items(
            cached.result_items, entity_type=entity_type, q=q,
            sort=sort, sort_dir=sort_dir, page=page, page_size=page_size,
        )
        return StudioBuildResponse(
            items=sliced,
            quickstatements=cached.quickstatements,
            summary=StudioSummary(**cached.summary),
            approved_match_count=cached.approved_match_count,
            pending_match_count=cached.pending_match_count,
            used_match_count=cached.used_match_count,
            approved_only=approved_only,
            record_count=cached.record_count,
            total=total,
            page=page,
            page_size=page_size,
            approved_item_count=approved_item_count,
            properties=props,
            property_labels=plabels,
        )

    logger.debug("wikidata-studio cache miss for run %s (fp=%s)", run_id, fingerprint[:8])
    job_id = await _enqueue_studio_build_job(
        db,
        project_id=run.project_id,
        run_id=run_id,
        approved_only=approved_only,
        force_rebuild=force_rebuild,
        user_id=auth.user.id,
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=_studio_build_in_progress_detail(job_id),
    )


@router.get("/{run_id}/wikidata-studio/quickstatements.txt", response_class=PlainTextResponse)
async def download_quickstatements(
    run_id: uuid.UUID,
    approved_only: bool = Query(default=True),
    item_approved_only: bool = Query(
        default=False,
        description="When true, only include items where the curator has "
                    "explicitly ticked 'Approved' in the Studio item overlay. "
                    "Independent of approved_only (which filters authority matches).",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Plain-text QuickStatements TSV — paste into
    https://quickstatements.toolforge.org."""
    if item_approved_only:
        native = await _build_native_items(db, run_id, auth, approved_only=approved_only)
        override_rows = (
            await db.execute(
                select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
            )
        ).scalars().all()
        approved_ids = {r.local_id for r in override_rows if r.approved}
        filtered = [it for it in native if wikidata_studio.local_id_for_item(it) in approved_ids]
        qs_text = await wikidata_studio.quickstatements_for_items(filtered)
    else:
        cached = await _get_studio_cache_row(db, run_id, approved_only)
        if cached is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Wikidata Studio has not been built for this run yet — "
                    "open the Studio page and wait for the build to finish."
                ),
            )
        qs_text = cached.quickstatements

    suffix = "approved" if item_approved_only else ("match-approved" if approved_only else "all")
    return PlainTextResponse(
        qs_text,
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
    approved_only: bool = Query(default=True),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ReconcileResponse:
    """PREVIEW ONLY. For every built item, SPARQL-query Wikidata to find an
    existing QID (manuscripts by P3959, persons by the conflict-checked
    identifier path). **Never** writes to Wikidata.

    This is a curator-facing preview: it does NOT change what a subsequent
    upload does. The authoritative reconcile runs again *inside*
    ``POST /upload`` (see ``wikidata_upload.upload_items``) so the upload
    decision can never drift from a stale preview, and so a lookup that fails
    here cannot leave a half-reconciled item eligible for accidental
    creation."""
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
    item_approved_only: bool = Query(
        default=False,
        description="When true, only upload items where the curator has "
                    "explicitly ticked 'Approved' in the Studio item overlay.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> UploadResponse:
    """Live uploads ALWAYS use the user's own Wikidata token (stored
    encrypted via the Settings page) and route through the real
    ``WikidataUploader`` with all four modification guards intact.

    Before any write, ``upload_items`` reconciles each item against live
    Wikidata (fail-closed: a lookup that cannot be completed BLOCKS creation,
    never mints a duplicate) and runs ``item_validator.validate_item`` as a
    hard gate (any ERROR-severity issue blocks the write). Dry-run reports the
    same create/update/BLOCKED decision the live run would take."""
    import os  # noqa: PLC0415

    # Build the items first (with the latest approval state).
    native = await _build_native_items(db, run_id, auth, approved_only=approved_only)

    if item_approved_only:
        override_rows = (
            await db.execute(
                select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
            )
        ).scalars().all()
        approved_ids = {r.local_id for r in override_rows if r.approved}
        native = [it for it in native if wikidata_studio.local_id_for_item(it) in approved_ids]

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


# ── Per-item editing (curator overrides) ───────────────────────────────


class ItemOverridePayload(BaseModel):
    """Partial update — every field optional. The persisted row is
    merged with the existing override, so the UI can PATCH one tab
    at a time."""
    labels:            dict[str, str | None] | None = None
    descriptions:      dict[str, str | None] | None = None
    aliases:           dict[str, list[str] | None] | None = None
    add_statements:    list[dict[str, Any]] | None = None
    remove_statements: list[int] | None = None
    statement_edits:   dict[str, dict[str, Any]] | None = None
    approved:          bool | None = None


class ItemOverrideResponse(BaseModel):
    run_id: uuid.UUID
    local_id: str
    labels: dict[str, Any]
    descriptions: dict[str, Any]
    aliases: dict[str, Any]
    add_statements: list[dict[str, Any]]
    remove_statements: list[int]
    statement_edits: dict[str, Any]
    approved: bool | None = None


@router.patch(
    "/{run_id}/wikidata-studio/items/{local_id:path}",
    response_model=ItemOverrideResponse,
)
async def patch_item_override(
    run_id: uuid.UUID, local_id: str,
    payload: ItemOverridePayload,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ItemOverrideResponse:
    """Persist a curator override for one Studio item.

    All fields optional. Sending ``labels: {"he": null}`` clears the
    Hebrew label override (reverts to whatever the builder produced).
    Statement edits use ``{"<index>": {"value": "Q5"}}`` — index is
    relative to the builder output AFTER removals are applied.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    row = (
        await db.execute(
            select(WikidataItemOverride).where(
                WikidataItemOverride.run_id == run_id,
                WikidataItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = WikidataItemOverride(
            run_id=run_id, local_id=local_id, updated_by=auth.user.id,
        )
        db.add(row)
        # Flush so the python-side UUID default is materialised before
        # we use ``row.id`` as the versioning entity id.
        await db.flush()

    if payload.labels is not None:
        # Merge non-null, drop keys set to null. Force a new dict so
        # SQLAlchemy notices the change.
        new = dict(row.labels or {})
        for k, v in payload.labels.items():
            if v is None: new.pop(k, None)
            else:         new[k] = v
        row.labels = new
    if payload.descriptions is not None:
        new = dict(row.descriptions or {})
        for k, v in payload.descriptions.items():
            if v is None: new.pop(k, None)
            else:         new[k] = v
        row.descriptions = new
    if payload.aliases is not None:
        new = dict(row.aliases or {})
        for lang, vals in payload.aliases.items():
            if vals is None: new.pop(lang, None)
            else:            new[lang] = list(vals)
        row.aliases = new
    if payload.add_statements is not None:
        row.add_statements = list(payload.add_statements)
    if payload.remove_statements is not None:
        row.remove_statements = list(payload.remove_statements)
    if payload.statement_edits is not None:
        new_edits = dict(row.statement_edits or {})
        for k, v in payload.statement_edits.items():
            if v is None: new_edits.pop(k, None)
            else:         new_edits[k] = v
        row.statement_edits = new_edits
    if payload.approved is not None:
        row.approved = payload.approved

    row.updated_by = auth.user.id

    # Versioning event — audit the override edit on the same transaction
    # as the row write. Failure must NEVER 500 the request.
    entity_id_str = str(row.id)
    try:
        has_history = (
            await db.execute(
                select(ProjectEvent.id)
                .where(
                    ProjectEvent.entity_type == ENTITY_TYPE_WIKIDATA_OVERRIDE,
                    ProjectEvent.entity_id == entity_id_str,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        op_kind = OP_PATCH if has_history else OP_CREATE
        new_state = {
            "labels":            dict(row.labels or {}),
            "descriptions":      dict(row.descriptions or {}),
            "aliases":           dict(row.aliases or {}),
            "add_statements":    list(row.add_statements or []),
            "remove_statements": list(row.remove_statements or []),
            "statement_edits":   dict(row.statement_edits or {}),
            "approved":          row.approved,
        }
        await apply_event(
            db,
            project_id=run.project_id,
            entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
            entity_id=entity_id_str,
            op=op_kind,
            new_state=new_state,
            actor_id=auth.user.id,
            message=f"wikidata override edit ({local_id})",
        )
    except Exception as exc:  # noqa: BLE001 — versioning must never 500
        logger.warning(
            "apply_event failed for wikidata_override %s: %s", entity_id_str, exc,
        )

    await db.commit()
    return ItemOverrideResponse(
        run_id=run_id, local_id=local_id,
        labels=row.labels or {}, descriptions=row.descriptions or {},
        aliases=row.aliases or {}, add_statements=row.add_statements or [],
        remove_statements=row.remove_statements or [],
        statement_edits=row.statement_edits or {},
        approved=row.approved,
    )


@router.delete(
    "/{run_id}/wikidata-studio/items/{local_id:path}/overrides",
    status_code=204,
)
async def clear_item_override(
    run_id: uuid.UUID, local_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Drop every curator edit for this item — next rebuild returns to
    what the builder + matchers produced."""
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    # Fetch the row first so we can record the tombstone against its
    # stable UUID — the bulk DELETE below loses the id otherwise.
    row = (
        await db.execute(
            select(WikidataItemOverride).where(
                WikidataItemOverride.run_id == run_id,
                WikidataItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()

    if row is not None:
        entity_id_str = str(row.id)
        try:
            has_history = (
                await db.execute(
                    select(ProjectEvent.id)
                    .where(
                        ProjectEvent.entity_type == ENTITY_TYPE_WIKIDATA_OVERRIDE,
                        ProjectEvent.entity_id == entity_id_str,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none() is not None
            op_kind = OP_PATCH if has_history else OP_CREATE
            await apply_event(
                db,
                project_id=run.project_id,
                entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
                entity_id=entity_id_str,
                op=op_kind,
                new_state={"_deleted": True},
                actor_id=auth.user.id,
                message=f"wikidata override cleared ({local_id})",
            )
        except Exception as exc:  # noqa: BLE001 — versioning must never 500
            logger.warning(
                "apply_event failed for wikidata_override tombstone %s: %s",
                entity_id_str, exc,
            )

    await db.execute(
        WikidataItemOverride.__table__.delete().where(
            (WikidataItemOverride.run_id == run_id)
            & (WikidataItemOverride.local_id == local_id)
        )
    )
    await db.commit()


# ── helpers ─────────────────────────────────────────────────────────────


def _slice_items(
    all_items: list[dict[str, Any]],
    *,
    entity_type: str | None,
    q: str | None,
    sort: str,
    sort_dir: str,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int, list[PropertyInfo], dict[str, str], int]:
    """Filter, sort, and paginate a flat list of serialised StudioItem dicts.

    Returns ``(page_items, total_matching, properties, property_labels, approved_item_count)``
    where:
    - ``page_items``          — the slice for the requested page
    - ``total_matching``      — count AFTER filter, BEFORE pagination
    - ``properties``          — distinct P-ids in the *full* build (unfiltered)
    - ``property_labels``     — P/Q id → label map for the *full* build
    - ``approved_item_count`` — items with approved==True in the *full* build

    Slicing never re-builds — it operates purely on the in-memory dicts
    returned from cache or from a fresh build. The cache invariant
    (Rule W-26) is untouched.
    """
    from converter.wikidata.property_labels import PROPERTY_LABELS, QID_LABELS  # noqa: PLC0415

    # ── precomputed aggregates (full unfiltered build) ──────────────────
    approved_item_count = sum(1 for it in all_items if it.get("approved") is True)

    prop_seen: dict[str, str] = {}   # p_id → label
    for it in all_items:
        for stmt in it.get("statements") or []:
            p = stmt.get("property") or stmt.get("property_id")
            if p and p not in prop_seen:
                plabel = (
                    stmt.get("property_label")
                    or PROPERTY_LABELS.get(p, "")
                )
                prop_seen[p] = plabel

    properties: list[PropertyInfo] = sorted(
        [PropertyInfo(id=pid, label=lbl) for pid, lbl in prop_seen.items()],
        key=lambda x: (x.label or x.id).lower(),
    )

    # Build property_labels: covers all P-ids + all Q-ids appearing as
    # statement values so the frontend label store can be seeded in one shot.
    property_labels: dict[str, str] = {}
    for it in all_items:
        for stmt in it.get("statements") or []:
            p = stmt.get("property") or stmt.get("property_id")
            if p:
                lbl = stmt.get("property_label") or PROPERTY_LABELS.get(p)
                if lbl:
                    property_labels[p] = lbl
            vid = stmt.get("value_id")
            val = stmt.get("value")
            qid = (
                vid if isinstance(vid, str) and vid.startswith("Q")
                else (val if isinstance(val, str) and val.startswith("Q") and val[1:].isdigit() else None)
            )
            if qid:
                vlbl = stmt.get("value_label") or QID_LABELS.get(qid)
                if vlbl:
                    property_labels[qid] = vlbl

    # ── filter ──────────────────────────────────────────────────────────
    filtered = all_items
    if entity_type and entity_type != "all":
        filtered = [it for it in filtered if it.get("entity_type") == entity_type]
    if q:
        q_lower = q.strip().lower()
        def _matches(it: dict[str, Any]) -> bool:
            parts: list[str] = [
                *it.get("labels", {}).values(),
                *it.get("descriptions", {}).values(),
                it.get("existing_qid") or "",
                it.get("entity_type") or "",
            ]
            for alias_list in (it.get("aliases") or {}).values():
                if isinstance(alias_list, list):
                    parts.extend(alias_list)
                elif isinstance(alias_list, str):
                    parts.append(alias_list)
            return q_lower in " ".join(parts).lower()
        filtered = [it for it in filtered if _matches(it)]

    # ── sort ─────────────────────────────────────────────────────────────
    reverse = sort_dir == "desc"

    def _label(it: dict[str, Any]) -> str:
        lbs = it.get("labels") or {}
        return (lbs.get("en") or lbs.get("he") or next(iter(lbs.values()), "")).lower()

    if sort == "statements":
        filtered.sort(key=lambda it: len(it.get("statements") or []), reverse=reverse)
    elif sort == "entity_type":
        filtered.sort(key=lambda it: it.get("entity_type") or "", reverse=reverse)
    elif sort == "wikidata":
        # items with a QID first (ascending), or last (descending)
        filtered.sort(
            key=lambda it: (0 if it.get("existing_qid") else 1),
            reverse=reverse,
        )
    else:  # "label" default
        try:
            filtered.sort(key=_label, reverse=reverse)
        except Exception:
            filtered.sort(key=_label, reverse=reverse)

    total = len(filtered)

    # ── paginate ─────────────────────────────────────────────────────────
    start = (page - 1) * page_size
    page_items = filtered[start: start + page_size]

    return page_items, total, properties, property_labels, approved_item_count


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
    entity_rows = (
        await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
        )
    ).scalars().all()
    override_rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()
    matches = [m for m in all_matches if m.approved] if approved_only else list(all_matches)

    overrides = {
        r.local_id: {
            "labels":            r.labels,
            "descriptions":      r.descriptions,
            "aliases":           r.aliases,
            "add_statements":    r.add_statements,
            "remove_statements": r.remove_statements,
            "statement_edits":   r.statement_edits,
        }
        for r in override_rows
    }
    entities_by_cn = _group_entity_rows(entity_rows, approved_only)
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
        entities_by_cn=entities_by_cn,
        overrides=overrides,
        return_native=True,
    )
    return result.get("native_items") or []


async def _fetch_wikidata_verify_items(
    db: AsyncSession,
    run_id: uuid.UUID,
    auth: AuthContext,
    *,
    item_ids: list[str] | None,
    approved_only: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build Studio items and return the scoped serialised candidates."""
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    cached = await execute_studio_build(
        db,
        run_id=run_id,
        approved_only=approved_only,
        force_rebuild=False,
        run_user_id=auth.user.id,
    )
    sliced, total, _props, _plabels, _approved_item_count = _slice_items(
        cached.result_items or [],
        entity_type=None,
        q=None,
        sort="label",
        sort_dir="asc",
        page=1,
        page_size=500,
    )
    # Build a lookup so each item gets only its own manuscript's control number.
    cn_by_local_id: dict[str, str] = {}
    for r in records:
        cn_by_local_id[str(r.control_number)] = str(r.control_number)

    wanted = set(item_ids or [])
    items: list[dict[str, Any]] = []
    for raw in sliced:
        item = dict(raw)
        local_id = str(item.get("local_id") or "")
        if wanted and local_id not in wanted:
            continue
        item["_local_id"] = local_id
        # Use the item's own record(s); "records" is a list of CNs on every studio item.
        own_records: list[str] = [str(cn) for cn in (item.get("records") or []) if cn]
        if not own_records:
            own_cn = str(item.get("control_number") or item.get("_control_number") or "")
            own_records = [own_cn] if own_cn else []
        item["record_ids"] = own_records or [str(r.control_number) for r in records[:1]]
        items.append(item)
    return items, [dict(r.marc or {"_control_number": r.control_number}) for r in records]


_WIKIDATA_VERIFY_CHANNEL = "wikidata-verify-sessions"


async def _wikidata_verify_event_stream(
    *,
    run_id: str,
    session_id: str,
    action: agent_actions.AgentAction,
    items: list[dict[str, Any]],
    uncached_items: list[dict[str, Any]],
    pre_cached: list[tuple[dict[str, Any], dict[str, Any]]],
    marc_records: list[dict[str, Any]],
    api_key: str,
    override_cache: bool,
    tier_model: str | None,
):
    state_dir = resolve_verify_state_dir(_WIKIDATA_VERIFY_CHANNEL, run_id)
    session_dir = resolve_verify_session_dir(_WIKIDATA_VERIFY_CHANNEL, run_id, session_id)
    pipeline_output = session_dir / "pipeline-output"
    session_dir.mkdir(parents=True, exist_ok=True)
    eval_agent_error: str | None = None

    if uncached_items:
        try:
            locate_eval_agent()
        except (FileNotFoundError, OSError, PermissionError) as exc:
            eval_agent_error = str(exc)
    start_ev = AgentEvent(
        type="session.start",
        payload={
            "session_id": session_id,
            "run_id": run_id,
            "action_id": action.id,
            "scope_size": len(items),
            "scope_item_ids": sorted(str(i.get("_local_id") or "") for i in items),
            "goal": agent_actions.render_goal(action, n_candidates=len(items)),
            "cache_hits": len(pre_cached),
        },
    )
    persist_session_event(session_dir, start_ev)
    yield start_ev

    for item, cached_payload in pre_cached:
        ev = AgentEvent(
            type="agent.verdict",
            payload=_cached_wikidata_verdict_event(item, cached_payload),
        )
        persist_session_event(session_dir, ev)
        yield ev

    try:
        if eval_agent_error:
            warn_ev = AgentEvent(
                type="runner.warning",
                payload={
                    "message": (
                        f"{len(uncached_items)} Wikidata Studio items cannot be verified here "
                        "because the eval-agent is not available on this server."
                    ),
                    "uncached_count": len(uncached_items),
                    "eval_agent_error": eval_agent_error,
                },
            )
            persist_session_event(session_dir, warn_ev)
            yield warn_ev
        else:
            assert pipeline_output is not None and state_dir is not None
            _write_wikidata_verify_fixture(
                dest_dir=pipeline_output,
                marc_records=marc_records,
                items=uncached_items,
            )
            async for ev in spawn_eval_agent_run(
                pipeline_output=pipeline_output,
                evaluators=action.evaluators,
                api_key=api_key,
                state_dir=state_dir,
                tier_model=tier_model,
                override_cache=override_cache,
                rpm=action.rate_limit_rpm,
            ):
                persist_session_event(session_dir, ev)
                yield ev
    finally:
        on_disk_verdicts = read_run_verdicts(state_dir) if (uncached_items and not eval_agent_error) else []
        items_by_id = {
            str(i.get("_local_id") or i.get("local_id") or ""): i
            for i in uncached_items
        }
        for v in on_disk_verdicts:
            cand = v.get("candidate") if isinstance(v.get("candidate"), dict) else None
            if isinstance(cand, dict):
                local_id = str(
                    cand.get("_local_id") or cand.get("_item_id")
                    or cand.get("local_id") or "",
                )
                item = items_by_id.get(local_id)
                if item is not None and not cand.get("label"):
                    cand["label"] = _item_label(item)
            ev = AgentEvent(type="agent.verdict", payload=v)
            persist_session_event(session_dir, ev)
            yield ev

        if on_disk_verdicts:
            try:
                await _write_wikidata_verdicts_to_cache(
                    items_by_id={
                        str(i.get("_local_id") or i.get("local_id") or ""): i
                        for i in uncached_items
                    },
                    verdicts=on_disk_verdicts,
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to write Wikidata item verdicts to inference cache")

        end_ev = AgentEvent(
            type="session.end",
            payload={
                "session_id": session_id,
                "scope_size": len(items),
                "cache_hits": len(pre_cached),
                "fresh_verdicts": len(on_disk_verdicts),
                "uncached_skipped": len(uncached_items) if eval_agent_error else 0,
                "outcome": "partial" if eval_agent_error else "complete",
            },
        )
        persist_session_event(session_dir, end_ev)
        yield end_ev


def _write_wikidata_verify_fixture(
    *,
    dest_dir: Path,
    marc_records: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "marc_extracted.json").write_text(
        json.dumps(marc_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (dest_dir / "wikidata_items.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _wikidata_verdict_query_summary(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
) -> dict[str, Any]:
    return {
        "local_id": str(item.get("_local_id") or item.get("local_id") or ""),
        "entity_type": str(item.get("entity_type") or ""),
        "labels": item.get("labels") or {},
        "descriptions": item.get("descriptions") or {},
        "aliases": item.get("aliases") or {},
        "statements": item.get("statements") or [],
        "existing_qid": item.get("existing_qid"),
        "validation_issues": item.get("validation_issues") or [],
        "judge_model": judge_model,
        "evaluator": "wikidata_item",
    }


def _cached_wikidata_verdict_event(
    item: dict[str, Any],
    cached_payload: dict[str, Any],
) -> dict[str, Any]:
    local_id = str(item.get("_local_id") or item.get("local_id") or "")
    record_ids = item.get("record_ids") if isinstance(item.get("record_ids"), list) else []
    return {
        "candidate": {
            **item,
            "_local_id": local_id,
            "_item_id": local_id,
            "label": _item_label(item),
        },
        "verdict": cached_payload.get("verdict") or {},
        "judge_id": cached_payload.get("judge_id"),
        "judged_at": cached_payload.get("judged_at"),
        "cache_key": cached_payload.get("cache_key"),
        "evaluator_id": cached_payload.get("evaluator") or "wikidata_item",
        "confidence": cached_payload.get("confidence"),
        "record_id": cached_payload.get("record_id") or (str(record_ids[0]) if record_ids else local_id),
        "sub_type": cached_payload.get("sub_type") or item.get("entity_type") or "item",
        "from_inference_cache": True,
    }


async def _write_wikidata_verdicts_to_cache(
    *,
    items_by_id: dict[str, dict[str, Any]],
    verdicts: list[dict[str, Any]],
) -> None:
    from app.db import session_scope  # noqa: PLC0415

    async with session_scope() as db:
        for v in verdicts:
            cand = v.get("candidate") if isinstance(v, dict) else None
            local_id = ""
            if isinstance(cand, dict):
                local_id = str(
                    cand.get("_local_id") or cand.get("_item_id")
                    or cand.get("local_id") or "",
                )
            item = items_by_id.get(local_id)
            if item is None:
                continue
            judge_model = str(v.get("judge_id") or v.get("model") or "gemini-3.5-flash")
            cached_result = {
                "verdict": v.get("verdict") or {},
                "judge_id": v.get("judge_id") or v.get("model"),
                "judged_at": v.get("judged_at"),
                "cache_key": v.get("cache_key"),
                "evaluator": v.get("evaluator_id") or v.get("evaluator") or "wikidata_item",
                "confidence": v.get("confidence"),
                "sub_type": v.get("sub_type"),
                "record_id": v.get("record_id"),
            }
            await write_to_inference_cache(
                db,
                kind="ai_verdict",
                query_summary=_wikidata_verdict_query_summary(item, judge_model),
                result=cached_result,
            )


def _item_label(item: dict[str, Any]) -> str:
    labels = item.get("labels")
    if isinstance(labels, dict):
        for key in ("en", "he"):
            value = labels.get(key)
            if value:
                return str(value)
        for value in labels.values():
            if value:
                return str(value)
    return str(item.get("_local_id") or item.get("local_id") or "")


def _list_wikidata_sessions(run_id: str) -> list[dict[str, Any]]:
    return list_verify_sessions(_WIKIDATA_VERIFY_CHANNEL, run_id)


def _read_wikidata_session(run_id: str, session_id: str) -> dict[str, Any] | None:
    return read_verify_session(_WIKIDATA_VERIFY_CHANNEL, run_id, session_id)


def _wikidata_session_meta(session_dir: Path) -> dict[str, Any]:
    trace = session_dir / "trace.jsonl"
    if not trace.exists():
        return {"started_at": None, "ended_at": None,
                "action_id": None, "scope_size": 0, "outcome": None}
    start: dict[str, Any] = {}
    end: dict[str, Any] = {}
    try:
        for line in trace.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("type") == "session.start":
                start = ev
            elif ev.get("type") == "session.end":
                end = ev
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "started_at": start.get("ts"),
        "ended_at": end.get("ts"),
        "action_id": start.get("action_id"),
        "scope_size": start.get("scope_size", 0),
        "outcome": end.get("outcome"),
    }


async def _resolve_gemini_key(
    db: AsyncSession,
    auth: AuthContext,
) -> str | None:
    import os  # noqa: PLC0415

    try:
        from app.pipeline.ai_verifier import unwrap_user_gemini_key  # noqa: PLC0415

        key = await unwrap_user_gemini_key(
            db, user_id=auth.user.id, kek=auth.kek,
        )
        if key:
            return key
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not unwrap stored Gemini key: %s", exc)
    return os.environ.get("GEMINI_API_KEY")


# Hebrew common-noun strings that the NER sometimes extracts as person names.
# These are definitely not names and must be filtered out before building Wikidata items.
_NON_PERSON_STRINGS: frozenset[str] = frozenset({
    "הסוחר",    # the merchant
    "נפש",      # soul
    "גוי",      # gentile
    "כותי",     # Samaritan
    "ישמעאל",   # Ishmael (used generically for Muslim/Arab, not as a person name)
    "עמוד",     # page/column
    "דף",       # folio
    "פרשה",     # Torah portion
    "פסוק",     # verse
})


def _group_entity_rows(
    rows: list[Any], approved_only: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Group ExtractionApproval rows into the per-control-number dict the
    desktop WikidataItemBuilder expects on ``record["entities"]``.

    Curator ``override_type`` / ``override_role`` / ``override_text``
    take precedence over the model's prediction (Rule W-24).
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if approved_only and not r.approved:
            continue
        ent_dict = {
            "text":             str(r.override_text or r.text or ""),
            "type":             (r.override_type or r.type or "").upper(),
            "role":             (r.override_role or r.role or "").upper(),
            "source":           r.source,
            "start":            int(r.start or 0),
            "end":              int(r.end or 0),
            "confidence":       r.confidence,
            "model_confidence": r.model_confidence,
            "approved":         bool(r.approved),
        }
        if ent_dict["type"] == "PERSON":
            text_lo = ent_dict["text"].strip().lower()
            if text_lo in _NON_PERSON_STRINGS:
                continue
            if (
                len(ent_dict["text"].split()) == 1
                and (r.confidence or 1.0) < 0.40
            ):
                continue
        grouped.setdefault(r.control_number, []).append(ent_dict)

    for cn, ents in grouped.items():
        seen_keys: set[tuple[str, str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for ent in ents:
            key = (
                ent["text"].strip().lower(),
                ent["type"],
                ent["role"] or "",
            )
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(ent)
        grouped[cn] = deduped
    return grouped


async def _upsert_studio_cache(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    approved_only: bool,
    fingerprint: str,
    items: list[dict[str, Any]],
    quickstatements: str,
    summary: dict[str, Any],
    approved_match_count: int,
    pending_match_count: int,
    used_match_count: int,
    record_count: int,
    existing: WikidataStudioCache | None,
) -> None:
    """Write (insert or update) the build cache row.  Errors are swallowed
    so a cache write failure never degrades the user-facing response."""
    try:
        from datetime import datetime, timezone  # noqa: PLC0415

        if existing is None:
            row = WikidataStudioCache(
                run_id=run_id,
                approved_only=approved_only,
                input_fingerprint=fingerprint,
                result_items=items,
                quickstatements=quickstatements,
                summary=summary,
                approved_match_count=approved_match_count,
                pending_match_count=pending_match_count,
                used_match_count=used_match_count,
                record_count=record_count,
            )
            db.add(row)
        else:
            existing.input_fingerprint = fingerprint
            existing.result_items = items
            existing.quickstatements = quickstatements
            existing.summary = summary
            existing.approved_match_count = approved_match_count
            existing.pending_match_count = pending_match_count
            existing.used_match_count = used_match_count
            existing.record_count = record_count
            existing.built_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("wikidata-studio cache write failed for run %s: %s", run_id, exc)
        await db.rollback()


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
