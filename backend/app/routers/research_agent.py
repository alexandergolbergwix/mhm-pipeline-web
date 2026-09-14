"""Research Assistant: session mint, JWT tools, canvas, local AG-UI stream.

Browser (cookie) mints a tool grant. Modal (or the local stub) calls
``POST /api/research-agent/tools`` with the grant. Wiki passwords stay on
Heroku.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.middleware.rate_limit import limiter
from app.models.project import Membership
from app.models.research_agent import (
    ResearchAgentArtifact,
    ResearchAgentGrant,
    ResearchAgentReplyCache,
    ResearchAgentThread,
)
from app.models.run import Run
from app.routers.wikidata_studio import _unwrap_user_secret
from app.services.research_agent.agui import run_local_agent, sse_pack, tool_catalog
from app.services.research_agent.grants import (
    GRANT_TTL_SECONDS,
    GrantClaims,
    issue_tool_grant,
    unwrap_wiki_token,
    verify_tool_jwt,
    wrap_wiki_token,
)
from app.services.research_agent.tools import (
    artifact_export_bytes,
    build_tool_context,
    dispatch_tool,
    upsert_artifact,
)
from app.settings import get_settings

router = APIRouter(prefix="/research-agent", tags=["research-agent"])
logger = logging.getLogger(__name__)


class SessionRequest(BaseModel):
    run_id: uuid.UUID
    thread_id: uuid.UUID | None = None
    title: str = "Research session"
    # Force a fresh thread even when an earlier thread exists for this run.
    new_thread: bool = False


class ThreadPatchRequest(BaseModel):
    title: str | None = None
    messages: list[Any] | None = None


class SessionResponse(BaseModel):
    thread_id: uuid.UUID
    tool_grant: str
    grant_expires_at: datetime
    agent_url: str
    agent_mode: str
    canvas_state: dict[str, Any]
    messages: list[Any]
    tools: list[dict[str, str]]


class ToolInvokeRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ArtifactPatchRequest(BaseModel):
    title: str | None = None
    kind: str | None = None
    content: dict[str, Any]
    created_by: str = "user"


class AguiRunRequest(BaseModel):
    threadId: str | None = None
    runId: str | None = None
    messages: list[Any] = Field(default_factory=list)
    state: dict[str, Any] | None = None
    forwardedProps: dict[str, Any] | None = None
    tools: list[Any] | None = None
    # RunAgentInput requires `context`; dropping it 422s the detached run
    # (production incident 2026-09-14 — the legacy raw-body path always
    # passed the frontend's context through, so this only surfaced in
    # async mode).
    context: list[Any] = Field(default_factory=list)


async def _require_membership(
    project_id: uuid.UUID,
    auth: AuthContext,
    db: AsyncSession,
) -> Membership:
    row = await db.execute(
        select(Membership).where(
            Membership.project_id == project_id,
            Membership.user_id == auth.user.id,
        )
    )
    membership = row.scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this project.")
    return membership


async def _load_run(db: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    return run


def _agent_target() -> tuple[str, str]:
    settings = get_settings()
    modal_url = (settings.research_agent_modal_url or "").rstrip("/")
    if modal_url:
        return f"{modal_url}/agui", "modal"
    return "/api/research-agent/agui", "local"


def _is_bot_password(token: str | None) -> bool:
    """MediaWiki bot passwords are ``Username@BotName:password``."""
    return bool(token) and "@" in token and ":" in token


async def _wiki_token_for_grant(db: AsyncSession, auth: AuthContext) -> tuple[str | None, str | None]:
    for name in ("wikidata", "wikidata_test"):
        try:
            token = await _unwrap_user_secret(db, auth, name)
        except Exception:
            logger.warning("Wiki key unwrap failed for %s", name)
            token = None
        if _is_bot_password(token):
            return token, name
        if token:
            logger.warning(
                "Saved wiki secret %s is not a Username@BotName:password bot "
                "password; ignoring it for research-agent reads", name,
            )
    # Publication ran against the server-held bot credential (it uploaded
    # the project's items); use the same server-side token for agent reads.
    # It never leaves this backend process (Rule W-228).
    settings = get_settings()
    for name, value in (
        ("server_live", settings.wikidata_publication_live_token),
        ("server_test", settings.wikidata_publication_test_token),
    ):
        if _is_bot_password(value and value.strip()):
            return value.strip(), name
    return None, None


@router.post("/sessions", response_model=SessionResponse)
@limiter.limit("30/minute")
async def create_or_resume_session(
    request: Request,
    body: SessionRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SessionResponse:
    settings = get_settings()
    if not settings.research_agent_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Research agent is disabled.")
    run = await _load_run(db, body.run_id)
    membership = await _require_membership(run.project_id, auth, db)

    thread: ResearchAgentThread | None = None
    if body.thread_id is not None:
        thread = await db.get(ResearchAgentThread, body.thread_id)
        if thread is None or thread.user_id != auth.user.id or thread.project_id != run.project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
    if thread is None and not body.new_thread:
        existing = await db.execute(
            select(ResearchAgentThread).where(
                ResearchAgentThread.user_id == auth.user.id,
                ResearchAgentThread.run_id == run.id,
            ).order_by(ResearchAgentThread.updated_at.desc())
        )
        thread = existing.scalars().first()
    if thread is None:
        thread = ResearchAgentThread(
            project_id=run.project_id,
            run_id=run.id,
            user_id=auth.user.id,
            title=body.title,
            messages=[],
            canvas_state={"artifacts": [], "active_key": None},
        )
        db.add(thread)
        await db.flush()

    wiki_token, wiki_source = await _wiki_token_for_grant(db, auth)
    wrapped = nonce = None
    if wiki_token:
        wrapped, nonce = wrap_wiki_token(wiki_token)
    grant = ResearchAgentGrant(
        thread_id=thread.id,
        user_id=auth.user.id,
        project_id=run.project_id,
        run_id=run.id,
        role=membership.role,
        expires_at=datetime.now(timezone.utc),
        wiki_wrapped=wrapped,
        wiki_nonce=nonce,
        wiki_source=wiki_source,
    )
    db.add(grant)
    await db.flush()
    token, expires = issue_tool_grant(
        grant_id=grant.id,
        thread_id=thread.id,
        user_id=auth.user.id,
        project_id=run.project_id,
        run_id=run.id,
        role=membership.role,
        ttl_seconds=GRANT_TTL_SECONDS,
    )
    grant.expires_at = expires
    await db.commit()
    await db.refresh(thread)
    agent_url, agent_mode = _agent_target()
    return SessionResponse(
        thread_id=thread.id,
        tool_grant=token,
        grant_expires_at=expires,
        agent_url=agent_url,
        agent_mode=agent_mode,
        canvas_state=thread.canvas_state or {},
        messages=list(thread.messages or []),
        tools=tool_catalog(),
    )


@router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    thread = await db.get(ResearchAgentThread, thread_id)
    if thread is None or thread.user_id != auth.user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
    await _require_membership(thread.project_id, auth, db)
    artifacts = (
        await db.execute(
            select(ResearchAgentArtifact).where(
                ResearchAgentArtifact.thread_id == thread.id,
            )
        )
    ).scalars().all()
    latest: dict[str, ResearchAgentArtifact] = {}
    for row in artifacts:
        prev = latest.get(row.artifact_key)
        if prev is None or row.version > prev.version:
            latest[row.artifact_key] = row
    return {
        "id": str(thread.id),
        "project_id": str(thread.project_id),
        "run_id": str(thread.run_id) if thread.run_id else None,
        "title": thread.title,
        "messages": thread.messages,
        "canvas_state": thread.canvas_state,
        "artifacts": [
            {
                "id": str(row.id),
                "artifact_key": row.artifact_key,
                "kind": row.kind,
                "title": row.title,
                "version": row.version,
                "content": row.content,
                "created_by": row.created_by,
            }
            for row in sorted(latest.values(), key=lambda r: r.artifact_key)
        ],
    }


@router.get("/threads")
async def list_threads(
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = Query(default=None),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _require_membership(project_id, auth, db)
    stmt = (
        select(ResearchAgentThread)
        .where(
            ResearchAgentThread.user_id == auth.user.id,
            ResearchAgentThread.project_id == project_id,
        )
        .order_by(ResearchAgentThread.updated_at.desc())
        .limit(50)
    )
    if run_id is not None:
        stmt = stmt.where(ResearchAgentThread.run_id == run_id)
    threads = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(t.id),
            "run_id": str(t.run_id) if t.run_id else None,
            "title": t.title,
            "message_count": len(t.messages or []),
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in threads
    ]


@router.patch("/threads/{thread_id}")
async def patch_thread(
    thread_id: uuid.UUID,
    body: ThreadPatchRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Persist the conversation transcript and/or rename the thread."""
    thread = await db.get(ResearchAgentThread, thread_id)
    if thread is None or thread.user_id != auth.user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
    await _require_membership(thread.project_id, auth, db)
    if body.title is not None:
        thread.title = body.title.strip()[:200] or thread.title
    if body.messages is not None:
        thread.messages = body.messages
    await db.commit()
    return {"id": str(thread.id), "title": thread.title, "message_count": len(thread.messages or [])}


@router.post("/threads/{thread_id}/auto-title")
async def auto_title_thread(
    thread_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Set an AI title from the first user message (Qubrid, fail-closed)."""
    from app.services.research_agent.title import fallback_title, generate_thread_title

    thread = await db.get(ResearchAgentThread, thread_id)
    if thread is None or thread.user_id != auth.user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
    await _require_membership(thread.project_id, auth, db)
    first_user = ""
    for msg in thread.messages or []:
        if isinstance(msg, dict) and str(msg.get("role") or "") in ("user", "human"):
            first_user = str(msg.get("content") or "")
            break
    title = await generate_thread_title(first_user)
    if title is None:
        title = fallback_title(first_user)
    thread.title = title[:200]
    await db.commit()
    return {"id": str(thread.id), "title": thread.title}


@router.put("/threads/{thread_id}/artifacts/{artifact_key}")
async def patch_artifact(
    thread_id: uuid.UUID,
    artifact_key: str,
    body: ArtifactPatchRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    thread = await db.get(ResearchAgentThread, thread_id)
    if thread is None or thread.user_id != auth.user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
    await _require_membership(thread.project_id, auth, db)
    existing = (
        await db.execute(
            select(ResearchAgentArtifact).where(
                ResearchAgentArtifact.thread_id == thread.id,
                ResearchAgentArtifact.artifact_key == artifact_key,
            ).order_by(ResearchAgentArtifact.version.desc())
        )
    ).scalars().first()
    kind = body.kind or (existing.kind if existing else "markdown")
    title = body.title or (existing.title if existing else artifact_key)
    row = await upsert_artifact(
        db, thread,
        artifact_key=artifact_key,
        kind=kind,
        title=title,
        content=body.content,
        created_by=body.created_by or "user",
    )
    await db.commit()
    await db.refresh(row)
    return {
        "id": str(row.id),
        "artifact_key": row.artifact_key,
        "kind": row.kind,
        "title": row.title,
        "version": row.version,
        "content": row.content,
        "canvas_state": thread.canvas_state,
    }


@router.get("/threads/{thread_id}/artifacts/{artifact_key}/download")
async def download_artifact(
    thread_id: uuid.UUID,
    artifact_key: str,
    format: str = Query("json"),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> Response:
    thread = await db.get(ResearchAgentThread, thread_id)
    if thread is None or thread.user_id != auth.user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
    await _require_membership(thread.project_id, auth, db)
    row = (
        await db.execute(
            select(ResearchAgentArtifact).where(
                ResearchAgentArtifact.thread_id == thread.id,
                ResearchAgentArtifact.artifact_key == artifact_key,
            ).order_by(ResearchAgentArtifact.version.desc())
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    body, media_type, filename = artifact_export_bytes(row.kind, row.content or {}, format)
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact_key}-{filename}"'},
    )


@router.get("/threads/{thread_id}/export")
async def export_session(
    thread_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> Response:
    thread = await db.get(ResearchAgentThread, thread_id)
    if thread is None or thread.user_id != auth.user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
    await _require_membership(thread.project_id, auth, db)
    artifacts = (
        await db.execute(
            select(ResearchAgentArtifact).where(ResearchAgentArtifact.thread_id == thread.id)
        )
    ).scalars().all()
    payload = {
        "thread_id": str(thread.id),
        "title": thread.title,
        "messages": thread.messages,
        "canvas_state": thread.canvas_state,
        "artifacts": [
            {
                "artifact_key": r.artifact_key,
                "kind": r.kind,
                "title": r.title,
                "version": r.version,
                "content": r.content,
                "created_by": r.created_by,
            }
            for r in artifacts
        ],
    }
    import json
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="research-session-{thread.id}.json"'},
    )


class ReplyCacheRequest(BaseModel):
    project_id: uuid.UUID
    text_hash: str
    answer: str


class ReplyCacheLookupRequest(BaseModel):
    project_id: uuid.UUID
    text_hash: str


async def _grant_from_request(request: Request, db: AsyncSession) -> tuple[GrantClaims, ResearchAgentGrant, str | None]:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer tool grant required.")
    token = header.split(" ", 1)[1].strip()
    payload = verify_tool_jwt(token)
    claims = GrantClaims.from_payload(payload)
    grant = await db.get(ResearchAgentGrant, claims.grant_id)
    if grant is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown tool grant.")
    if grant.expires_at.replace(tzinfo=grant.expires_at.tzinfo or timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tool grant expired.")
    wiki_token = None
    if grant.wiki_wrapped and grant.wiki_nonce:
        try:
            wiki_token = unwrap_wiki_token(grant.wiki_wrapped, grant.wiki_nonce)
        except Exception:
            logger.warning("Grant wiki token unwrap failed for %s", grant.id)
            wiki_token = None
    return claims, grant, wiki_token


@router.post("/tools")
@limiter.limit("60/minute")
async def invoke_tool(
    body: ToolInvokeRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    claims, _grant, wiki_token = await _grant_from_request(request, db)
    ctx = await build_tool_context(db, claims, wiki_token)
    try:
        result = await dispatch_tool(ctx, body.name, body.arguments)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("research agent tool %s failed: %s", body.name, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"ok": True, "name": body.name, "result": result}


@router.get("/reply-cache")
@limiter.limit("120/minute")
async def lookup_reply_cache(
    request: Request,
    project_id: uuid.UUID,
    text_hash: str = Query(min_length=64, max_length=64),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the cached answer for (project, question hash) — grant-protected."""
    claims, _grant, _wiki = await _grant_from_request(request, db)
    if claims.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Grant is scoped to another project.")
    row = (
        await db.execute(
            select(ResearchAgentReplyCache).where(
                ResearchAgentReplyCache.project_id == project_id,
                ResearchAgentReplyCache.text_hash == text_hash,
            )
        )
    ).scalars().first()
    return {"answer": row.answer_text if row else None}


@router.post("/reply-cache")
@limiter.limit("60/minute")
async def store_reply_cache(
    body: ReplyCacheRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Store a successful planner answer for (project, question hash)."""
    claims, _grant, _wiki = await _grant_from_request(request, db)
    if claims.project_id != body.project_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Grant is scoped to another project.")
    if not body.answer.strip():
        return {"ok": False, "reason": "empty answer"}
    row = (
        await db.execute(
            select(ResearchAgentReplyCache).where(
                ResearchAgentReplyCache.project_id == body.project_id,
                ResearchAgentReplyCache.text_hash == body.text_hash,
            )
        )
    ).scalars().first()
    if row is None:
        row = ResearchAgentReplyCache(
            project_id=body.project_id,
            text_hash=body.text_hash,
            answer_text=body.answer[:10000],
        )
        db.add(row)
    else:
        row.answer_text = body.answer[:10000]
    await db.commit()
    return {"ok": True}


@router.post("/agui")
@limiter.limit("30/minute")
async def local_agui(
    body: AguiRunRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Local/dev AG-UI stream. Production uses Modal ``POST /agui``."""
    settings = get_settings()
    if not settings.research_agent_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Research agent is disabled.")
    forwarded = body.forwardedProps or {}
    token = str(forwarded.get("tool_grant") or "")
    if not token:
        header = request.headers.get("authorization") or ""
        if header.lower().startswith("bearer "):
            token = header.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="tool_grant is required.")
    payload = verify_tool_jwt(token)
    claims = GrantClaims.from_payload(payload)
    grant = await db.get(ResearchAgentGrant, claims.grant_id)
    if grant is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown tool grant.")
    wiki_token = None
    if grant.wiki_wrapped and grant.wiki_nonce:
        wiki_token = unwrap_wiki_token(grant.wiki_wrapped, grant.wiki_nonce)
    ctx = await build_tool_context(db, claims, wiki_token)
    thread_id = body.threadId or str(claims.thread_id)
    run_id = body.runId or str(uuid.uuid4())

    async def event_stream():
        async for event in run_local_agent(
            ctx,
            thread_id=thread_id,
            run_id=run_id,
            messages=body.messages,
            state=body.state,
        ):
            yield sse_pack(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _public_base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}"


async def _canvas_digest(db: AsyncSession, thread_id: uuid.UUID) -> str:
    """One line per canvas artifact (latest version) — the planner's view
    of what is ACTUALLY on the canvas right now, from the DB, not from
    the possibly-stale frontend state."""
    from app.models.research_agent import ResearchAgentArtifact  # noqa: PLC0415
    from sqlalchemy import func  # noqa: PLC0415

    rows = (
        await db.execute(
            select(
                ResearchAgentArtifact.artifact_key,
                ResearchAgentArtifact.kind,
                func.max(ResearchAgentArtifact.version).label("version"),
                func.max(ResearchAgentArtifact.title).label("title"),
            )
            .where(ResearchAgentArtifact.thread_id == thread_id)
            .group_by(
                ResearchAgentArtifact.artifact_key,
                ResearchAgentArtifact.kind,
            )
            .order_by(ResearchAgentArtifact.artifact_key)
            .limit(15)
        )
    ).all()
    if not rows:
        return "The canvas is currently empty."
    lines = [
        f"- {r.artifact_key} ({r.kind}) \"{(r.title or '')[:80]}\" v{r.version}"
        for r in rows
    ]
    return "The canvas currently holds these artifacts (key, kind, title, version):\n" + "\n".join(lines)


async def _grant_for_token(db: AsyncSession, token: str) -> tuple[GrantClaims, Any]:
    claims = GrantClaims.from_payload(verify_tool_jwt(token))
    grant = await db.get(ResearchAgentGrant, claims.grant_id)
    if grant is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown tool grant.")
    return claims, grant


@router.post("/agui-async")
@limiter.limit("30/minute")
async def start_async_agui(
    body: AguiRunRequest,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Start a detached Modal planner run; the browser then reads
    ``/agui-stream`` (Redis-Stream relay). Long turns survive Heroku's
    router idle timeout and Modal's HTTP timeout (Rule R28)."""
    from app.services.research_agent import async_run  # noqa: PLC0415

    settings = get_settings()
    if not settings.research_agent_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Research agent is disabled.")
    modal_url = (settings.research_agent_modal_url or "").rstrip("/")
    if not modal_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Async agent runs need Modal.")

    forwarded = body.forwardedProps or {}
    token = str(forwarded.get("tool_grant") or "")
    if not token:
        header = request.headers.get("authorization") or ""
        if header.lower().startswith("bearer "):
            token = header.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="tool_grant is required.")
    claims, grant = await _grant_for_token(db, token)
    thread = await db.get(ResearchAgentThread, claims.thread_id)
    if thread is None or thread.project_id != claims.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
    await _require_membership(claims.project_id, auth, db)

    run_id = str(body.runId or uuid.uuid4())
    await async_run.register_run(claims.thread_id, run_id)

    # Strict AG-UI message shape: RunAgentInput validation (422) rejects
    # anything but {id, role, content-string} dicts. Coerce here so the
    # planner never refuses an odd transcript shape.
    import json  # noqa: PLC0415

    safe_messages: list[dict[str, str]] = []
    for index, msg in enumerate(body.messages or []):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role not in ("user", "assistant", "system"):
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str) if content is not None else ""
        safe_messages.append({
            "id": str(msg.get("id") or f"m-{index}")[:200],
            "role": role,
            "content": content[:20_000],
        })

    dispatch_body = dict(body.model_dump())
    dispatch_body["runId"] = run_id
    dispatch_body["messages"] = safe_messages
    merged_forwarded = dict(forwarded)
    merged_forwarded["callback_url"] = (
        f"{_public_base_url(request)}/api/research-agent/agui-events"
    )
    merged_forwarded["run_id"] = run_id
    merged_forwarded["canvas_digest"] = await _canvas_digest(db, claims.thread_id)
    dispatch_body["forwardedProps"] = merged_forwarded

    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{modal_url}/agui-async",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=dispatch_body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Modal dispatch failed: {exc}") from exc
    if resp.status_code != 202:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Modal dispatch rejected: {resp.text[:300]}")
    return {
        "run_id": run_id,
        "stream_url": f"/api/research-agent/agui-stream?run_id={run_id}",
        "agent_mode": "modal-async",
    }


class AguiEventsRequest(BaseModel):
    run_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/agui-events")
@limiter.limit("600/minute")
async def agui_events(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Modal webhook: append AG-UI events for one detached run.

    Authenticated with the run's tool grant (same trust surface as
    ``/tools``); the run id must belong to the grant's thread.
    """
    from app.services.research_agent import async_run  # noqa: PLC0415

    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer tool grant is required.")
    token = header.split(" ", 1)[1].strip()
    claims, _grant = await _grant_for_token(db, token)

    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body.") from exc
    body = AguiEventsRequest.model_validate(payload)
    bound = await async_run.run_thread_id(body.run_id)
    if bound is None or bound != str(claims.thread_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown run for this grant.")
    stored = await async_run.append_events(claims.thread_id, body.run_id, body.events)
    return {"stored": stored}


@router.get("/agui-stream")
@limiter.limit("30/minute")
async def agui_stream(
    request: Request,
    run_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """SSE bridge over the run's Redis Stream, with heartbeats."""
    from app.services.research_agent import async_run  # noqa: PLC0415

    bound = await async_run.run_thread_id(run_id)
    if bound is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown run.")
    thread = await db.get(ResearchAgentThread, uuid.UUID(bound))
    if thread is None or thread.user_id != auth.user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown run.")
    last_id = request.query_params.get("last_id") or "0"

    async def event_stream():
        async for event in async_run.iter_stream(bound, run_id, last_id=last_id):
            if event is None:
                yield ": ping\n\n"
                continue
            yield sse_pack(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
