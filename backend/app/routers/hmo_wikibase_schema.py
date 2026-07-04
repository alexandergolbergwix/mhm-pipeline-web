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

Live writes use server-held OAuth (Heroku config), not per-user keys.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session, session_scope
from app.models.run_job import JOB_KIND_HMO_SCHEMA_BOOTSTRAP
from app.pipeline import hmo_schema_actions, hmo_schema_bootstrap as pipeline
from app.pipeline.agent_runner import (
    list_verify_sessions,
    new_session_id,
    read_verify_session,
    sse_stream,
)
from app.pipeline.hmo_schema_verify import (
    HMO_SCHEMA_VERIFY_CHANNEL,
    filter_schema_entries,
    hmo_schema_verify_event_stream,
    schema_verdict_query_summary,
)
from app.pipeline.inference_cache import read_from_inference_cache
from app.pipeline.run_job_params import prepare_job_params
from app.pipeline.run_job_service import ActiveJobError, create_job, serialise_job
from app.routers.runs import _lookup_run_with_access
from app.routers.wikidata_studio import _resolve_gemini_key
from app.services.wikibase_credentials import verify_server_wikibase_auth
from app.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hmo-wikibase-schema", tags=["hmo-wikibase-schema"])


class SchemaStatusResponse(BaseModel):
    total_classes: int
    total_properties: int
    mapped_classes: int
    mapped_properties: int
    missing_sample: list[str]
    wikibase_configured: bool
    wikibase_base_url: str
    wikibase_write_user: str


class WikibaseVerifyResponse(BaseModel):
    ok: bool
    message: str
    base_url: str | None = None
    api_username: str | None = None
    write_user: str | None = None


class SchemaBootstrapEntryDto(BaseModel):
    ontology_uri: str
    entity_kind: str
    label: str
    wikibase_id: str | None
    status: str
    message: str = ""
    description: str = ""
    datatype: str | None = None


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
                    "writing. Set False for live; live requires server "
                    "Wikibase Cloud OAuth to be configured.",
    )
    run_id: uuid.UUID | None = Field(
        default=None,
        description="Required when dry_run=False. The schema is global, "
                    "not tied to any run — this only anchors the background "
                    "job (which needs a run_id for its progress-polling row) "
                    "to whichever run's page the curator was on.",
    )


class SchemaVerifyStartRequest(BaseModel):
    run_id: uuid.UUID
    action_id: str = Field(..., min_length=1, max_length=64)
    ontology_uris: list[str] | None = None
    override_cache: bool = False
    tier_model: str | None = Field(default=None, max_length=64)


def _bootstrap_response(result: pipeline.SchemaBootstrapResult) -> SchemaBootstrapResponse:
    return SchemaBootstrapResponse(
        dry_run=result.dry_run,
        created=result.created,
        skipped=result.skipped,
        failed=result.failed,
        would_create=result.would_create,
        entries=[SchemaBootstrapEntryDto(**entry.__dict__) for entry in result.entries],
    )


@router.get("/bootstrap/last-report", response_model=SchemaBootstrapResponse)
async def get_last_bootstrap_report(
    auth: AuthContext = Depends(current_auth),  # noqa: ARG001
    db: AsyncSession = Depends(get_session),
) -> SchemaBootstrapResponse:
    """Return the latest bootstrap report (job result or on-disk cache)."""
    report = await pipeline.load_last_bootstrap_report(db)
    if report is None:
        report = await pipeline.bootstrap_schema(db, writer=None, dry_run=True)
        pipeline.cache_schema_bootstrap_report(report)
    return _bootstrap_response(report)


@router.get("/ai-verify/actions")
async def list_schema_verify_actions(
    scope_kind: str = Query("selection", pattern=r"^(single|selection|all)$"),
    auth: AuthContext = Depends(current_auth),  # noqa: ARG001
) -> list[dict[str, Any]]:
    return [
        hmo_schema_actions.to_dict(a)
        for a in hmo_schema_actions.list_actions(scope_kind=scope_kind)  # type: ignore[arg-type]
    ]


@router.get("/ai-verify/cached-verdicts")
async def get_cached_schema_verdicts(
    tier_model: str | None = Query(default=None),
    auth: AuthContext = Depends(current_auth),  # noqa: ARG001
    db: AsyncSession = Depends(get_session),
) -> dict[str, dict[str, Any]]:
    """Verdicts persisted from a previous AI-verify run, keyed by local id.

    Lets the schema panel show verdict pills on page load without
    re-running verification — the SSE stream's verdicts otherwise only
    ever live in the open modal's React state (Rule W-17 style
    persistence, but for the global schema, not per-run entities).
    """
    from app.pipeline.ai_verifier import GEMINI_MODEL  # noqa: PLC0415

    report = await pipeline.load_last_bootstrap_report(db)
    if report is None:
        return {}
    judge_model = tier_model or GEMINI_MODEL
    evaluator_id = "hmo_wikibase_schema"
    items = filter_schema_entries(report, ontology_uris=None)
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        hit = await read_from_inference_cache(
            db,
            kind="ai_verdict",
            query_summary=schema_verdict_query_summary(item, judge_model, evaluator=evaluator_id),
        )
        if hit is None:
            continue
        local_id = str(item.get("_local_id") or "")
        if not local_id:
            continue
        verdict = hit.get("verdict") or {} if isinstance(hit, dict) else {}
        out[local_id] = {
            "overall": verdict.get("overall") or "unknown",
            "name_ok": None,
            "type_ok": None,
            "role_ok": None,
            "reasoning": verdict.get("reasoning"),
            "model": hit.get("judge_id") if isinstance(hit, dict) else None,
            "judged_at": hit.get("judged_at") if isinstance(hit, dict) else None,
            "cache_key": hit.get("cache_key") if isinstance(hit, dict) else None,
            "evaluator": (hit.get("evaluator") if isinstance(hit, dict) else None) or evaluator_id,
        }
    return out


@router.post("/ai-verify/start-stream")
async def start_schema_verify_stream(
    payload: SchemaVerifyStartRequest,
    auth: AuthContext = Depends(current_auth),
) -> StreamingResponse:
    """Kick off one AI verification session and stream events via SSE.

    Deliberately does NOT take ``db: AsyncSession = Depends(get_session)``:
    FastAPI only closes yield-dependencies once the whole response — for a
    ``StreamingResponse``, that means once the generator below is fully
    exhausted — has been sent. A verification session can run for many
    minutes (or hang forever if the eval-agent subprocess call never
    returns), so holding the request-scoped session open for that whole
    window pins one Postgres connection per in-flight session and, if the
    generator hangs, leaks it for good (see 2026-07-04 outage — a handful
    of hung sessions exhausted the whole ``pool_size=5 + max_overflow=10``
    budget and turned unrelated requests like ``/auth/login`` into 503s).
    All of the setup below runs inside its own short-lived
    ``session_scope()`` that commits and returns its connection to the
    pool before the streaming response is even constructed.
    """
    async with session_scope() as db:
        await _lookup_run_with_access(db, payload.run_id, auth, write=False)

        action = hmo_schema_actions.get_action(payload.action_id)
        if action is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown action_id {payload.action_id!r}",
            )

        report = await pipeline.load_last_bootstrap_report(db)
        if report is None:
            report = await pipeline.bootstrap_schema(db, writer=None, dry_run=True)
            pipeline.cache_schema_bootstrap_report(report)

        items = filter_schema_entries(report, ontology_uris=payload.ontology_uris)
        if not items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no schema entries in scope — run a dry-run bootstrap first",
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
        evaluator_id = action.evaluators[0] if action.evaluators else "hmo_wikibase_schema"
        pre_cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
        uncached: list[dict[str, Any]] = []
        if not payload.override_cache:
            for item in items:
                hit = await read_from_inference_cache(
                    db,
                    kind="ai_verdict",
                    query_summary=schema_verdict_query_summary(
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
                    "No Gemini API key configured. Open Settings → Credentials "
                    "and add one from https://aistudio.google.com/app/apikey."
                ),
            )

    session_id = new_session_id()
    return StreamingResponse(
        sse_stream(hmo_schema_verify_event_stream(
            run_id=str(payload.run_id),
            session_id=session_id,
            action=action,
            items=items,
            uncached_items=uncached,
            pre_cached=pre_cached,
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


@router.get("/ai-verify/sessions")
async def list_schema_verify_sessions(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    return list_verify_sessions(HMO_SCHEMA_VERIFY_CHANNEL, str(run_id))


@router.get("/ai-verify/sessions/{session_id}")
async def get_schema_verify_session(
    run_id: uuid.UUID,
    session_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    data = read_verify_session(HMO_SCHEMA_VERIFY_CHANNEL, str(run_id), session_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        )
    return data


@router.get("/status", response_model=SchemaStatusResponse)
async def get_schema_status(
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SchemaStatusResponse:
    """Ontology class/property counts vs. how many already have a live mapping."""
    settings = get_settings()
    result = await pipeline.schema_status(db)
    return SchemaStatusResponse(
        total_classes=result.total_classes,
        total_properties=result.total_properties,
        mapped_classes=result.mapped_classes,
        mapped_properties=result.mapped_properties,
        missing_sample=result.missing_sample,
        wikibase_configured=settings.wikibase_cloud_configured,
        wikibase_base_url=settings.wikibase_cloud_base_url.strip()
        or "https://mhm-hmo.wikibase.cloud",
        wikibase_write_user=settings.wikibase_cloud_write_user.strip()
        or "mhm-pipeline-web",
    )


@router.get("/verify", response_model=WikibaseVerifyResponse)
async def verify_wikibase_connection(
    auth: AuthContext = Depends(current_auth),  # noqa: ARG001 — gate
) -> WikibaseVerifyResponse:
    """Live smoke test: OAuth session, CSRF, and wiki username match."""
    result = await verify_server_wikibase_auth()
    return WikibaseVerifyResponse(
        ok=result.ok,
        message=result.message,
        base_url=result.base_url,
        api_username=result.api_username,
        write_user=result.write_user,
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
    project's WebSocket room) for progress. Requires server Wikibase
    Cloud OAuth and a ``run_id`` to anchor the job row to (the schema
    itself is global).
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
