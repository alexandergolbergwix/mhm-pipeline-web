"""HMO Wikibase Studio per-item review API."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session, session_scope
from app.export.formatters import json_stream
from app.models.event import (
    ENTITY_TYPE_HMO_ITEM_OVERRIDE,
    OP_CREATE,
    OP_PATCH,
    ProjectEvent,
)
from app.models.hmo_studio_item_override import HmoStudioItemOverride
from app.models.run import RunRecord
from app.pipeline import hmo_item_actions
from app.pipeline.agent_runner import (
    list_verify_sessions,
    new_session_id,
    read_verify_session,
    sse_stream,
)
from app.pipeline.hmo_item_merge import override_row_to_dict
from app.pipeline.hmo_item_reconcile import (
    ReconciliationUnavailableError,
    reconcile_item,
)
from app.pipeline.hmo_item_verify import (
    HMO_ITEM_VERIFY_CHANNEL,
    cached_hmo_item_verdict_event,
    hmo_item_verdict_query_summary,
    hmo_item_verify_event_stream,
)
from app.pipeline.hmo_item_views import ItemBuildMissingError, fetch_merged_hmo_items, item_label
from app.pipeline.hmo_wikibase_live_enrich import enrich_hmo_items_with_wikibase_live
from app.pipeline.inference_cache import read_from_inference_cache
from app.pipeline.wikidata_autofix_apply import merge_ai_fixes
from app.routers.runs import _lookup_run_with_access
from app.versioning import apply_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/runs", tags=["hmo-studio-items"])


class HmoItemOverridePayload(BaseModel):
    labels: dict[str, str | None] | None = None
    descriptions: dict[str, str | None] | None = None
    aliases: dict[str, list[str] | None] | None = None
    add_statements: list[dict[str, Any]] | None = None
    remove_statements: list[int] | None = None
    statement_edits: dict[str, dict[str, Any]] | None = None
    approved: bool | None = None


class HmoItemOverrideResponse(BaseModel):
    run_id: uuid.UUID
    local_id: str
    labels: dict[str, Any]
    descriptions: dict[str, Any]
    aliases: dict[str, Any]
    add_statements: list[dict[str, Any]]
    remove_statements: list[int]
    statement_edits: dict[str, Any]
    approved: bool | None = None


class HmoItemsListResponse(BaseModel):
    run_id: uuid.UUID
    items: list[dict[str, Any]]


class HmoItemVerifyStartRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=64)
    item_ids: list[str] | None = None
    override_cache: bool = False
    tier_model: str | None = Field(default=None, max_length=64)


class HmoAiFixApplyRequest(BaseModel):
    fixes: list[dict[str, Any]] = Field(default_factory=list)


class HmoItemPushResponse(BaseModel):
    local_id: str
    source_uri: str
    status: str
    wikibase_id: str | None = None
    message: str = ""


class HmoItemsImportRequest(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/{run_id}/hmo-studio/items", response_model=HmoItemsListResponse)
async def list_hmo_items(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoItemsListResponse:
    await _lookup_run_with_access(db, run_id, auth)
    try:
        items = await fetch_merged_hmo_items(db, run_id)
    except ItemBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return HmoItemsListResponse(run_id=run_id, items=items)


@router.patch(
    "/{run_id}/hmo-studio/items/{local_id:path}/override",
    response_model=HmoItemOverrideResponse,
)
async def patch_hmo_item_override(
    run_id: uuid.UUID,
    local_id: str,
    payload: HmoItemOverridePayload,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoItemOverrideResponse:
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    try:
        await fetch_merged_hmo_items(db, run_id)
    except ItemBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    known_ids = {
        str(i.get("local_id") or "")
        for i in (await fetch_merged_hmo_items(db, run_id))
    }
    if local_id not in known_ids:
        raise HTTPException(status_code=404, detail=f"unknown local_id {local_id!r}")

    row = (
        await db.execute(
            select(HmoStudioItemOverride).where(
                HmoStudioItemOverride.run_id == run_id,
                HmoStudioItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = HmoStudioItemOverride(
            run_id=run_id, local_id=local_id, updated_by=auth.user.id,
        )
        db.add(row)
        await db.flush()

    if payload.labels is not None:
        new = dict(row.labels or {})
        for k, v in payload.labels.items():
            if v is None:
                new.pop(k, None)
            else:
                new[k] = v
        row.labels = new
    if payload.descriptions is not None:
        new = dict(row.descriptions or {})
        for k, v in payload.descriptions.items():
            if v is None:
                new.pop(k, None)
            else:
                new[k] = v
        row.descriptions = new
    if payload.aliases is not None:
        new = dict(row.aliases or {})
        for lang, vals in payload.aliases.items():
            if vals is None:
                new.pop(lang, None)
            else:
                new[lang] = list(vals)
        row.aliases = new
    if payload.add_statements is not None:
        row.add_statements = list(payload.add_statements)
    if payload.remove_statements is not None:
        row.remove_statements = list(payload.remove_statements)
    if payload.statement_edits is not None:
        new_edits = dict(row.statement_edits or {})
        for k, v in payload.statement_edits.items():
            if v is None:
                new_edits.pop(k, None)
            else:
                new_edits[k] = v
        row.statement_edits = new_edits
    if payload.approved is not None:
        row.approved = payload.approved
    row.updated_by = auth.user.id

    entity_id_str = str(row.id)
    try:
        has_history = (
            await db.execute(
                select(ProjectEvent.id)
                .where(
                    ProjectEvent.entity_type == ENTITY_TYPE_HMO_ITEM_OVERRIDE,
                    ProjectEvent.entity_id == entity_id_str,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        op_kind = OP_PATCH if has_history else OP_CREATE
        await apply_event(
            db,
            project_id=run.project_id,
            entity_type=ENTITY_TYPE_HMO_ITEM_OVERRIDE,
            entity_id=entity_id_str,
            op=op_kind,
            new_state=override_row_to_dict(row),
            actor_id=auth.user.id,
            message=f"hmo item override edit ({local_id})",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "apply_event failed for hmo_item_override %s: %s", entity_id_str, exc,
        )

    await db.commit()
    return HmoItemOverrideResponse(
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


@router.post("/{run_id}/hmo-studio/items/{local_id:path}/reconcile")
async def reconcile_hmo_item(
    run_id: uuid.UUID,
    local_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    items = await fetch_merged_hmo_items(db, run_id)
    item = next((i for i in items if i.get("local_id") == local_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=f"unknown local_id {local_id!r}")

    source_uri = str(item.get("source_uri") or "")
    if item.get("wikibase_id"):
        return {
            "local_id": local_id,
            "source_uri": source_uri,
            "wikibase_id": item.get("wikibase_id"),
            "status": "already_mapped",
        }

    try:
        outcome = await reconcile_item(db, source_uri)
    except ReconciliationUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if not outcome.found or not outcome.wikibase_id:
        return {
            "local_id": local_id,
            "source_uri": source_uri,
            "wikibase_id": None,
            "status": "not_found",
            "message": outcome.message,
        }

    from app.pipeline.hmo_item_upload import _record_instance_mapping  # noqa: PLC0415

    await _record_instance_mapping(
        db,
        source_uri=source_uri,
        wikibase_id=outcome.wikibase_id,
        run_id=run_id,
        label=item_label(item),
    )
    return {
        "local_id": local_id,
        "source_uri": source_uri,
        "wikibase_id": outcome.wikibase_id,
        "status": "adopted",
        "message": outcome.message,
    }


@router.post(
    "/{run_id}/hmo-studio/items/{local_id:path}/push",
    response_model=HmoItemPushResponse,
)
async def push_hmo_item(
    run_id: uuid.UUID,
    local_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoItemPushResponse:
    """Create-or-update exactly this one item on the live Wikibase Cloud,
    using its current override-merged build state.

    Lets a curator go from "apply an AI-suggested fix" (which only updates
    the override row) straight to "the live item now reflects the fix"
    without re-running the whole corpus upload with ``update_existing``.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    try:
        items = await fetch_merged_hmo_items(db, run_id)
    except ItemBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    item = next((i for i in items if i.get("local_id") == local_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=f"unknown local_id {local_id!r}")

    from app.models.wikibase_cloud_write import CHANNEL_ITEM_UPLOAD  # noqa: PLC0415
    from app.pipeline.hmo_item_reconcile import resolve_source_uri_pid  # noqa: PLC0415
    from app.pipeline.hmo_item_upload import push_single_item  # noqa: PLC0415
    from app.services.wikibase_audit import WikibaseAuditContext  # noqa: PLC0415
    from app.services.wikibase_credentials import build_server_wikibase_writer  # noqa: PLC0415
    from converter.wikibase.resolved_models import ResolvedWikibaseEntity  # noqa: PLC0415

    writer = build_server_wikibase_writer()
    entity = ResolvedWikibaseEntity.from_dict(item)
    existing_qid = item.get("wikibase_id")
    reconcile_pid = None if existing_qid else await resolve_source_uri_pid(db)

    # Close out the read transaction (run lookup, item fetch, pid lookup)
    # before the slow live Wikibase Cloud / SPARQL call below — never hold
    # a DB transaction open across external I/O (Rule G5 / W-40).
    await db.commit()

    outcome = await push_single_item(
        db, run_id, entity,
        writer=writer,
        audit_ctx=WikibaseAuditContext(
            actor_user_id=auth.user.id,
            project_id=run.project_id,
            run_id=run_id,
            job_id=None,
            channel=CHANNEL_ITEM_UPLOAD,
        ),
        update_existing=True,
        reconcile_pid=reconcile_pid,
        existing_qid=existing_qid,
    )
    return HmoItemPushResponse(
        local_id=outcome.local_id,
        source_uri=outcome.source_uri,
        status=outcome.status,
        wikibase_id=outcome.wikibase_id,
        message=outcome.message,
    )


@router.get("/{run_id}/hmo-studio/items/export")
async def export_hmo_items(
    run_id: uuid.UUID,
    format: Literal["json", "csv"] = Query(default="json"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _lookup_run_with_access(db, run_id, auth)
    try:
        items = await fetch_merged_hmo_items(db, run_id)
    except ItemBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    filename = f"run-{run_id}-hmo-wikibase-items.{format}"
    if format == "json":
        return StreamingResponse(
            json_stream({"run_id": str(run_id), "items": items}),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _csv_stream():
        buf = io.StringIO()
        fields = [
            "local_id", "class_qid", "source_uri", "status", "wikibase_id",
            "approved", "label_en", "label_he",
        ]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for it in items:
            labels = it.get("labels") or {}
            writer.writerow({
                "local_id": it.get("local_id"),
                "class_qid": it.get("class_qid"),
                "source_uri": it.get("source_uri"),
                "status": it.get("status"),
                "wikibase_id": it.get("wikibase_id"),
                "approved": it.get("approved"),
                "label_en": labels.get("en"),
                "label_he": labels.get("he"),
            })
        yield buf.getvalue()

    return StreamingResponse(
        _csv_stream(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{run_id}/hmo-studio/items/import")
async def import_hmo_items(
    run_id: uuid.UUID,
    body: HmoItemsImportRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    known = {
        str(i.get("local_id") or "")
        for i in await fetch_merged_hmo_items(db, run_id)
    }
    imported = skipped = 0
    errors: list[str] = []

    for row in body.items:
        local_id = str(row.get("local_id") or "")
        if not local_id or local_id not in known:
            skipped += 1
            errors.append(f"unknown local_id {local_id!r}")
            continue
        payload = HmoItemOverridePayload(
            labels=row.get("labels"),
            descriptions=row.get("descriptions"),
            aliases=row.get("aliases"),
            add_statements=row.get("add_statements"),
            remove_statements=row.get("remove_statements"),
            statement_edits=row.get("statement_edits"),
            approved=row.get("approved"),
        )
        await patch_hmo_item_override(run_id, local_id, payload, auth, db)
        imported += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


@router.post(
    "/{run_id}/hmo-studio/items/{local_id:path}/ai-fixes/apply",
    response_model=HmoItemOverrideResponse,
)
async def apply_hmo_ai_fixes(
    run_id: uuid.UUID,
    local_id: str,
    body: HmoAiFixApplyRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoItemOverrideResponse:
    if not body.fixes:
        raise HTTPException(status_code=400, detail="no fixes to apply")

    row = (
        await db.execute(
            select(HmoStudioItemOverride).where(
                HmoStudioItemOverride.run_id == run_id,
                HmoStudioItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = HmoStudioItemOverride(
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
    payload = HmoItemOverridePayload(**{
        k: v for k, v in fragment.items() if v is not None
    })
    return await patch_hmo_item_override(run_id, local_id, payload, auth, db)


@router.get("/{run_id}/hmo-studio/items/ai-verify/actions")
async def list_hmo_item_verify_actions(
    run_id: uuid.UUID,
    scope_kind: str = Query("selection", pattern=r"^(single|selection|all)$"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return [
        hmo_item_actions.to_dict(a)
        for a in hmo_item_actions.list_actions(scope_kind=scope_kind)  # type: ignore[arg-type]
    ]


@router.get("/{run_id}/hmo-studio/items/ai-verify/cached-verdicts")
async def get_cached_hmo_item_verdicts(
    run_id: uuid.UUID,
    tier_model: str | None = Query(default=None),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    from app.pipeline.ai_verifier import GEMINI_MODEL  # noqa: PLC0415

    judge_model = tier_model or GEMINI_MODEL
    items = await fetch_merged_hmo_items(db, run_id)
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        local_id = str(item.get("local_id") or "")
        if item.get("ai_verdict"):
            out[local_id] = item["ai_verdict"]
            continue
        hit = await read_from_inference_cache(
            db,
            kind="ai_verdict",
            query_summary=hmo_item_verdict_query_summary(item, judge_model),
        )
        if hit is None:
            continue
        verdict = hit.get("verdict") or {} if isinstance(hit, dict) else {}
        out[local_id] = {
            "overall": verdict.get("overall") or "unknown",
            "reasoning": verdict.get("reasoning"),
            "model": hit.get("judge_id") if isinstance(hit, dict) else None,
            "evaluator": (hit.get("evaluator") if isinstance(hit, dict) else None) or "hmo_wikibase_item",
        }
    return out


@router.post("/{run_id}/hmo-studio/items/ai-verify/start-stream")
async def start_hmo_item_verify_stream(
    run_id: uuid.UUID,
    payload: HmoItemVerifyStartRequest,
    auth: AuthContext = Depends(current_auth),
) -> StreamingResponse:
    async with session_scope() as db:
        await _lookup_run_with_access(db, run_id, auth, write=False)
        action = hmo_item_actions.get_action(payload.action_id)
        if action is None:
            raise HTTPException(status_code=400, detail=f"unknown action_id {payload.action_id!r}")

        items = await _fetch_verify_items(db, run_id, item_ids=payload.item_ids)
        items = await _prepare_verify_scope(action, items)
        if not items:
            raise HTTPException(status_code=400, detail="no HMO items in scope")
        if len(items) < action.min_candidates:
            raise HTTPException(
                status_code=400,
                detail=f"action requires at least {action.min_candidates} candidates",
            )

        from app.pipeline.ai_verifier import GEMINI_MODEL  # noqa: PLC0415

        judge_model = payload.tier_model or GEMINI_MODEL
        evaluator_id = action.evaluators[0] if action.evaluators else "hmo_wikibase_item"
        pre_cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
        uncached: list[dict[str, Any]] = []
        if not payload.override_cache:
            for item in items:
                hit = await read_from_inference_cache(
                    db,
                    kind="ai_verdict",
                    query_summary=hmo_item_verdict_query_summary(
                        item, judge_model, evaluator=evaluator_id,
                    ),
                )
                if hit is not None:
                    pre_cached.append((item, hit))
                else:
                    uncached.append(item)
        else:
            uncached = list(items)

        marc_records = await _load_marc_records(db, run_id)
        api_key = await _resolve_gemini_key(db, auth)
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="No Gemini API key configured.",
            )

    session_id = new_session_id()
    return StreamingResponse(
        sse_stream(hmo_item_verify_event_stream(
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
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
        },
    )


@router.get("/{run_id}/hmo-studio/items/ai-verify/sessions")
async def list_hmo_item_verify_sessions(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return list_verify_sessions(HMO_ITEM_VERIFY_CHANNEL, str(run_id))


@router.get("/{run_id}/hmo-studio/items/ai-verify/sessions/{session_id}")
async def get_hmo_item_verify_session(
    run_id: uuid.UUID,
    session_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    data = read_verify_session(HMO_ITEM_VERIFY_CHANNEL, str(run_id), session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return data


async def _fetch_verify_items(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    item_ids: list[str] | None,
) -> list[dict[str, Any]]:
    items = await fetch_merged_hmo_items(db, run_id)
    wanted = set(item_ids or [])
    out: list[dict[str, Any]] = []
    for raw in items:
        local_id = str(raw.get("local_id") or "")
        if wanted and local_id not in wanted:
            continue
        item = dict(raw)
        item["_local_id"] = local_id
        item["label"] = item_label(item)
        out.append(item)
    return out


async def _prepare_verify_scope(
    action: Any,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if action.id != "autofix_hmo_wikibase_item":
        return items
    scoped = [i for i in items if str(i.get("wikibase_id") or "").strip()]
    if not scoped:
        return scoped
    return await enrich_hmo_items_with_wikibase_live(scoped)


async def _load_marc_records(db: AsyncSession, run_id: uuid.UUID) -> list[dict[str, Any]]:
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    return [dict(r.marc or {"_control_number": r.control_number}) for r in records]


async def _resolve_gemini_key(db: AsyncSession, auth: AuthContext) -> str | None:
    try:
        from app.pipeline.ai_verifier import unwrap_user_gemini_key  # noqa: PLC0415

        key = await unwrap_user_gemini_key(db, user_id=auth.user.id, kek=auth.kek)
        if key:
            return key
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not unwrap stored Gemini key: %s", exc)
    return os.environ.get("GEMINI_API_KEY")
