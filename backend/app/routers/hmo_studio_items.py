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
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB as _JSONB
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
from app.pipeline.hmo_item_verdict_cache import (
    hmo_item_verdict_query_summary,
    sanitise_stale_hmo_item_verdict,
)
from app.pipeline.hmo_item_verify import (
    HMO_ITEM_VERIFY_CHANNEL,
    hmo_item_verify_event_stream,
)
from app.pipeline.hmo_item_views import (
    ItemBuildMissingError,
    fetch_merged_hmo_items_cached,
    fetch_validation_error_items,
    item_label,
)
from app.pipeline.marc_verify_context import attach_marc_context, load_run_marc_records
from app.pipeline.hmo_wikibase_live_enrich import enrich_hmo_items_with_wikibase_live
from app.pipeline.inference_cache import read_from_inference_cache
from app.pipeline.rule_verify.base import DEFAULT_BLOCKING_RULES, STATE_FAIL
from app.pipeline.rule_verify.rules.hmo import build_hmo_rules
from app.pipeline.studio_item_bulk_approve import MAX_BULK_APPROVE_IDS
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
        items = await fetch_merged_hmo_items_cached(db, run_id)
    except ItemBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return HmoItemsListResponse(run_id=run_id, items=items)


@router.get("/{run_id}/hmo-studio/items/page")
async def list_hmo_items_page(
    run_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    q: str = Query(default=""),
    sort: Literal["label", "local_id"] = Query(default="label"),
    dir: Literal["asc", "desc"] = Query(default="asc"),
    filter: list[str] = Query(default=[], description="Repeated col:value pairs"),
    ids_only: bool = Query(
        default=False,
        description="Return the full filtered local_id list (no rows)",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """One page of the review table, filtered and sorted server-side.

    Reads the in-process cached merged list (the 50-100 MB deserialise
    happens once per run, W-239) and returns PAGE_SIZE rows — the browser
    never receives the full corpus, so any entity count is safe.
    ``ids_only=true`` returns every matching local_id (bulk actions).
    """
    await _lookup_run_with_access(db, run_id, auth)
    try:
        items = await fetch_merged_hmo_items_cached(db, run_id)
    except ItemBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    col_filters: dict[str, set[str]] = {}
    for raw in filter:
        col, _, value = str(raw).partition(":")
        if col and value:
            col_filters.setdefault(col, set()).add(value)

    def matches_query(item: dict[str, Any], needle: str) -> bool:
        labels = item.get("labels") or {}
        haystack_parts = [
            *[str(v) for v in labels.values() if v],
            str(item.get("local_id") or ""),
            str(item.get("class_qid") or ""),
            str(item.get("source_uri") or ""),
            *[str(v) for v in (item.get("descriptions") or {}).values() if v],
            *[str(cn) for cn in item.get("control_numbers") or []],
            *[
                str(c.get("value"))
                for c in item.get("claims") or []
                if isinstance(c, dict) and isinstance(c.get("value"), str)
            ],
        ]
        return any(needle in part.lower() for part in haystack_parts)

    needle = q.strip().lower()
    if needle:
        items = [i for i in items if matches_query(i, needle)]
    for col, wanted in col_filters.items():
        items = [
            i for i in items
            if bool(set(_item_cell_values(i, col)) & wanted)
        ]

    if ids_only:
        return {
            "run_id": str(run_id),
            "total": len(items),
            "entries": [
                {
                    "local_id": str(i.get("local_id") or ""),
                    "wikibase_id": i.get("wikibase_id"),
                    "approved": i.get("approved"),
                }
                for i in items
            ],
        }

    reverse = dir == "desc"
    if sort == "local_id":
        items = sorted(items, key=lambda i: str(i.get("local_id") or ""), reverse=reverse)
    else:
        items = sorted(
            items,
            key=lambda i: _item_label(i).lower(),
            reverse=reverse,
        )

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    facets: dict[str, dict[str, int]] = {}
    for col in _FACET_COLUMNS:
        counts: dict[str, int] = {}
        for item in items:
            for value in _item_cell_values(item, col):
                counts[value] = counts.get(value, 0) + 1
        facets[col] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    return {
        "run_id": str(run_id),
        "page": page,
        "page_size": page_size,
        "total": total,
        "facets": facets,
        "items": page_items,
    }


_FACET_COLUMNS = (
    "approved", "validation", "data_status", "ai_verdict", "rule_verdict",
    "type", "upload_outcome", "wikibase_id", "authority",
)


def _item_label(item: dict[str, Any]) -> str:
    labels = item.get("labels") or {}
    for key in ("en", "he"):
        if labels.get(key):
            return str(labels[key])
    for value in labels.values():
        if value:
            return str(value)
    return str(item.get("local_id") or "")


def _item_data_status(item: dict[str, Any]) -> str:
    if item.get("upload_outcome") == "failed":
        return "failed"
    if item.get("status") == "would_create":
        return "new"
    if item.get("upload_outcome") == "update":
        return "updated"
    return "will_update"


def _item_validation(item: dict[str, Any]) -> str:
    if item.get("has_blocking_shacl"):
        return "blocked"
    issues = item.get("shacl_issues") or []
    if not issues:
        return "ok"
    if any(
        str(i.get("severity")) in ("Violation", "Error")
        for i in issues if isinstance(i, dict)
    ):
        return "error"
    return "warn"


def _item_rule_verdict_value(item: dict[str, Any]) -> str:
    verdict = item.get("rule_verdict")
    if not isinstance(verdict, dict):
        return "not checked"
    if int(verdict.get("fail_count") or 0) > 0:
        return "fail"
    if int(verdict.get("error_count") or 0) > 0:
        return "error"
    return str(verdict.get("overall") or "pass")


def _item_authority_kinds(item: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for evidence in item.get("authority_evidence") or []:
        if isinstance(evidence, dict) and evidence.get("accepted"):
            kind = str(evidence.get("kind") or evidence.get("source") or "authority")
            if kind not in kinds:
                kinds.append(kind)
    for claim in item.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        value = str(claim.get("value") or "")
        if "wikidata.org" in value and "Wikidata" not in kinds:
            kinds.append("Wikidata")
        elif "viaf.org" in value and "VIAF" not in kinds:
            kinds.append("VIAF")
        elif "nli.org.il" in value and "Mazal/NLI" not in kinds:
            kinds.append("Mazal/NLI")
    return kinds


def _item_cell_values(item: dict[str, Any], col: str) -> list[str]:
    if col == "approved":
        if item.get("approved") is True:
            return ["approved"]
        if item.get("approved") is False:
            return ["rejected"]
        return ["pending"]
    if col == "validation":
        return [_item_validation(item)]
    if col == "data_status":
        return [_item_data_status(item)]
    if col == "ai_verdict":
        verdict = item.get("ai_verdict")
        if isinstance(verdict, dict) and verdict.get("overall"):
            return [str(verdict.get("overall"))]
        return ["not verified"]
    if col == "rule_verdict":
        return [_item_rule_verdict_value(item)]
    if col == "type":
        return [str(item.get("class_qid") or "")]
    if col == "upload_outcome":
        return [str(item.get("upload_outcome") or "never")]
    if col == "wikibase_id":
        return [str(item.get("wikibase_id") or "—")]
    if col == "authority":
        return _item_authority_kinds(item)
    value = item.get(col)
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)] if value else []


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
        await fetch_merged_hmo_items_cached(db, run_id)
    except ItemBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    known_ids = {
        str(i.get("local_id") or "")
        for i in (await fetch_merged_hmo_items_cached(db, run_id))
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
    items = await fetch_merged_hmo_items_cached(db, run_id)
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


class HmoValidationErrorsResponse(BaseModel):
    run_id: uuid.UUID
    count: int
    items: list[dict[str, Any]]


@router.get(
    "/{run_id}/hmo-studio/items/validation-errors",
    response_model=HmoValidationErrorsResponse,
)
async def list_validation_error_items(
    run_id: uuid.UUID,
    on_wiki_only: bool = Query(
        default=False,
        description="When true, return only items already on the wiki (status=created).",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> HmoValidationErrorsResponse:
    """Items whose SHACL report has Violation/Error-level issues."""
    await _lookup_run_with_access(db, run_id, auth)
    try:
        items = await fetch_validation_error_items(
            db, run_id, on_wiki_only=on_wiki_only,
        )
    except ItemBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return HmoValidationErrorsResponse(run_id=run_id, count=len(items), items=items)


@router.post(
    "/{run_id}/hmo-studio/items/{local_id:path}/push",
    response_model=HmoItemPushResponse,
)
async def push_hmo_item(
    run_id: uuid.UUID,
    local_id: str,
    allow_shacl_errors: bool = Query(default=False),
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
        items = await fetch_merged_hmo_items_cached(db, run_id)
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
        allow_shacl_errors=allow_shacl_errors,
        shacl_issues=item.get("shacl_issues"),
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
        items = await fetch_merged_hmo_items_cached(db, run_id)
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
            "ai_verdict_overall", "ai_verdict_reasoning", "ai_verdict_model",
            "ai_verdict_judged_at",
        ]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for it in items:
            labels = it.get("labels") or {}
            av = it.get("ai_verdict") if isinstance(it.get("ai_verdict"), dict) else {}
            writer.writerow({
                "local_id": it.get("local_id"),
                "class_qid": it.get("class_qid"),
                "source_uri": it.get("source_uri"),
                "status": it.get("status"),
                "wikibase_id": it.get("wikibase_id"),
                "approved": it.get("approved"),
                "label_en": labels.get("en"),
                "label_he": labels.get("he"),
                "ai_verdict_overall": av.get("overall"),
                "ai_verdict_reasoning": av.get("reasoning"),
                "ai_verdict_model": av.get("model"),
                "ai_verdict_judged_at": it.get("ai_verdict_at") or av.get("judged_at"),
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
        for i in await fetch_merged_hmo_items_cached(db, run_id)
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
    items = await fetch_merged_hmo_items_cached(db, run_id)
    marc_records = await load_run_marc_records(db, run_id)
    attach_marc_context(items, marc_records)
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        local_id = str(item.get("local_id") or "")
        fresh = sanitise_stale_hmo_item_verdict(
            item,
            judge_model=judge_model,
            marc_context=(
                item.get("_marc_context")
                if isinstance(item.get("_marc_context"), dict) else None
            ),
        )
        if fresh:
            out[local_id] = fresh
            continue
        hit = await read_from_inference_cache(
            db,
            kind="ai_verdict",
            query_summary=hmo_item_verdict_query_summary(item, judge_model),
        )
        if hit is None:
            continue
        verdict = hit.get("verdict") or {} if isinstance(hit, dict) else {}
        from app.pipeline.ai_verdict_cache_common import normalise_public_verdict  # noqa: PLC0415
        verdict = normalise_public_verdict(verdict)
        out[local_id] = {
            "overall": verdict.get("overall") or "abstain",
            "reasoning": verdict.get("reasoning"),
            "model": hit.get("judge_id") if isinstance(hit, dict) else None,
            "evaluator": (
                (hit.get("evaluator") if isinstance(hit, dict) else None)
                or "hmo_wikibase_item"
            ),
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
        marc_records = await _load_marc_records(db, run_id)
        attach_marc_context(items, marc_records)
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
    items = await fetch_merged_hmo_items_cached(db, run_id)
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
    return await load_run_marc_records(db, run_id)


async def _resolve_gemini_key(db: AsyncSession, auth: AuthContext) -> str | None:
    try:
        from app.pipeline.ai_verifier import unwrap_user_gemini_key  # noqa: PLC0415

        key = await unwrap_user_gemini_key(db, user_id=auth.user.id, kek=auth.kek)
        if key:
            return key
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not unwrap stored Gemini key: %s", exc)
    return os.environ.get("GEMINI_API_KEY")


# ── Rule-based verification (deterministic, non-AI) ─────────────────────
# Advisory only: rules never block upload, and bulk approval treats a
# rule as blocking only when the curator opted in (see /me settings).


class RuleVerifyFilterSpec(BaseModel):
    state: str = "pass"
    rules: list[str] = Field(default_factory=list)
    q: str = ""


class RuleVerifyBulkApproveRequest(BaseModel):
    local_ids: list[str] = Field(default_factory=list)
    filters: RuleVerifyFilterSpec | None = Field(
        default=None,
        description="Server-side filter; required when local_ids is empty.",
    )
    blocking_rules: list[str] | None = Field(
        default=None,
        description="Rule ids that must not be failing. Defaults to the "
        "curator's saved blocking set (or the curated defaults).",
    )
    blocking_rules_from_settings: bool = True


class RuleVerifyBulkApprovePreview(BaseModel):
    total: int
    eligible: list[str]
    excluded: list[dict[str, Any]]
    not_checked: list[str]


@router.get("/{run_id}/hmo-studio/items/rule-verify/catalog")
async def get_rule_verify_catalog(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return [
        {
            "id": r.id,
            "title": r.title,
            "description": r.description,
            "channel": r.channel,
            "uses_api": r.uses_api,
        }
        for r in build_hmo_rules()
    ]


@router.get("/{run_id}/hmo-studio/items/rule-verify/results")
async def get_rule_verify_results(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Summary-only: per-rule tallies + overall counts.

    Scans the override rows (verdict JSONB) — never the merged item view,
    so the response size is independent of the entity count. Entities come
    from the paginated ``/results/entities`` endpoint.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)

    from app.pipeline.rule_verify.base import RULE_STATES

    rows = (
        await db.execute(
            select(HmoStudioItemOverride.rule_verdict).where(
                HmoStudioItemOverride.run_id == run_id,
                HmoStudioItemOverride.rule_verdict.is_not(None),
            )
        )
    ).scalars().all()

    per_rule: dict[str, dict[str, int]] = {}
    overall_counts: dict[str, int] = {s: 0 for s in RULE_STATES}
    overall_counts["unchecked"] = 0
    for verdict in rows:
        if not isinstance(verdict, dict) or not verdict.get("results"):
            continue
        overall_counts[str(verdict.get("overall"))] = (
            overall_counts.get(str(verdict.get("overall")), 0) + 1
        )
        for res in verdict["results"]:
            if not isinstance(res, dict):
                continue
            tally = per_rule.setdefault(
                str(res.get("rule_id")),
                {s: 0 for s in RULE_STATES},
            )
            state = str(res.get("state"))
            if state in tally:
                tally[state] += 1
    return {
        "run_id": str(run_id),
        "overall_counts": overall_counts,
        "per_rule": per_rule,
    }


_STATE_SORT_SQL = {
    "fail": 0, "error": 1, "pass": 2, "not_relevant": 3,
}


def _verdict_filter_conditions(
    *,
    state: str,
    rules: list[str],
    q: str,
):
    """SQL conditions over the verdict JSONB (Postgres)."""
    from app.models.hmo_studio_item_override import HmoStudioItemOverride
    from app.pipeline.rule_verify.base import RULE_STATES

    if state and state not in (*RULE_STATES, "all"):
        raise HTTPException(status_code=400, detail=f"unknown state {state!r}")

    verdict_col = HmoStudioItemOverride.rule_verdict
    conditions = [verdict_col.is_not(None)]
    if state and state != "all":
        conditions.append(verdict_col["overall"].astext == state)
    for rule_id in rules:
        contains = cast(
            json.dumps([{"rule_id": rule_id, "state": "fail"}]),
            _JSONB,
        )
        conditions.append(verdict_col["results"].op("@>")(contains))
    if q.strip():
        pattern = f"%{q.strip()}%"
        conditions.append(
            verdict_col["label"].astext.ilike(pattern)
            | HmoStudioItemOverride.local_id.ilike(pattern)
        )
    return conditions


async def _query_verdict_page(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    state: str,
    rules: list[str],
    q: str,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page of verdicts + total, filtered and sorted worst-first."""
    from sqlalchemy import case, func

    from app.models.hmo_studio_item_override import HmoStudioItemOverride

    conditions = _verdict_filter_conditions(state=state, rules=rules, q=q)
    base = select(HmoStudioItemOverride).where(
        HmoStudioItemOverride.run_id == run_id, *conditions,
    )
    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    sort_rank = case(
        *(  # worst first, then stable by local_id
            (HmoStudioItemOverride.rule_verdict["overall"].astext == key, rank)
            for key, rank in _STATE_SORT_SQL.items()
        ),
        else_=4,
    )
    rows = (
        await db.execute(
            base.order_by(sort_rank, HmoStudioItemOverride.local_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    out = []
    for row in rows:
        verdict = row.rule_verdict if isinstance(row.rule_verdict, dict) else {}
        results = [r for r in verdict.get("results") or [] if isinstance(r, dict)]
        out.append({
            "local_id": row.local_id,
            "label": verdict.get("label") or row.local_id,
            "class_qid": verdict.get("class_qid"),
            "approved": row.approved,
            "overall": verdict.get("overall") or "unchecked",
            "checked_at": verdict.get("checked_at"),
            "pass_count": sum(1 for r in results if r.get("state") == "pass"),
            "results": [r for r in results if r.get("state") != "pass"],
        })
    return out, int(total)


@router.get("/{run_id}/hmo-studio/items/rule-verify/results/entities")
async def get_rule_verify_entity_page(
    run_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    state: str = Query(default="fail"),
    rules: str = Query(default="", description="Comma-separated rule ids"),
    q: str = Query(default=""),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """One page of rule-verified entities, filtered in SQL.

    Pagination, search, and rule filters never load the merged item view,
    so the response stays small at any entity count.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)
    rules_list = [r.strip() for r in rules.split(",") if r.strip()]
    blocking = await _blocking_rules_for(db, None, auth)
    if state == "unchecked":
        raise HTTPException(
            status_code=400,
            detail="unchecked entities are not stored per-row; use the summary counts",
        )
    entities, total = await _query_verdict_page(
        db, run_id, state=state, rules=rules_list, q=q,
        page=page, page_size=page_size,
    )
    for entity in entities:
        blocked_by = [
            r.get("rule_id")
            for r in entity["results"]
            if r.get("state") in ("fail", "error") and str(r.get("rule_id")) in blocking
        ]
        entity["blocked_by"] = blocked_by
        entity["upload_ready"] = not blocked_by
    return {
        "run_id": str(run_id),
        "page": page,
        "page_size": page_size,
        "total": total,
        "blocking_rules": sorted(blocking),
        "items": entities,
    }


@router.get("/{run_id}/hmo-studio/items/rule-verify/export")
async def export_rule_verify_results(
    run_id: uuid.UUID,
    format: Literal["json", "csv"] = Query(default="json"),
    scope: Literal["failures", "all"] = Query(default="failures"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Rule results per entity, with the full entity data attached.

    ``scope=failures`` (default) keeps only entities with at least one
    ``fail``; ``all`` includes every checked entity. CSV emits one row per
    non-pass rule result; JSON attaches labels, descriptions, claims and
    authority evidence per entity.
    """
    await _lookup_run_with_access(db, run_id, auth, write=False)
    try:
        items = await fetch_merged_hmo_items_cached(db, run_id)
    except ItemBuildMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    verdict_rows = (
        await db.execute(
            select(
                HmoStudioItemOverride.local_id,
                HmoStudioItemOverride.rule_verdict,
            ).where(HmoStudioItemOverride.run_id == run_id)
        )
    ).all()
    verdicts = {
        str(local_id): verdict
        for local_id, verdict in verdict_rows
        if isinstance(verdict, dict) and verdict.get("results")
    }

    def _entity_rows() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in items:
            local_id = str(item.get("local_id") or "")
            verdict = verdicts.get(local_id)
            if verdict is None:
                continue
            results = [r for r in verdict["results"] if isinstance(r, dict)]
            non_pass = [r for r in results if r.get("state") != "pass"]
            if scope == "failures" and not any(
                r.get("state") == "fail" for r in results
            ):
                continue
            out.append({
                "local_id": local_id,
                "label": item_label(item),
                "class_qid": item.get("class_qid"),
                "entity_type": item.get("entity_type"),
                "source_uri": item.get("source_uri"),
                "wikibase_id": item.get("wikibase_id"),
                "status": item.get("status"),
                "approved": item.get("approved"),
                "overall": verdict.get("overall"),
                "pass_count": sum(1 for r in results if r.get("state") == "pass"),
                "results": non_pass,
                "entity": {
                    "labels": item.get("labels") or {},
                    "descriptions": item.get("descriptions") or {},
                    "aliases": item.get("aliases") or {},
                    "claims": item.get("claims") or [],
                    "control_numbers": item.get("control_numbers") or [],
                    "authority_evidence": item.get("authority_evidence") or [],
                    "skipped_statements": item.get("skipped_statements") or [],
                    "shacl_issues": item.get("shacl_issues") or [],
                },
            })
        return out

    filename = f"run-{run_id}-rule-verify-{scope}.{format}"
    if format == "json":
        return StreamingResponse(
            json_stream({"run_id": str(run_id), "scope": scope, "entities": _entity_rows()}),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _csv_stream():
        buf = io.StringIO()
        fields = [
            "local_id", "label", "class_qid", "entity_type", "wikibase_id",
            "status", "approved", "overall", "rule_id", "state", "field",
            "message",
        ]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in _entity_rows():
            skip = ("rule_id", "state", "field", "message")
            base = {k: row.get(k) for k in fields if k not in skip}
            if not row["results"]:
                writer.writerow({
                    **base, "rule_id": "", "state": "pass", "field": "", "message": "",
                })
            for res in row["results"]:
                writer.writerow({
                    **base,
                    "rule_id": res.get("rule_id"),
                    "state": res.get("state"),
                    "field": res.get("field") or "",
                    "message": str(res.get("message") or "")[:300],
                })
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        _csv_stream(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _blocking_rule_ids(
    payload: RuleVerifyBulkApproveRequest,
    user_settings: dict[str, Any] | None,
) -> set[str]:
    if payload.blocking_rules is not None:
        return {str(x) for x in payload.blocking_rules}
    blocked = (user_settings or {}).get("blocked_rules") or {}
    return {str(k) for k, v in blocked.items() if v}


async def _split_by_blocking_rules(
    db: AsyncSession,
    run_id: uuid.UUID,
    local_ids: list[str],
    blocking: set[str],
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Partition ids into eligible vs excluded by the persisted rule verdicts."""
    rows = (
        await db.execute(
            select(HmoStudioItemOverride).where(
                HmoStudioItemOverride.run_id == run_id,
                HmoStudioItemOverride.local_id.in_(local_ids),
            )
        )
    ).scalars().all()
    verdicts = {r.local_id: (r.rule_verdict or {}) for r in rows}
    eligible: list[str] = []
    excluded: list[dict[str, Any]] = []
    not_checked: list[str] = []
    for local_id in local_ids:
        verdict = verdicts.get(local_id)
        if not verdict:
            not_checked.append(local_id)
            eligible.append(local_id)
            continue
        hits = [
            {
                "rule_id": str(r.get("rule_id")),
                "message": str(r.get("message") or "")[:200],
            }
            for r in (verdict.get("results") or [])
            if isinstance(r, dict)
            and r.get("state") == STATE_FAIL
            and str(r.get("rule_id")) in blocking
        ]
        if hits:
            excluded.append({"local_id": local_id, "blocked_by": hits})
        else:
            eligible.append(local_id)
    return eligible, excluded, not_checked


async def _resolve_bulk_approve_ids(
    db: AsyncSession,
    run_id: uuid.UUID,
    payload: RuleVerifyBulkApproveRequest,
) -> tuple[list[str], list[str]]:
    """The ids in scope: explicit list, or a server-side filter query."""
    if payload.local_ids:
        return [str(x) for x in payload.local_ids], []
    if payload.filters is None:
        raise HTTPException(
            status_code=400,
            detail="Provide local_ids or filters.",
        )
    spec = payload.filters
    ids: list[str] = []
    page = 1
    # Page through the filter server-side up to the bulk cap.
    while len(ids) < MAX_BULK_APPROVE_IDS:
        rows, total = await _query_verdict_page(
            db, run_id, state=spec.state, rules=spec.rules, q=spec.q,
            page=page, page_size=500,
        )
        if not rows:
            break
        ids.extend(str(r["local_id"]) for r in rows)
        if page * 500 >= total:
            break
        page += 1
    return ids[:MAX_BULK_APPROVE_IDS], []


@router.post(
    "/{run_id}/hmo-studio/items/rule-verify/bulk-approve/preview",
    response_model=RuleVerifyBulkApprovePreview,
)
async def preview_rule_verify_bulk_approve(
    run_id: uuid.UUID,
    payload: RuleVerifyBulkApproveRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RuleVerifyBulkApprovePreview:
    await _lookup_run_with_access(db, run_id, auth, write=True)
    blocking = await _blocking_rules_for(db, payload, auth)
    in_scope, _ = await _resolve_bulk_approve_ids(db, run_id, payload)
    eligible, excluded, not_checked = await _split_by_blocking_rules(
        db, run_id, in_scope, blocking,
    )
    return RuleVerifyBulkApprovePreview(
        total=len(in_scope),
        eligible=eligible,
        excluded=excluded[:100],
        not_checked=not_checked[:100],
    )


@router.post("/{run_id}/hmo-studio/items/rule-verify/bulk-approve")
async def rule_verify_bulk_approve(
    run_id: uuid.UUID,
    payload: RuleVerifyBulkApproveRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Bulk approve through the blocking-rule filter, as a background job.

    Delegates to the existing ``hmo_item_bulk_approve`` job so the UI flow
    (job tray, progress, throttled refresh) is identical to bulk approve.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    blocking = await _blocking_rules_for(db, payload, auth)
    in_scope, _ = await _resolve_bulk_approve_ids(db, run_id, payload)
    eligible, _excluded, _not_checked = await _split_by_blocking_rules(
        db, run_id, in_scope, blocking,
    )
    if not eligible:
        return {
            "started": False, "job": None,
            "eligible": 0,
            "excluded": len(in_scope),
            "message": "every item in scope is excluded by a blocking rule",
        }
    from app.pipeline.run_job_params import prepare_job_params
    from app.pipeline.run_job_service import ActiveJobError, create_job

    params = await prepare_job_params(
        db, auth, run_id=run_id, kind="hmo_item_bulk_approve",
        params={"local_ids": eligible, "approved": True},
    )
    try:
        job = await create_job(
            db, project_id=run.project_id, run_id=run_id,
            kind="hmo_item_bulk_approve", params=params, created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"active job already exists: {exc.job_id}",
        ) from exc
    return {"started": True, "eligible": len(eligible), "job_id": str(job.id)}


async def _blocking_rules_for(
    db: AsyncSession,
    payload: RuleVerifyBulkApproveRequest | None,
    auth: AuthContext,
) -> set[str]:
    """Explicit list > saved settings > curated defaults.

    A curator who never touched the settings still gets the safety-critical
    set (SHACL, duplicates, datatypes, MARC grounding, live checks) so
    "passed the blocking rules" is meaningful out of the box. Saving any
    settings (even empty) overrides the defaults — an explicit choice.
    """
    from app.models.user_rule_settings import UserRuleSettings

    if payload is not None and payload.blocking_rules is not None:
        return {str(x) for x in payload.blocking_rules}
    if payload is not None and not payload.blocking_rules_from_settings:
        return set()
    row = await db.get(UserRuleSettings, auth.user.id)
    if row is None:
        return set(DEFAULT_BLOCKING_RULES)
    blocked = row.blocked_rules or {}
    return {str(k) for k, v in blocked.items() if v}
