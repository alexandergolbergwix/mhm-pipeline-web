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

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.export.formatters import csv_stream, json_stream
from app.models.event import (
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    OP_CREATE,
    OP_PATCH,
    ProjectEvent,
)
from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, RunRecord
from app.models.run_job import (
    JOB_KIND_WIKIDATA_STUDIO_BUILD,
    JOB_KIND_WIKIDATA_UPLOAD,
    JOB_KIND_WIKIDATA_VERIFY,
)
from app.models.wikibase_cloud_write import CHANNEL_WIKIDATA_UPLOAD
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.settings import get_settings
from app.pipeline import agent_actions, wikidata_actions, wikidata_studio, wikidata_upload
from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import (
    filter_public_wikidata_items,
    studio_cache_has_non_public_items,
    build_canonical_studio_result,
    canonical_studio_context,
    canonical_wikidata_fingerprint,
    native_items_from_hmo,
)
from app.pipeline.agent_runner import (
    AgentEvent,
    list_verify_sessions,
    locate_eval_agent,
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
from app.pipeline.verify_session_store import load_verify_session
from app.pipeline.wikidata_export_quality_gate import assert_wikidata_export_quality
from app.pipeline.wikidata_item_views import (
    StudioBuildMissingError,
    fetch_merged_wikidata_item,
    fetch_merged_wikidata_items,
    fetch_validation_error_items,
    trim_studio_list_item,
)
from app.pipeline.wikidata_verdict_cache import (
    attach_local_reference_targets,
    attach_wikidata_marc_context,
    marc_context_for_wikidata_item,
    record_ids_for_wikidata_item,
    sanitise_stale_wikidata_verdict,
    wikidata_verdict_input_fingerprint,
    wikidata_verdict_query_summary,
)
from app.pipeline.wikidata_verify_fixture import (
    compact_wikidata_verdict_candidate,
    write_wikidata_verify_fixture,
)
from app.routers.runs import _lookup_run_with_access  # noqa: PLF401 — module-internal
from app.services.wikibase_audit import WikibaseAuditContext
from app.versioning import apply_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["wikidata-studio"])


async def _canonical_entities_for_run(
    db: AsyncSession, run_id: uuid.UUID,
) -> list[Any]:
    rows = (
        await db.execute(
            select(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == run_id),
        )
    ).scalars().all()
    return [normalize_live_entity(row.snapshot) for row in rows]


async def _canonical_cache_fingerprint(
    db: AsyncSession, run_id: uuid.UUID,
) -> str:
    canonical = await _canonical_entities_for_run(db, run_id)
    return canonical_wikidata_fingerprint(canonical) if canonical else ""


async def studio_items_for_project(
    run_ids: list[str], db: AsyncSession, *, approved_only: bool = True,
    source: str = "legacy",
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
                WikidataStudioCache.source == source,
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
    source: str                   # legacy or canonical HMO source
    record_count: int
    # Server-side slicing metadata
    total: int                    # total items matching current slice params
    page: int
    page_size: int
    # Precomputed aggregates to replace client-side scans
    approved_item_count: int      # items with approved==True in the full build
    properties: list[PropertyInfo]        # distinct P-ids in the full build
    property_labels: dict[str, str]       # P/Q id → label map for label-store seeding
    cache_stale: bool = False     # true when cached items predate current inputs


class VerifyStartRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=64)
    item_ids: list[str] | None = None
    approved_only: bool = False  # AI verification audits all items, not just approved-match ones
    # Must match the Studio projection toggle (canonical vs legacy). Hardcoding
    # legacy while the UI defaults to canonical yields "no items in scope"
    # because local_ids never intersect (Rule W-115).
    source: Literal["legacy", "canonical"] = "canonical"
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
        source=payload.source,
    )
    items = await _prepare_wikidata_verify_scope(action, items)
    if not items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "no Wikidata Studio items with an existing QID in scope"
                if action.id == "autofix_from_wikidata"
                else "no Wikidata Studio items in scope"
            ),
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
    evaluator_id = action.evaluators[0] if action.evaluators else "wikidata_item"
    attach_wikidata_marc_context(items, marc_records)
    pre_cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
    uncached: list[dict[str, Any]] = []
    if not payload.override_cache:
        for item in items:
            hit = await read_from_inference_cache(
                db,
                kind="ai_verdict",
                query_summary=wikidata_verdict_query_summary(
                    item, judge_model, evaluator=evaluator_id,
                ),
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
    source: str = "legacy",
) -> WikidataStudioCache | None:
    return (
        await db.execute(
            select(WikidataStudioCache).where(
                WikidataStudioCache.run_id == run_id,
                WikidataStudioCache.approved_only == approved_only,
                WikidataStudioCache.source == source,
            )
        )
    ).scalar_one_or_none()


async def execute_studio_build(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    approved_only: bool,
    source: str,
    force_rebuild: bool,
    run_user_id: uuid.UUID | None,
    reconcile: bool = True,
) -> WikidataStudioCache:
    """Run the full item builder and upsert the Postgres cache.

    Background jobs call this directly so the work never runs inside a
    Heroku HTTP request (30 s router timeout).

    ``reconcile=False`` skips live WDQS lookups (verify scope materialisation).
    """
    records, all_matches, entity_rows, override_rows = await _load_studio_build_rows(
        db, run_id,
    )
    hmo_instance_qids = await wikidata_studio.hmo_instance_qids_for_run(
        db, run_id, [r.control_number for r in records],
    )
    fingerprint = wikidata_studio.compute_build_fingerprint(
        records, all_matches, entity_rows, override_rows, approved_only,
        hmo_instance_qids,
    )
    cached = await _get_studio_cache_row(db, run_id, approved_only, source)

    if source == "canonical":
        canonical = await _canonical_entities_for_run(db, run_id)
        if not canonical:
            raise ValueError(f"no durable HMO canonical entities for run {run_id}")
        enrichment_fp = wikidata_studio.compute_build_fingerprint(
            records, all_matches, entity_rows, override_rows, approved_only,
            hmo_instance_qids,
        )
        canonical_fp = canonical_wikidata_fingerprint(
            canonical, enrichment_fingerprint=enrichment_fp,
        )
        if (
            not force_rebuild
            and cached is not None
            and cached.input_fingerprint == canonical_fp
            and not studio_cache_has_non_public_items(
                cached.result_items, source="canonical",
            )
        ):
            return cached
    elif not force_rebuild and cached is not None and cached.input_fingerprint == fingerprint:
        return cached

    if source == "canonical":
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
        approved_matches = [
            {
                "id": str(m.id),
                "control_number": m.control_number,
                "entity_text": m.entity_text,
                "entity_kind": m.entity_kind,
                "role": m.role,
                "field": (m.payload or {}).get("field") or "",
                "matched_name": m.matched_name,
                "mazal_id": m.mazal_id,
                "viaf_id": m.viaf_id,
                "wikidata_qid": m.wikidata_qid,
                "confidence": m.confidence,
                "source": m.source,
                "approved": bool(m.approved),
                "payload": m.payload or {},
            }
            for m in (m for m in all_matches if m.approved)
        ]
        context = canonical_studio_context(
            marc_records=[dict(r.marc) for r in records],
            approved_matches=approved_matches,
        )
        entities_by_cn = _group_entity_rows(entity_rows, approved_only=True)
        from converter.wikidata import hebrew_translit  # noqa: PLC0415
        from starlette.concurrency import run_in_threadpool  # noqa: PLC0415

        prewarmed = await _prewarm_transliterations(
            db, marc_records=[dict(r.marc) for r in records], user_id=run_user_id,
        )
        hebrew_translit.set_prewarmed_labels(prewarmed)
        hebrew_translit.set_sync_network_disabled(True)
        try:
            legacy_result = await wikidata_studio.build_items_for_run(
                marc_records=[dict(r.marc) for r in records],
                approved_matches=approved_matches,
                entities_by_cn=entities_by_cn,
                overrides=overrides,
                return_native=True,
                hmo_instance_qids=hmo_instance_qids,
            )
        finally:
            hebrew_translit.set_sync_network_disabled(False)
            hebrew_translit.clear_prewarmed_labels()

        result = await run_in_threadpool(
            build_canonical_studio_result,
            canonical,
            overrides=overrides,
            context=context,
            reconcile=reconcile,
            legacy_native_items=legacy_result.get("native_items") or [],
        )
        items = result["items"]
        summary = result["summary"]
        await _upsert_studio_cache(db, run_id=run_id, approved_only=approved_only, source=source, fingerprint=canonical_fp, items=items, quickstatements=result["quickstatements"], summary=summary, approved_match_count=0, pending_match_count=0, used_match_count=0, record_count=len(items), existing=cached)
        row = await _get_studio_cache_row(db, run_id, approved_only, source)
        if row is None:
            raise RuntimeError(f"canonical Studio cache missing after build for run {run_id}")
        return row

    approved_count = sum(1 for m in all_matches if m.approved)
    pending_count = len(all_matches) - approved_count
    matches = [m for m in all_matches if m.approved] if approved_only else list(all_matches)

    marc_records = [dict(r.marc) for r in records]
    approved_matches = [
        {
            "id": str(m.id),
            "control_number": m.control_number,
            "entity_text": m.entity_text,
            "entity_kind": m.entity_kind,
            "role": m.role,
            "field": (m.payload or {}).get("field") or "",
            "matched_name": m.matched_name,
            "mazal_id": m.mazal_id,
            "viaf_id": m.viaf_id,
            "wikidata_qid": m.wikidata_qid,
            "confidence": m.confidence,
            "source": m.source,
            "approved": bool(m.approved),
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
            hmo_instance_qids=hmo_instance_qids,
        )
    finally:
        hebrew_translit.set_sync_network_disabled(False)
        hebrew_translit.clear_prewarmed_labels()

    if result.get("native_items"):
        assert_wikidata_export_quality(result["native_items"])
        for it_dict, it_native in zip(
            result["items"], result["native_items"], strict=True,
        ):
            lid = wikidata_studio.local_id_for_item(it_native)
            it_dict["local_id"] = lid
            it_dict["approved"] = overrides_approved.get(lid)

    summary_dict = result["summary"]
    await _upsert_studio_cache(
        db, run_id=run_id, approved_only=approved_only, source=source,
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
    row = await _get_studio_cache_row(db, run_id, approved_only, source)
    if row is None:
        raise RuntimeError(f"studio cache missing after build for run {run_id}")
    return row


async def _enqueue_studio_build_job(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    approved_only: bool,
    source: str,
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
                "source": source,
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


def _studio_response_from_cache(
    cached: WikidataStudioCache,
    merged_items: list[dict[str, Any]],
    *,
    approved_only: bool,
    entity_type: str | None,
    q: str | None,
    upload_outcome: str | None,
    sort: str,
    sort_dir: str,
    page: int,
    page_size: int,
    cache_stale: bool = False,
    list_view: bool = False,
) -> StudioBuildResponse:
    sliced, total, props, plabels, approved_item_count = _slice_items(
        merged_items,
        entity_type=entity_type,
        q=q,
        upload_outcome=upload_outcome,
        sort=sort,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )
    if list_view:
        sliced = [trim_studio_list_item(item) for item in sliced]
    return StudioBuildResponse(
        items=sliced,
        quickstatements="" if list_view else cached.quickstatements,
        summary=StudioSummary(**cached.summary),
        approved_match_count=cached.approved_match_count,
        pending_match_count=cached.pending_match_count,
        used_match_count=cached.used_match_count,
        approved_only=approved_only,
        source=cached.source or "legacy",
        record_count=cached.record_count,
        total=total,
        page=page,
        page_size=page_size,
        approved_item_count=approved_item_count,
        properties=props,
        property_labels=plabels,
        cache_stale=cache_stale,
    )


@router.get("/{run_id}/wikidata-studio", response_model=StudioBuildResponse)
async def build_studio(
    run_id: uuid.UUID,
    source: str = Query(default="canonical", pattern="^(legacy|canonical)$"),
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
    upload_outcome: str | None = Query(
        default=None,
        description="Filter by last upload outcome (create/adopt/update/blocked/…).",
    ),
    list_view: bool = Query(
        default=False,
        description="When true, omit bulky per-item fields (statements, evidence, "
                    "quickstatements corpus) for paginated review tables.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StudioBuildResponse:
    run = await _lookup_run_with_access(db, run_id, auth)
    if source == "legacy" and get_settings().canonical_first_for_run(run_id):
        source = "canonical"

    records, all_matches, entity_rows, override_rows = await _load_studio_build_rows(
        db, run_id,
    )
    hmo_instance_qids = await wikidata_studio.hmo_instance_qids_for_run(
        db, run_id, [r.control_number for r in records],
    )

    fingerprint = wikidata_studio.compute_build_fingerprint(
        records, all_matches, entity_rows, override_rows, approved_only,
        hmo_instance_qids,
    )

    cached = await _get_studio_cache_row(db, run_id, approved_only, source)

    cache_fingerprint = (
        await _canonical_cache_fingerprint(db, run_id)
        if source == "canonical"
        else fingerprint
    )

    if not force_rebuild and cached is not None:
        if cached.input_fingerprint == cache_fingerprint:
            logger.debug("wikidata-studio cache hit for run %s (fp=%s)", run_id, fingerprint[:8])
            merged = await fetch_merged_wikidata_items(
                db, run_id, approved_only=approved_only, source=source,
            )
            cache_shape_stale = studio_cache_has_non_public_items(
                cached.result_items, source=source,
            )
            return _studio_response_from_cache(
                cached,
                merged,
                approved_only=approved_only,
                entity_type=entity_type,
                q=q,
                upload_outcome=upload_outcome,
                sort=sort,
                sort_dir=sort_dir,
                page=page,
                page_size=page_size,
                cache_stale=cache_shape_stale,
                list_view=list_view,
            )
        # Stale cache: serve the last good build immediately. Do not auto-start
        # a background job — passive page loads should not surface a job-tray
        # banner while the curator is already reviewing cached items.
        logger.debug(
            "wikidata-studio stale cache for run %s (cached=%s current=%s)",
            run_id,
            (cached.input_fingerprint or "")[:8],
            cache_fingerprint[:8],
        )
        return _studio_response_from_cache(
            cached,
            await fetch_merged_wikidata_items(
                db, run_id, approved_only=approved_only, source=source,
            ),
            approved_only=approved_only,
            entity_type=entity_type,
            q=q,
            upload_outcome=upload_outcome,
            sort=sort,
            sort_dir=sort_dir,
            page=page,
            page_size=page_size,
            cache_stale=True,
            list_view=list_view,
        )

    logger.debug("wikidata-studio cache miss for run %s (fp=%s)", run_id, fingerprint[:8])
    job_id = await _enqueue_studio_build_job(
        db,
        project_id=run.project_id,
        run_id=run_id,
        approved_only=approved_only,
        source=source,
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
    source: str = Query(default="legacy", pattern="^(legacy|canonical)$"),
    approved_only: bool = Query(default=True),
    item_approved_only: bool = Query(
        default=False,
        description="When true, only include items where the curator has "
                    "explicitly ticked 'Approved' in the Studio item overlay. "
                    "Independent of approved_only (which filters authority matches).",
    ),
    gated: bool = Query(
        default=True,
        description="When true (default), run reconcile+validator and exclude blocked items.",
    ),
    ack: str | None = Query(default=None, description="Set to 'raw' with gated=false for ungated export."),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Plain-text QuickStatements TSV — paste into
    https://quickstatements.toolforge.org."""
    if not gated:
        if ack != "raw":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ungated QuickStatements export requires gated=false and ack=raw.",
            )
        logger.warning("wikidata QS ungated export for run %s by user %s", run_id, auth.user.id)

    native = await _build_native_items(
        db, run_id, auth, approved_only=approved_only, source=source,
    )
    if item_approved_only:
        override_rows = (
            await db.execute(
                select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
            )
        ).scalars().all()
        approved_ids = {r.local_id for r in override_rows if r.approved}
        native = [it for it in native if wikidata_studio.local_id_for_item(it) in approved_ids]

    if gated:
        ledger = await wikidata_upload.load_ledger_for_prepare(db)
        eligible, blocked = wikidata_upload.prepare_items_for_export(
            native, ledger=ledger,
        )
        for p in blocked:
            if p.method == "error" and "Reconciliation lookup" in p.block_message:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=p.block_message,
                )
        native = [p.item for p in eligible]
        qs_text = await wikidata_studio.quickstatements_for_items(native)
        header_lines = []
        if blocked:
            header_lines.append(f"# excluded {len(blocked)} items:")
            for p in blocked[:50]:
                header_lines.append(f"# {p.local_id} — {p.block_message}")
            if len(blocked) > 50:
                header_lines.append(f"# … and {len(blocked) - 50} more")
            qs_text = "\n".join(header_lines) + "\n" + qs_text
    elif item_approved_only or approved_only:
        qs_text = await wikidata_studio.quickstatements_for_items(native)
    else:
        cached = await _get_studio_cache_row(db, run_id, approved_only, source)
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
    source: str = Query(default="legacy", pattern="^(legacy|canonical)$"),
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
    if source == "canonical":
        canonical_rows = (await db.execute(select(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == run_id))).scalars().all()
        canonical = [normalize_live_entity(row.snapshot) for row in canonical_rows]
        if not canonical:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no canonical HMO entities for run")
        if get_settings().canonical_first_for_run(run_id) and not canonical_rows:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="canonical-first rollout requires durable HMO rows; run the backfill gate")
        native = native_items_from_hmo(canonical)
        outcomes = await wikidata_upload.reconcile_items(native)
        return ReconcileResponse(
            reconciled=len(outcomes),
            matched=sum(1 for o in outcomes if o.existing_qid),
            outcomes=[ReconcileOutcomeDto(**o.__dict__) for o in outcomes],
        )

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
    upload_target: str = "dry_run"
    moratorium_lifted: bool
    test_mode: bool
    outcomes: list[UploadOutcomeDto]


@router.post("/{run_id}/wikidata-studio/upload")
async def upload_to_wikidata(
    run_id: uuid.UUID,
    response: Response,
    source: str = Query(default="canonical", pattern="^(legacy|canonical)$"),
    upload_target: str = Query(
        default="dry_run",
        pattern="^(dry_run|test|live)$",
        description=(
            "Curator upload target: dry_run (default, no writes), "
            "test (test.wikidata.org), or live (wikidata.org)."
        ),
    ),
    dry_run: bool | None = Query(
        default=None,
        description="Deprecated — prefer upload_target. Kept for compatibility.",
    ),
    approved_only: bool = Query(default=True),
    item_approved_only: bool = Query(
        default=False,
        description="When true, only upload items where the curator has "
                    "explicitly ticked 'Approved' in the Studio item overlay.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enqueue Wikidata upload / dry-run as ``wikidata_upload`` (Rule W-107).

    Prefer ``POST /runs/{id}/jobs`` with the same kind; this route remains
    as a convenience alias so older clients do not run the upload inline
    (Heroku H12 landmine). Live writes still require the curator's
    Settings Wikidata token (validated in ``prepare_job_params``).
    """
    from app.pipeline.run_job_params import prepare_job_params  # noqa: PLC0415
    from app.pipeline.run_job_service import (  # noqa: PLC0415
        ActiveJobError,
        create_job,
        serialise_job,
    )

    mode = wikidata_upload.resolve_upload_mode(upload_target, dry_run=dry_run)
    run = await _lookup_run_with_access(db, run_id, auth, write=not mode.dry_run)
    params = await prepare_job_params(
        db, auth, run_id=run_id, kind=JOB_KIND_WIKIDATA_UPLOAD,
        params={
            "upload_target": mode.target,
            "dry_run": mode.dry_run,
            "approved_only": approved_only,
            "item_approved_only": item_approved_only,
            "source": source,
        },
    )
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_UPLOAD,
            params=params,
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "a Wikidata upload job is already running",
                "job_id": str(exc.job_id),
            },
        ) from exc
    response.status_code = status.HTTP_201_CREATED
    return serialise_job(job)


class WikidataItemPushResponse(BaseModel):
    local_id: str
    label: str
    entity_type: str
    qid: str | None
    status: str
    message: str


@router.get("/{run_id}/wikidata-studio/items/ai-verify/cached-verdicts")
async def get_cached_wikidata_item_verdicts(
    run_id: uuid.UUID,
    tier_model: str | None = Query(default=None),
    approved_only: bool = Query(default=True),
    source: str = Query(default="canonical", pattern="^(legacy|canonical)$"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    from app.pipeline.ai_verifier import GEMINI_MODEL  # noqa: PLC0415

    judge_model = tier_model or GEMINI_MODEL
    try:
        items = await fetch_merged_wikidata_items(
            db, run_id, approved_only=approved_only, source=source,
        )
    except StudioBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    attach_wikidata_marc_context(items, await _load_marc_records_for_run(db, run_id))
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        local_id = str(item.get("local_id") or "")
        fresh = sanitise_stale_wikidata_verdict(
            item,
            item.get("ai_verdict") if isinstance(item.get("ai_verdict"), dict) else None,
            judge_model=judge_model,
            marc_context=item.get("_marc_context") if isinstance(item.get("_marc_context"), dict) else None,
        )
        if fresh:
            out[local_id] = fresh
            continue
        hit = await read_from_inference_cache(
            db,
            kind="ai_verdict",
            query_summary=wikidata_verdict_query_summary(item, judge_model),
        )
        if hit is None:
            continue
        verdict = hit.get("verdict") or {} if isinstance(hit, dict) else {}
        out[local_id] = {
            "overall": verdict.get("overall") or "unknown",
            "reasoning": verdict.get("reasoning"),
            "model": hit.get("judge_id") if isinstance(hit, dict) else None,
            "evaluator": (hit.get("evaluator") if isinstance(hit, dict) else None) or "wikidata_item",
        }
    return out


@router.get("/{run_id}/wikidata-studio/items/validation-errors")
async def list_validation_errors(
    run_id: uuid.UUID,
    approved_only: bool = Query(default=True),
    source: str = Query(default="canonical", pattern="^(legacy|canonical)$"),
    on_wikidata_only: bool = Query(default=False),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    try:
        return await fetch_validation_error_items(
            db, run_id, approved_only=approved_only, source=source,
            on_wikidata_only=on_wikidata_only,
        )
    except StudioBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{run_id}/wikidata-studio/items/export")
async def export_wikidata_items(
    run_id: uuid.UUID,
    format: Literal["json", "csv"] = Query(default="json"),
    approved_only: bool = Query(default=True),
    source: str = Query(default="canonical", pattern="^(legacy|canonical)$"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _lookup_run_with_access(db, run_id, auth)
    try:
        items = await fetch_merged_wikidata_items(
            db, run_id, approved_only=approved_only, source=source,
        )
    except StudioBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    filename = f"run-{run_id}-wikidata-studio-items.{format}"
    if format == "json":
        return StreamingResponse(
            json_stream({"run_id": str(run_id), "items": items}),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _json_cell(value: Any) -> str:
        if value in (None, "", {}, []):
            return ""
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    marc_records = await _load_marc_records_for_run(db, run_id)
    fields = [
        "local_id", "entity_type", "existing_qid", "approved", "source_uri",
        "record_ids_json", "label_en", "label_he", "description_en", "description_he",
        "aliases_json", "statement_count", "statements_json", "validation_issues_json",
        "authority_evidence_json", "local_reference_targets_json",
        "has_blocking_validation", "marc_context_json", "upload_outcome", "upload_message",
        "upload_at", "ai_verdict_overall", "ai_verdict_name_ok", "ai_verdict_type_ok",
        "ai_verdict_role_ok", "ai_verdict_reasoning", "ai_verdict_model",
        "ai_verdict_judged_at", "ai_verdict_json",
    ]
    rows: list[dict[str, Any]] = []
    for it in items:
        labels = it.get("labels") if isinstance(it.get("labels"), dict) else {}
        descriptions = it.get("descriptions") if isinstance(it.get("descriptions"), dict) else {}
        av = it.get("ai_verdict") if isinstance(it.get("ai_verdict"), dict) else {}
        marc_context = marc_context_for_wikidata_item(it, marc_records)
        records = it.get("record_ids") or it.get("records") or []
        statements = it.get("statements") if isinstance(it.get("statements"), list) else []
        rows.append({
            "local_id": it.get("local_id"),
            "entity_type": it.get("entity_type"),
            "existing_qid": it.get("existing_qid"),
            "approved": it.get("approved"),
            "source_uri": it.get("source_uri"),
            "record_ids_json": _json_cell(records),
            "label_en": labels.get("en"),
            "label_he": labels.get("he"),
            "description_en": descriptions.get("en"),
            "description_he": descriptions.get("he"),
            "aliases_json": _json_cell(it.get("aliases")),
            "authority_evidence_json": _json_cell(it.get("authority_evidence")),
            "local_reference_targets_json": _json_cell(it.get("local_reference_targets")),
            "statement_count": len(statements),
            "statements_json": _json_cell(statements),
            "validation_issues_json": _json_cell(it.get("validation_issues")),
            "has_blocking_validation": it.get("has_blocking_validation"),
            "marc_context_json": _json_cell(marc_context),
            "upload_outcome": it.get("upload_outcome"),
            "upload_message": it.get("upload_message"),
            "upload_at": it.get("upload_at"),
            "ai_verdict_overall": av.get("overall"),
            "ai_verdict_name_ok": av.get("name_ok"),
            "ai_verdict_type_ok": av.get("type_ok"),
            "ai_verdict_role_ok": av.get("role_ok"),
            "ai_verdict_reasoning": av.get("reasoning"),
            "ai_verdict_model": av.get("model"),
            "ai_verdict_judged_at": it.get("ai_verdict_at") or av.get("judged_at"),
            "ai_verdict_json": _json_cell(av),
        })

    return StreamingResponse(
        csv_stream(rows, fields),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )



@router.post("/{run_id}/wikidata-studio/items/import")
async def import_wikidata_items(
    run_id: uuid.UUID,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _lookup_run_with_access(db, run_id, auth, write=True)
    try:
        known = {
            str(i.get("local_id") or "")
            for i in await fetch_merged_wikidata_items(db, run_id, source="canonical")
        }
    except StudioBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    raw = await file.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="import file must be UTF-8 JSON") from exc

    rows = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="import JSON must contain an items array")

    imported = skipped = 0
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        local_id = str(row.get("local_id") or "")
        if not local_id or local_id not in known:
            skipped += 1
            errors.append(f"unknown local_id {local_id!r}")
            continue
        await patch_item_override(
            run_id,
            local_id,
            ItemOverridePayload(
                labels=row.get("labels"),
                descriptions=row.get("descriptions"),
                aliases=row.get("aliases"),
                add_statements=row.get("add_statements"),
                remove_statements=row.get("remove_statements"),
                statement_edits=row.get("statement_edits"),
                approved=row.get("approved"),
            ),
            auth,
            db,
        )
        imported += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


@router.post("/{run_id}/wikidata-studio/items/{local_id}/push", response_model=WikidataItemPushResponse)
async def push_wikidata_item(
    run_id: uuid.UUID,
    local_id: str,
    upload_target: str = Query(
        default="test",
        pattern="^(test|live)$",
        description="Single-item push target: test (default) or live.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> WikidataItemPushResponse:
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    try:
        await fetch_merged_wikidata_items(db, run_id, source="canonical")
    except StudioBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    native = await _build_native_items(db, run_id, auth, approved_only=True)
    item = next(
        (it for it in native if wikidata_studio.local_id_for_item(it) == local_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail=f"unknown local_id {local_id!r}")

    token = await _unwrap_user_secret(db, auth, "wikidata")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Live push requires a Wikidata token in Settings.",
        )

    await db.commit()

    outcome = await wikidata_upload.push_single_item(
        db, item,
        token=token,
        audit_ctx=WikibaseAuditContext(
            actor_user_id=auth.user.id,
            channel=CHANNEL_WIKIDATA_UPLOAD,
            project_id=run.project_id,
            run_id=run_id,
        ),
        run_id=run_id,
        upload_target=upload_target,
    )
    return WikidataItemPushResponse(
        local_id=outcome.local_id,
        label=outcome.label,
        entity_type=outcome.entity_type,
        qid=outcome.qid,
        status=outcome.status,
        message=outcome.message,
    )


class WikidataReconcileItemResponse(BaseModel):
    local_id: str
    label: str
    entity_type: str
    existing_qid: str | None
    method: str
    message: str
    status: str


@router.post("/{run_id}/wikidata-studio/items/{local_id}/reconcile", response_model=WikidataReconcileItemResponse)
async def reconcile_wikidata_item(
    run_id: uuid.UUID,
    local_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> WikidataReconcileItemResponse:
    await _lookup_run_with_access(db, run_id, auth, write=True)
    native = await _build_native_items(db, run_id, auth, approved_only=True)
    item = next(
        (it for it in native if wikidata_studio.local_id_for_item(it) == local_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail=f"unknown local_id {local_id!r}")

    from converter.wikidata.reconciler import ReconciliationUnavailableError  # noqa: PLC0415

    await db.commit()
    try:
        outcome = await wikidata_upload.reconcile_single_item(db, item, record_ledger=True)
    except ReconciliationUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    status_label = "adopted" if outcome.existing_qid else "not_found"
    return WikidataReconcileItemResponse(
        local_id=outcome.local_id,
        label=outcome.label,
        entity_type=outcome.entity_type,
        existing_qid=outcome.existing_qid,
        method=outcome.method,
        message=outcome.message,
        status=status_label,
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
    # Explicit accept to UPDATE a foreign Wikidata QID (must match reconcile).
    accept_foreign_modify: bool | None = None
    accepted_foreign_qid: str | None = None


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
    accept_foreign_modify: bool | None = None
    accepted_foreign_qid: str | None = None


@router.get("/{run_id}/wikidata-studio/items/{local_id:path}")
async def get_studio_item(
    run_id: uuid.UUID,
    local_id: str,
    source: str = Query(default="canonical", pattern="^(legacy|canonical)$"),
    approved_only: bool = Query(default=True),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    if source == "legacy" and get_settings().canonical_first_for_run(run_id):
        source = "canonical"
    try:
        item = await fetch_merged_wikidata_item(
            db,
            run_id,
            local_id,
            approved_only=approved_only,
            source=source,
        )
    except StudioBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown local_id {local_id!r}",
        )
    return item


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
    if payload.accept_foreign_modify is not None:
        row.accept_foreign_modify = payload.accept_foreign_modify
        if not payload.accept_foreign_modify:
            row.accepted_foreign_qid = None
    if payload.accepted_foreign_qid is not None:
        q = str(payload.accepted_foreign_qid).strip()
        row.accepted_foreign_qid = q or None
        if row.accepted_foreign_qid and row.accept_foreign_modify is None:
            row.accept_foreign_modify = True

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
            "accept_foreign_modify": row.accept_foreign_modify,
            "accepted_foreign_qid": row.accepted_foreign_qid,
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
        accept_foreign_modify=row.accept_foreign_modify,
        accepted_foreign_qid=row.accepted_foreign_qid,
    )


async def _studio_item_from_cache(
    db: AsyncSession,
    run_id: uuid.UUID,
    local_id: str,
    *,
    approved_only: bool,
    source: str = "canonical",
) -> dict[str, Any] | None:
    cached = await _get_studio_cache_row(db, run_id, approved_only, source)
    if cached is None:
        return None
    for it in cached.result_items or []:
        if str(it.get("local_id") or "") == local_id:
            return it
    return None


@router.get(
    "/{run_id}/wikidata-studio/items/{local_id:path}/wikidata-compare",
)
async def compare_studio_item_with_wikidata(
    run_id: uuid.UUID,
    local_id: str,
    qid: str | None = Query(
        default=None,
        description="Wikidata QID to compare against. Defaults to the item's "
                    "existing_qid or a reconcile hit supplied separately.",
    ),
    approved_only: bool = Query(default=True),
    source: str = Query(default="canonical", pattern="^(legacy|canonical)$"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Side-by-side diff: live Wikidata entity vs the cached Studio item."""
    from app.pipeline.wikidata_entity_compare import (  # noqa: PLC0415
        build_compare,
        fetch_wikidata_entity,
    )

    await _lookup_run_with_access(db, run_id, auth, write=False)
    studio_item = await _studio_item_from_cache(
        db, run_id, local_id, approved_only=approved_only, source=source,
    )
    if studio_item is None:
        raise HTTPException(status_code=404, detail="Studio item not found in cache")

    target_qid = (qid or studio_item.get("existing_qid") or "").strip()
    if not target_qid:
        raise HTTPException(
            status_code=400,
            detail="No Wikidata QID — reconcile first or pass ?qid=Q…",
        )
    try:
        live = await fetch_wikidata_entity(target_qid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = build_compare(studio_item, live, target_qid)
    return result.model_dump()


class WikidataCompareApplyRequest(BaseModel):
    policy: str = Field(default="custom", pattern=r"^(wikidata|studio|custom)$")
    choices: list[dict[str, Any]] = Field(default_factory=list)
    qid: str | None = None
    approved_only: bool = True
    source: str = "canonical"


@router.post(
    "/{run_id}/wikidata-studio/items/{local_id:path}/wikidata-compare/apply",
    response_model=ItemOverrideResponse,
)
async def apply_wikidata_compare(
    run_id: uuid.UUID,
    local_id: str,
    body: WikidataCompareApplyRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ItemOverrideResponse:
    """Apply merge resolutions as curator overrides (labels / statements)."""
    from app.pipeline.wikidata_entity_compare import (  # noqa: PLC0415
        apply_compare_choices,
        build_compare,
        fetch_wikidata_entity,
    )

    await _lookup_run_with_access(db, run_id, auth, write=True)
    studio_item = await _studio_item_from_cache(
        db, run_id, local_id, approved_only=body.approved_only, source=body.source,
    )
    if studio_item is None:
        raise HTTPException(status_code=404, detail="Studio item not found in cache")

    target_qid = (body.qid or studio_item.get("existing_qid") or "").strip()
    if not target_qid:
        raise HTTPException(status_code=400, detail="No Wikidata QID to apply")

    live = await fetch_wikidata_entity(target_qid)
    compare = build_compare(studio_item, live, target_qid)
    fragment = apply_compare_choices(
        compare,
        policy=body.policy,
        choices=body.choices,
        studio_statements=list(studio_item.get("statements") or []),
    )

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
        await db.flush()

    if fragment.labels:
        new = dict(row.labels or {})
        for k, v in fragment.labels.items():
            if v is None:
                new.pop(k, None)
            else:
                new[k] = v
        row.labels = new
    if fragment.descriptions:
        new = dict(row.descriptions or {})
        for k, v in fragment.descriptions.items():
            if v is None:
                new.pop(k, None)
            else:
                new[k] = v
        row.descriptions = new
    if fragment.remove_statements:
        merged_rm = set(row.remove_statements or [])
        merged_rm.update(fragment.remove_statements)
        row.remove_statements = sorted(merged_rm)
    if fragment.add_statements:
        row.add_statements = list(row.add_statements or []) + list(fragment.add_statements)
    if fragment.statement_edits:
        new_edits = dict(row.statement_edits or {})
        new_edits.update(fragment.statement_edits)
        row.statement_edits = new_edits

    row.updated_by = auth.user.id
    await db.commit()
    await db.refresh(row)

    return ItemOverrideResponse(
        run_id=run_id,
        local_id=local_id,
        labels=row.labels or {},
        descriptions=row.descriptions or {},
        aliases=row.aliases or {},
        add_statements=row.add_statements or [],
        remove_statements=row.remove_statements or [],
        statement_edits=row.statement_edits or {},
        approved=row.approved,
    )


class WikidataAiFixApplyRequest(BaseModel):
    fixes: list[dict[str, Any]] = Field(default_factory=list)


@router.post(
    "/{run_id}/wikidata-studio/items/{local_id:path}/ai-fixes/apply",
    response_model=ItemOverrideResponse,
)
async def apply_wikidata_ai_fixes(
    run_id: uuid.UUID,
    local_id: str,
    body: WikidataAiFixApplyRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ItemOverrideResponse:
    """Merge high-confidence AI autofixes into curator overrides."""
    from app.pipeline.wikidata_autofix_apply import merge_ai_fixes  # noqa: PLC0415

    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    if not body.fixes:
        raise HTTPException(status_code=400, detail="no fixes to apply")

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
        await db.flush()

    fragment = merge_ai_fixes(
        body.fixes,
        labels=dict(row.labels or {}),
        descriptions=dict(row.descriptions or {}),
        add_statements=list(row.add_statements or []),
        remove_statements=list(row.remove_statements or []),
    )
    if fragment.get("labels") is not None:
        new = dict(row.labels or {})
        for k, v in fragment["labels"].items():
            if v is None:
                new.pop(k, None)
            else:
                new[k] = v
        row.labels = new
    if fragment.get("descriptions") is not None:
        new = dict(row.descriptions or {})
        for k, v in fragment["descriptions"].items():
            if v is None:
                new.pop(k, None)
            else:
                new[k] = v
        row.descriptions = new
    if fragment.get("add_statements"):
        row.add_statements = list(fragment["add_statements"])
    if fragment.get("remove_statements"):
        merged_rm = set(row.remove_statements or [])
        merged_rm.update(fragment["remove_statements"])
        row.remove_statements = sorted(merged_rm)

    row.updated_by = auth.user.id

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
            "labels": dict(row.labels or {}),
            "descriptions": dict(row.descriptions or {}),
            "aliases": dict(row.aliases or {}),
            "add_statements": list(row.add_statements or []),
            "remove_statements": list(row.remove_statements or []),
            "statement_edits": dict(row.statement_edits or {}),
            "approved": row.approved,
        }
        await apply_event(
            db,
            project_id=run.project_id,
            entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
            entity_id=entity_id_str,
            op=op_kind,
            new_state=new_state,
            actor_id=auth.user.id,
            message=f"wikidata AI autofix ({local_id})",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "apply_event failed for wikidata AI autofix %s: %s", entity_id_str, exc,
        )

    await db.commit()
    return ItemOverrideResponse(
        run_id=run_id,
        local_id=local_id,
        labels=row.labels or {},
        descriptions=row.descriptions or {},
        aliases=row.aliases or {},
        add_statements=row.add_statements or [],
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
    upload_outcome: str | None = None,
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
    if upload_outcome:
        filtered = [
            it for it in filtered
            if (it.get("upload_outcome") or "") == upload_outcome
        ]
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
    *, approved_only: bool, source: str = "canonical",
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
    if source == "canonical":
        canonical_rows = (await db.execute(
            select(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == run_id)
        )).scalars().all()
        canonical = [normalize_live_entity(row.snapshot) for row in canonical_rows]
        if not canonical:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="HMO Wikibase records are not ready for Wikidata projection; complete HMO read-back first.",
            )
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
        native = native_items_from_hmo(canonical)
        for item in native:
            ov = overrides.get(item.local_id)
            if ov:
                wikidata_studio._apply_override(item, ov)
        return native
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


async def _load_marc_records_for_run(db: AsyncSession, run_id: uuid.UUID) -> list[dict[str, Any]]:
    from app.pipeline.marc_verify_context import load_run_marc_records  # noqa: PLC0415

    return await load_run_marc_records(db, run_id)


async def _fetch_wikidata_verify_items(
    db: AsyncSession,
    run_id: uuid.UUID,
    auth: AuthContext,
    *,
    item_ids: list[str] | None,
    approved_only: bool,
    source: str = "canonical",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load Studio items for verify — prefer the curator-visible cache.

    Never SPARQL-reconcile ~N items on the verify path: a cache miss that
    rebuilt with ``reconcile=True`` hammered WDQS (429 / timeouts) and left
    the job looking stuck on QUEUED/running with no progress (Rule W-116).
    """
    if source not in ("legacy", "canonical"):
        source = "canonical"
    from app.pipeline.marc_verify_context import (  # noqa: PLC0415
        canonical_control_number,
        load_run_control_numbers,
        load_run_marc_records_scoped,
    )

    run_record_ids = await load_run_control_numbers(db, run_id)
    cached = await _get_studio_cache_row(db, run_id, approved_only, source)
    if cached is None or not (cached.result_items or []):
        # Fall back to the other approved_only cache for the same source when
        # the exact mode is empty — verify must still see the table the curator
        # already loaded (canonical pages often use approved_only=true).
        alt = await _get_studio_cache_row(db, run_id, not approved_only, source)
        if alt is not None and (alt.result_items or []):
            cached = alt
        else:
            cached = await execute_studio_build(
                db,
                run_id=run_id,
                approved_only=approved_only,
                force_rebuild=False,
                run_user_id=auth.user.id,
                source=source,
                reconcile=False,
            )
    scoped_items = filter_public_wikidata_items(
        cached.result_items or [],
        source=source,
    )

    wanted = {str(i).strip() for i in (item_ids or []) if str(i).strip()}
    items: list[dict[str, Any]] = []
    wanted_cns: set[str] = set()
    for item in scoped_items:
        local_id = str(item.get("local_id") or "")
        if wanted and local_id not in wanted:
            continue
        item["_local_id"] = local_id
        record_ids = [
            cn
            for cn in (
                canonical_control_number(value)
                for value in record_ids_for_wikidata_item(item)
            )
            if cn and cn in run_record_ids
        ]
        item["record_ids"] = record_ids
        wanted_cns.update(record_ids)
        items.append(item)
    attach_local_reference_targets(items)
    marc_records = await load_run_marc_records_scoped(db, run_id, wanted_cns)
    from app.pipeline.wikidata_verify_evidence import (  # noqa: PLC0415
        enrich_items_with_verify_evidence,
    )

    enrich_items_with_verify_evidence(items, marc_records)
    return items, marc_records

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
    streamed_fresh_verdict_keys: set[str] = set()
    runner_error: str | None = None
    runner_exit_code: int | None = None
    saw_runner_exit = False
    # Built early so each TRACE verdict can write-through to overrides +
    # inference cache before a dyno crash (Rule W-130).
    items_by_id = {
        str(i.get("_local_id") or i.get("local_id") or ""): i
        for i in items
    }
    persist_batch = None
    if uncached_items and not eval_agent_error:
        from app.pipeline.wikidata_item_verify import WikidataVerdictPersistBatch  # noqa: PLC0415

        persist_batch = WikidataVerdictPersistBatch(
            run_id=UUID(run_id),
            items_by_id=items_by_id,
            judge_model=tier_model or "gemini-3.5-flash",
            marc_records=marc_records,
        )

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
            from app.pipeline.wikidata_verify_fixture import (  # noqa: PLC0415
                release_wikidata_verify_heap,
            )

            release_wikidata_verify_heap(
                items=items,
                items_by_id=items_by_id,
                marc_records=marc_records,
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
                from app.pipeline.agent_runner import emit_session_event  # noqa: PLC0415

                await emit_session_event(session_dir, ev)
                yield ev
                if ev.type == "agent.verdict":
                    payload = dict(ev.payload or {})
                    from app.pipeline.verify_outcome import (  # noqa: PLC0415
                        verdict_candidate_local_id,
                    )

                    local_id = verdict_candidate_local_id(payload)
                    if local_id:
                        streamed_fresh_verdict_keys.add(local_id)
                        if persist_batch is not None:
                            persist_batch.enqueue(payload)
                elif ev.type == "runner.error":
                    runner_error = str((ev.payload or {}).get("message") or "verify failed")
                elif ev.type == "runner.exit":
                    saw_runner_exit = True
                    raw_rc = (ev.payload or {}).get("return_code")
                    try:
                        runner_exit_code = int(raw_rc) if raw_rc is not None else None
                    except (TypeError, ValueError):
                        runner_exit_code = None
    finally:
        if persist_batch is not None:
            try:
                await persist_batch.finish()
            except Exception:  # noqa: BLE001
                logger.exception("final Wikidata verdict persist batch failed")

        from app.pipeline.verify_outcome import (  # noqa: PLC0415
            merge_fresh_verdicts,
            resolve_verify_session_outcome,
            synthesize_missing_runner_error,
            verdict_candidate_local_id,
        )

        on_disk_verdicts = read_run_verdicts(state_dir) if (uncached_items and not eval_agent_error) else []
        fresh_verdicts = merge_fresh_verdicts(
            streamed=[],
            on_disk=on_disk_verdicts,
        )
        runner_error = synthesize_missing_runner_error(
            fresh_verdict_count=len(fresh_verdicts),
            scope_size=len(items),
            cache_hits=len(pre_cached),
            saw_runner_exit=saw_runner_exit or bool(eval_agent_error) or not uncached_items,
            runner_error=runner_error,
        )
        verdicts_to_persist: list[dict[str, Any]] = [
            _cached_wikidata_verdict_event(item, cached_payload)
            for item, cached_payload in pre_cached
        ]
        for v in fresh_verdicts:
            cand = v.get("candidate") if isinstance(v.get("candidate"), dict) else None
            local_id = verdict_candidate_local_id(v)
            if isinstance(cand, dict):
                item = items_by_id.get(local_id)
                if item is not None and not cand.get("label"):
                    cand["label"] = _item_label(item)
            if local_id not in streamed_fresh_verdict_keys:
                ev = AgentEvent(type="agent.verdict", payload=v)
                persist_session_event(session_dir, ev)
                yield ev
            verdicts_to_persist.append(v)

        if verdicts_to_persist:
            try:
                from app.pipeline.wikidata_item_verify import (  # noqa: PLC0415
                    _persist_wikidata_verdicts_to_overrides,
                )

                await _persist_wikidata_verdicts_to_overrides(
                    run_id=UUID(run_id),
                    items_by_id=items_by_id,
                    verdicts=verdicts_to_persist,
                    judge_model=tier_model or "gemini-3.5-flash",
                    marc_records=marc_records,
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to persist Wikidata item verdicts to overrides")

            try:
                await _write_wikidata_verdicts_to_cache(
                    items_by_id={
                        str(i.get("_local_id") or i.get("local_id") or ""): i
                        for i in uncached_items
                    },
                    verdicts=fresh_verdicts,
                    marc_records=marc_records,
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to write Wikidata item verdicts to inference cache")

        outcome = resolve_verify_session_outcome(
            eval_agent_unavailable=bool(eval_agent_error),
            uncached_count=len(uncached_items),
            fresh_verdict_count=len(fresh_verdicts),
            scope_size=len(items),
            cache_hits=len(pre_cached),
            runner_error=runner_error,
            runner_exit_code=runner_exit_code,
            saw_runner_exit=saw_runner_exit or bool(eval_agent_error) or not uncached_items,
        )
        end_ev = AgentEvent(
            type="session.end",
            payload={
                "session_id": session_id,
                "scope_size": len(items),
                "cache_hits": len(pre_cached),
                "fresh_verdicts": len(fresh_verdicts),
                "uncached_skipped": len(uncached_items) if eval_agent_error else 0,
                "outcome": outcome,
                "runner_error": runner_error,
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
    write_wikidata_verify_fixture(
        dest_dir=dest_dir,
        marc_records=marc_records,
        items=items,
    )


def _wikidata_verdict_query_summary(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "wikidata_item",
) -> dict[str, Any]:
    """Backward-compatible wrapper — prefer ``wikidata_verdict_cache``."""
    return wikidata_verdict_query_summary(item, judge_model, evaluator=evaluator)


async def _prepare_wikidata_verify_scope(
    action: agent_actions.AgentAction,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter/enrich verify scope for action-specific evaluators."""
    if action.id != "autofix_from_wikidata":
        return items
    scoped = [i for i in items if str(i.get("existing_qid") or "").strip()]
    if not scoped:
        return scoped
    from app.pipeline.wikidata_live_enrich import enrich_items_with_wikidata_live  # noqa: PLC0415

    return await enrich_items_with_wikidata_live(scoped)


def _cached_wikidata_verdict_event(
    item: dict[str, Any],
    cached_payload: dict[str, Any],
) -> dict[str, Any]:
    local_id = str(item.get("_local_id") or item.get("local_id") or "")
    record_ids = item.get("record_ids") if isinstance(item.get("record_ids"), list) else []
    return {
        "candidate": compact_wikidata_verdict_candidate(
            item,
            label=_item_label(item),
        ),
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
    marc_records: list[dict[str, Any]] | None = None,
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
            evaluator_id = str(
                v.get("evaluator_id") or v.get("evaluator") or "wikidata_item",
            )
            marc_ctx = item.get("_marc_context")
            if not isinstance(marc_ctx, dict):
                marc_ctx = marc_context_for_wikidata_item(item, marc_records or [])
            fingerprint = wikidata_verdict_input_fingerprint(
                item,
                judge_model,
                evaluator=evaluator_id,
                marc_context=marc_ctx if isinstance(marc_ctx, dict) else None,
            )
            cached_result = {
                "verdict": v.get("verdict") or {},
                "judge_id": v.get("judge_id") or v.get("model"),
                "judged_at": v.get("judged_at"),
                "cache_key": fingerprint,
                "evaluator": v.get("evaluator_id") or v.get("evaluator") or "wikidata_item",
                "confidence": v.get("confidence"),
                "sub_type": v.get("sub_type"),
                "record_id": v.get("record_id"),
            }
            await write_to_inference_cache(
                db,
                kind="ai_verdict",
                query_summary=wikidata_verdict_query_summary(
                    item, judge_model, evaluator=evaluator_id,
                    marc_context=marc_ctx if isinstance(marc_ctx, dict) else None,
                ),
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
    source: str = "legacy",
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
                source=source,
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
