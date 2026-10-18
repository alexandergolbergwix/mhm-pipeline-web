"""Modal research agent — Pydantic AI + AG-UI over HTTPS.

Deploy target only (Rule W-15 / W-228 / W-229 / W-231). The backend never
imports this file. Tools call Heroku with a short-lived tool grant. User
wiki passwords never enter this container.

Two run modes:

- ``POST /agui`` — legacy request-scoped SSE stream (bounded by Modal's
  web-function timeout; kept for local/dev parity).
- ``POST /agui-async`` — detached run: returns 202 immediately, executes
  the planner in ``run_agui_detached`` (900 s budget) and relays AG-UI
  events to the Heroku webhook, which fans them into a Redis Stream read
  by ``GET /api/research-agent/agui-stream`` (Rule R28). Long turns no
  longer die with "network error".

    cd modal && modal deploy modal_research_agent.py

Set on Heroku:

    heroku config:set RESEARCH_AGENT_ENABLED=true \\
      RESEARCH_AGENT_MODAL_URL=https://<workspace>--mhm-research-agent-web.modal.run

Do not add ``from __future__ import annotations`` here. FastAPI must see
the real Request type on the /agui route (Rule W-229).
"""
import asyncio
import json
import os
import time
from typing import Any

import modal

import pathlib as _pathlib

_HEAD = str(_pathlib.Path(__file__).resolve().parent.parent)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "fastapi>=0.115",
        "httpx>=0.27",
        "pydantic-ai[ag-ui]>=0.7",
        "pydantic>=2.10",
    )
)

if modal.is_local():
    # P0 (2026-10-17): the research image carried NO backend code, so every
    # `from app.…` import (prompt, jev_prefilter, model_fallback) crashed
    # run_agui_detached with ModuleNotFoundError — the surface died before
    # the model call. Mirror modal_jobs: mount the backend tree and make it
    # importable.
    image = (
        image.add_local_dir(
            os.path.join(_HEAD, "backend"),
            remote_path="/root/backend",
            copy=True,
        ).env({"PYTHONPATH": "/root/backend"})
    )

app = modal.App("mhm-research-agent")

# One prompt, one home (P3, 2026-10-17): the Modal planner uses the exact
# text the local surface uses so the safety rails cannot drift apart.
from app.services.research_agent.prompt import SYSTEM_PROMPT as _SYSTEM

PLANNER_RETRIES = 3


def curator_run_error(message: str) -> str:
    text = (message or "").strip() or "The assistant could not finish that answer."
    lowered = text.lower()
    if "maximum output retries" in lowered or "exceeded maximum retries" in lowered:
        return (
            "The assistant could not finish that answer. "
            "Send the question again, or name a manuscript, work, or URI."
        )
    return text


DEFAULT_MODEL = "glm-5.3-flash"
# Qubrid serves the OpenAI Chat Completions wire format only (see
# eval-agent/config/tier1_models.yaml). The bare `openai:` prefix would
# select pydantic-ai's Responses API and Qubrid answers
# "Invalid request sent to the model" — always use OpenAIChatModel here.
QUBRID_BASE_URL = "https://platform.qubrid.com/v1"
SANDBOX_EGRESS_ALLOWLIST = (
    "query.wikidata.org",
    "www.wikidata.org",
)

# Planner context caps: Qubrid rejects oversized requests ("Invalid
# request sent to the model"), usually from an unbounded transcript.
MAX_HISTORY_MESSAGES = 24
MAX_MESSAGE_CHARS = 20_000

WEB_FUNCTION_TIMEOUT_S = 600
DETACHED_FUNCTION_TIMEOUT_S = 900


def _heroku_base() -> str:
    return (os.environ.get("HEROKU_TOOL_BASE_URL") or "").rstrip("/")


def _tool_headers(grant: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {grant}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(grant: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST one tool call to the Heroku tools route with the run's grant."""
    import httpx

    base = _heroku_base()
    if not base:
        return {"error": "HEROKU_TOOL_BASE_URL is not set."}
    url = f"{base}/api/research-agent/tools"
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            url,
            headers=_tool_headers(grant),
            json={"name": name, "arguments": arguments or {}},
        )
        if resp.status_code >= 400:
            return {"error": resp.text, "status": resp.status_code}
        return resp.json()


async def reply_cache_lookup(grant: str, project_id: str, text_hash: str) -> str | None:
    """Cached answer for (project, question hash) — None on miss or error."""
    import httpx

    base = _heroku_base()
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base}/api/research-agent/reply-cache",
                params={"project_id": project_id, "text_hash": text_hash},
                headers={"Authorization": f"Bearer {grant}"},
            )
            if resp.status_code >= 400:
                return None
            answer = resp.json().get("answer")
            return str(answer) if answer else None
    except Exception:
        return None


async def reply_cache_store(
    grant: str, project_id: str, text_hash: str, answer: str
) -> None:
    """Persist a successful answer — best effort, never raises."""
    import httpx

    base = _heroku_base()
    if not base or not answer.strip():
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{base}/api/research-agent/reply-cache",
                headers=_tool_headers(grant),
                json={
                    "project_id": project_id,
                    "text_hash": text_hash,
                    "answer": answer,
                },
            )
    except Exception:
        pass


def _trim_messages(messages: list) -> list:
    trimmed = messages[-MAX_HISTORY_MESSAGES:]
    out = []
    for msg in trimmed:
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str) and len(content) > MAX_MESSAGE_CHARS:
                msg = {**msg, "content": content[:MAX_MESSAGE_CHARS] + " …[trimmed]"}
        out.append(msg)
    return out


def extract_grant(request, body: dict) -> str:
    forwarded = body.get("forwardedProps") or {}
    grant = str(forwarded.get("tool_grant") or "")
    if not grant:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            grant = auth.split(" ", 1)[1].strip()
    return grant


def last_user_text(messages: list) -> str:
    for msg in reversed(messages or []):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "") not in ("user", "human"):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def project_id_from_grant(token: str) -> str:
    """Unverified base64 decode — cache routing only. Heroku
    re-verifies the grant and enforces scope on every call."""
    import base64

    try:
        payload_part = token.split(".", 2)[1]
        payload_part += "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part))
        return str(payload.get("project_id") or "")
    except Exception:
        return ""


def build_agent(grant: str, canvas_digest: str = "", model_config=None):
    """Build the planner with every tool wired to Heroku via the grant.

    ``canvas_digest`` (server-side truth from the thread's artifacts) is
    injected into the system prompt so the planner knows what is on the
    canvas before its first word.
    """
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    async def call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await call_tool(grant, name, arguments or {})

    system_prompt = _SYSTEM
    if canvas_digest:
        system_prompt += (
            "\n\nCanvas context (server-side truth, may be newer than this chat):\n"
            f"{canvas_digest}\n"
            "Refer to artifacts by key. Reuse datasets WITHIN a run; but a dataset "
            "saved in an earlier session may be stale — when a question "
            "depends on live values (claims, coordinates), re-run "
            "wikidata_fetch_items even if 'wikidata-items' already exists. "
            "When the user says 'the map' or 'the chart', "
            "match it against this list first — do not describe artifacts from "
            "memory. For a fuller inventory (dataset columns and row counts), "
            "call canvas_describe."        )

    if model_config is None:
        from app.services.research_agent.model_fallback import (  # noqa: PLC0415
            resolve_model_configs,
        )

        configs = resolve_model_configs()
        model_config = configs[0] if configs else None
    if model_config is not None:
        model = OpenAIChatModel(
            model_config.model,
            provider=OpenAIProvider(
                base_url=model_config.base_url,
                api_key=model_config.api_key,
            ),
        )
    else:
        model = OpenAIChatModel(
            os.environ.get("RESEARCH_AGENT_MODEL") or DEFAULT_MODEL,
            provider=OpenAIProvider(
                base_url=(os.environ.get("QUBRID_BASE_URL") or QUBRID_BASE_URL).rstrip("/"),
                api_key=os.environ.get("QUBRID_API_KEY") or "",
            ),
        )
    agent_kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "retries": PLANNER_RETRIES,
    }
    try:
        agent = Agent(model, output_type=str, **agent_kwargs)
    except TypeError:
        agent = Agent(model, **agent_kwargs)

    @agent.tool_plain
    async def research_summary() -> dict[str, Any]:
        return await call("research_summary")

    @agent.tool_plain
    async def research_cooccurrence() -> dict[str, Any]:
        return await call("research_cooccurrence")

    @agent.tool_plain
    async def research_network() -> dict[str, Any]:
        return await call("research_network")

    @agent.tool_plain
    async def research_ownership() -> dict[str, Any]:
        return await call("research_ownership")

    @agent.tool_plain
    async def research_geography(mode: str | None = None) -> dict[str, Any]:
        return await call("research_geography", {"mode": mode} if mode else {})

    @agent.tool_plain
    async def research_provenance(ms: str, overlay: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"ms": ms}
        if overlay:
            args["overlay"] = overlay
        return await call("research_provenance", args)

    @agent.tool_plain
    async def research_movement_map(cn: str, include_unapproved: bool = False) -> dict[str, Any]:
        return await call("research_movement_map", {"cn": cn, "include_unapproved": include_unapproved})

    @agent.tool_plain
    async def research_manuscripts() -> dict[str, Any]:
        return await call("research_manuscripts")

    @agent.tool_plain
    async def research_shortest_path(from_uri: str, to_uri: str) -> dict[str, Any]:
        return await call("research_shortest_path", {"from": from_uri, "to": to_uri})

    @agent.tool_plain
    async def research_neighbors(uri: str) -> dict[str, Any]:
        return await call("research_neighbors", {"uri": uri})

    @agent.tool_plain
    async def research_sparql(query: str, source: str = "hmo") -> dict[str, Any]:
        return await call("research_sparql", {"query": query, "source": source})

    @agent.tool_plain
    async def research_entity(uri: str) -> dict[str, Any]:
        return await call("research_entity", {"uri": uri})

    @agent.tool_plain
    async def research_evidence(uri: str) -> dict[str, Any]:
        return await call("research_evidence", {"uri": uri})

    @agent.tool_plain
    async def fetch_rdf_ttl(run_id: str | None = None) -> dict[str, Any]:
        return await call("fetch_rdf_ttl", {"run_id": run_id} if run_id else {})

    @agent.tool_plain
    async def canvas_upsert_artifact(
        artifact_key: str,
        kind: str,
        title: str,
        text: str = "",
    ) -> dict[str, Any]:
        return await call(
            "canvas_upsert_artifact",
            {
                "artifact_key": artifact_key,
                "kind": kind,
                "title": title,
                "content": {"text": text},
                "created_by": "agent",
            },
        )

    @agent.tool_plain
    async def canvas_list_versions(artifact_key: str | None = None) -> dict[str, Any]:
        args = {"artifact_key": artifact_key} if artifact_key else {}
        return await call("canvas_list_versions", args)

    @agent.tool_plain
    async def canvas_describe() -> dict[str, Any]:
        """Inventory of the current canvas: artifacts with kind, title, version; datasets with columns + row counts."""
        return await call("canvas_describe", {})

    @agent.tool_plain
    async def create_download_link(artifact_key: str, format: str = "json") -> dict[str, Any]:
        return await call("create_download_link", {"artifact_key": artifact_key, "format": format})

    @agent.tool_plain
    async def wikidata_entity(qid: str) -> dict[str, Any]:
        return await call("wikidata_entity", {"qid": qid})

    @agent.tool_plain
    async def wikibase_entity(qid: str) -> dict[str, Any]:
        return await call("wikibase_entity", {"qid": qid})

    @agent.tool_plain
    async def data_info(artifact_key: str) -> dict[str, Any]:
        """Columns, row count, and a small sample of a saved dataset."""
        return await call("data_info", {"artifact_key": artifact_key})

    @agent.tool_plain
    async def data_select(
        artifact_key: str, column: str, op: str = "eq", value: str | list[str] | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Filter saved dataset rows: op is eq, contains, or in (value: str or list)."""
        args: dict[str, Any] = {"artifact_key": artifact_key, "column": column, "op": op, "limit": limit}
        if value is not None:
            args["value"] = value
        return await call("data_select", args)

    @agent.tool_plain
    async def data_distinct(artifact_key: str, column: str) -> dict[str, Any]:
        """Distinct values of a column with counts (top 25)."""
        return await call("data_distinct", {"artifact_key": artifact_key, "column": column})

    @agent.tool_plain
    async def data_search(artifact_key: str, needle: str, limit: int = 20) -> dict[str, Any]:
        """Rows where any cell contains the substring (case-insensitive)."""
        return await call("data_search", {"artifact_key": artifact_key, "needle": needle, "limit": limit})

    @agent.tool_plain
    async def data_agg(artifact_key: str, column: str, fn: str = "count") -> dict[str, Any]:
        """Aggregate a column: count, min, max, sum, or avg (numeric cells)."""
        return await call("data_agg", {"artifact_key": artifact_key, "column": column, "fn": fn})

    @agent.tool_plain
    async def show_movement_map(cn: str | None = None) -> dict[str, Any]:
        """Place the provenance movement map on the canvas; cn = control number for one manuscript, omit for the corpus."""
        args = {"cn": cn} if cn else {}
        return await call("show_movement_map", args)

    @agent.tool_plain
    async def show_link_types(artifact_key: str = "wikidata-items") -> dict[str, Any]:
        """Aggregate saved claim rows into a link-type chart on the canvas (families of properties with counts)."""
        return await call("show_link_types", {"artifact_key": artifact_key})

    @agent.tool_plain
    async def show_wikidata_places(artifact_key: str = "wikidata-items") -> dict[str, Any]:
        """Plot every place-valued claim on our uploaded items on an interactive canvas map; popups link to the Wikidata entities."""
        return await call("show_wikidata_places", {"artifact_key": artifact_key})

    @agent.tool_plain
    async def wikidata_pack(artifact_key: str = "wikidata-uploads") -> dict[str, Any]:
        """One round-trip: refresh live claims, then place BOTH the link-type chart and the place-mentions map on the canvas."""
        return await call("wikidata_pack", {"artifact_key": artifact_key})

    @agent.tool_plain
    async def export_pdf(artifact_key: str) -> dict[str, Any]:
        """Render a saved artifact as a PDF and return its download_path."""
        return await call("export_pdf", {"artifact_key": artifact_key})

    @agent.tool_plain
    async def wikidata_uploaded_items() -> dict[str, Any]:
        """Save this project's uploaded Wikidata items (from the DB) as 'wikidata-uploads'."""
        return await call("wikidata_uploaded_items", {})

    @agent.tool_plain
    async def wikidata_fetch_items(artifact_key: str = "wikidata-uploads") -> dict[str, Any]:
        """Fetch live claims for the stored QIDs from the Wikidata API into 'wikidata-items'."""
        return await call("wikidata_fetch_items", {"artifact_key": artifact_key})

    return agent


def wrap_cache_capture(response, *, grant: str, question: str, text_hash: str, project_id: str):
    """Re-yield the AG-UI stream; store the answer on success."""
    from fastapi.responses import StreamingResponse

    async def relay():
        chunks: list[str] = []
        finished = False
        error = False
        try:
            async for chunk in response.body_iterator:
                data = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
                for line in data.splitlines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    kind = event.get("type")
                    if kind == "TEXT_MESSAGE_CONTENT":
                        chunks.append(str(event.get("delta") or ""))
                    elif kind == "RUN_FINISHED":
                        finished = True
                    elif kind == "RUN_ERROR":
                        error = True
                yield chunk
        finally:
            if finished and not error and chunks and question and text_hash and project_id:
                await reply_cache_store(grant, project_id, text_hash, "".join(chunks))

    return StreamingResponse(
        relay(),
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
    )


def sse_error(message: str):
    from fastapi.responses import StreamingResponse

    payload = json.dumps({"type": "RUN_ERROR", "message": curator_run_error(message)})

    async def fail():
        yield f"data: {payload}\n\n"

    return StreamingResponse(fail(), media_type="text/event-stream")


async def relay_agui_events_to_webhook(
    grant: str,
    callback_url: str,
    run_id: str,
    response,
    *,
    question: str = "",
    text_hash: str = "",
    project_id: str = "",
) -> str:
    """Consume the planner's AG-UI SSE and POST events to the Heroku
    webhook; capture the answer for the reply cache. Returns "finished",
    "error", or "died"."""
    chunks: list[str] = []
    pending: list[dict[str, Any]] = []
    last_flush = 0.0
    outcome = "died"
    type_counts: dict[str, int] = {}
    try:
        async for chunk in response.body_iterator:
            data = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            for line in data.splitlines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                kind = event.get("type")
                type_counts[str(kind)] = type_counts.get(str(kind), 0) + 1
                if kind == "TEXT_MESSAGE_CONTENT":
                    chunks.append(str(event.get("delta") or ""))
                elif kind == "RUN_FINISHED":
                    outcome = "finished"
                elif kind == "RUN_ERROR":
                    outcome = "error"
                pending.append(event)
            # Flush in batches: one webhook POST per token would trip the
            # event limiter (production incident 2026-09-14 — 429s dropped
            # every event past 120/minute and truncated the answer).
            now = time.monotonic()
            if pending and (len(pending) >= 20 or now - last_flush >= 1.0):
                await post_events(grant, callback_url, run_id, pending)
                pending = []
                last_flush = now
        print(f"[agui-async] relay outcome={outcome} event_types={json.dumps(type_counts, sort_keys=True)}")
        return outcome
    finally:
        if pending:
            try:
                await post_events(grant, callback_url, run_id, pending)
            except Exception:
                pass
        if outcome == "finished" and chunks and question and text_hash and project_id:
            await reply_cache_store(grant, project_id, text_hash, "".join(chunks))


async def post_events(grant: str, callback_url: str, run_id: str, events: list[dict[str, Any]]) -> bool:
    """POST events to the webhook after coalescing adjacent text deltas.

    pydantic-ai emits one TEXT_MESSAGE_CONTENT event per token; merging
    adjacent deltas keeps the webhook far below its rate limit (the
    frontend concatenates deltas anyway, so the stream is identical).
    Retries transient failures — a dropped final batch would lose
    RUN_FINISHED and strand the browser in the watchdog timeout.
    """
    import asyncio

    import httpx

    coalesced: list[dict[str, Any]] = []
    for event in events:
        if (
            event.get("type") == "TEXT_MESSAGE_CONTENT"
            and coalesced
            and coalesced[-1].get("type") == "TEXT_MESSAGE_CONTENT"
        ):
            coalesced[-1]["delta"] = coalesced[-1].get("delta", "") + event.get("delta", "")
        else:
            coalesced.append(event)

    base = _heroku_base()
    if not base or not callback_url:
        return False
    delay = 1.0
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    callback_url,
                    headers=_tool_headers(grant),
                    json={"run_id": run_id, "events": coalesced},
                )
            if resp.status_code < 400:
                return True
            if resp.status_code < 500 and resp.status_code != 429:
                return False  # auth/validation won't heal with retries
        except Exception:
            pass
        await asyncio.sleep(delay)
        delay *= 2
    return False


@app.function(
    image=image,
    cpu=1,
    memory=1024,
    timeout=DETACHED_FUNCTION_TIMEOUT_S,
    secrets=[modal.Secret.from_name("mhm-research-agent")],
)
async def run_agui_detached(body: dict[str, Any]) -> dict[str, Any]:
    """Detached planner run: execute AG-UI and relay events to the webhook.

    Not reachable over HTTP. Spawned by POST /agui-async; outlives the web
    request by design (Modal budget: 900 s instead of one held-open HTTP
    response).
    """
    import httpx
    from starlette.requests import Request as StarletteRequest

    from pydantic_ai.ui.ag_ui import AGUIAdapter

    forwarded = body.get("forwardedProps") or {}
    grant = str(forwarded.get("tool_grant") or "")
    callback_url = str(forwarded.get("callback_url") or "")
    run_id = str(forwarded.get("run_id") or "")
    if not grant or not callback_url or not run_id:
        return {"ok": False, "error": "missing grant/callback/run_id in forwardedProps"}

    payload = json.dumps(body).encode("utf-8")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/agui",
        "raw_path": b"/agui",
        "query_string": b"",
        "root_path": "",
        "server": ("local", 80),
        "client": ("local", 0),
        "headers": [
            (b"content-type", b"application/json"),
            (b"accept", b"text/event-stream"),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    question = last_user_text(body.get("messages") or [])
    import hashlib

    text_hash = (
        hashlib.sha256(" ".join(question.split()).encode("utf-8")).hexdigest()
        if question
        else ""
    )
    project_id = project_id_from_grant(grant)

    # F1 gate (W-273): local scope router + TypeSafe Jev BEFORE the planner.
    # The Modal plane enforces the same gate as the Heroku funnel so neither
    # entry can be called directly to skip it. Import-safe: a packaging slip
    # degrades to the local router instead of killing the run (P0, 2026-10-17).
    try:
        from app.services.research_agent.jev_prefilter import (  # noqa: PLC0415
            JevPreFilter,
            prefilter_verdict_message,
        )

        verdict = JevPreFilter(
            api_key=os.environ.get("TYPESAFE_API_KEY") or ""
        ).check(question, project_id=project_id)
    except Exception as exc:  # noqa: BLE001 — degrade, never die
        print(f"[agui-async] gate unavailable, allowing local-only check: {exc}")
        verdict = None
    if verdict is not None and not verdict.allowed:
        await post_events(grant, callback_url, run_id, [
            {"type": "RUN_ERROR", "message": prefilter_verdict_message(verdict),
             "code": "gate_refused", "retryable": False},
        ])
        return {"ok": True, "outcome": "refused"}

    from app.services.research_agent.model_fallback import (  # noqa: PLC0415
        breaker_open,
        humanize_model_error,
        is_quota_error,
        resolve_model_configs,
        trip_breaker,
    )

    if breaker_open():
        message, code = humanize_model_error("insufficient_quota")
        await post_events(grant, callback_url, run_id, [
            {"type": "RUN_ERROR", "message": message, "code": code, "retryable": False},
        ])
        return {"ok": True, "outcome": "model_unavailable"}
    configs = resolve_model_configs() or [None]
    outcome = "died"
    last_error = ""
    for config in configs:
        agent = build_agent(
            grant,
            canvas_digest=str(forwarded.get("canvas_digest") or ""),
            model_config=config,
        )
        request = StarletteRequest(scope, receive)
        try:
            response = await AGUIAdapter.dispatch_request(request, agent=agent)
            if not hasattr(response, "body_iterator"):
                # Validation 422 (or similar) — relay the real reason so the
                # curator sees why the run refused, not a generic failure.
                body_bytes = getattr(response, "body", b"") or b""
                try:
                    detail = body_bytes.decode("utf-8", "replace")[:600]
                except Exception:  # noqa: BLE001
                    detail = "<unreadable body>"
                print(f"[agui-async] non-stream response {getattr(response, 'status_code', '?')}: {detail}")
                await post_events(grant, callback_url, run_id, [
                    {"type": "RUN_ERROR", "message": f"Planner rejected the run input ({getattr(response, 'status_code', '?')}): {detail}"}
                ])
                return {"ok": False, "error": detail}
            deadline_s = float(os.environ.get("RESEARCH_AGENT_RUN_DEADLINE_S") or 600)
            outcome = await asyncio.wait_for(
                relay_agui_events_to_webhook(
                    grant, callback_url, run_id, response,
                    question=question, text_hash=text_hash, project_id=project_id,
                ),
                timeout=deadline_s,
            )
            return {"ok": True, "outcome": outcome}
        except asyncio.TimeoutError:
            await post_events(grant, callback_url, run_id, [
                {"type": "RUN_ERROR",
                 "message": "The agent run exceeded its total time budget.",
                 "code": "deadline_exceeded", "retryable": True},
            ])
            return {"ok": True, "outcome": "deadline_exceeded"}
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            outcome = "error"
            if is_quota_error(last_error):
                trip_breaker()
            if is_quota_error(last_error) and config is not configs[-1]:
                continue
            break
    message, code = humanize_model_error(last_error)
    await post_events(grant, callback_url, run_id, [
        {"type": "RUN_ERROR", "message": message, "code": code,
         "retryable": code != "model_quota_exhausted"},
    ])
    return {"ok": True, "outcome": outcome}


@app.cls(
    image=image,
    cpu=1,
    memory=1024,
    scaledown_window=300,
    timeout=WEB_FUNCTION_TIMEOUT_S,
    secrets=[modal.Secret.from_name("mhm-research-agent")],
)
class ResearchAgent:
    @modal.enter()
    def setup(self) -> None:
        self.heroku_base = _heroku_base()

    @modal.asgi_app()
    def web(self):
        from fastapi import FastAPI, Request
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse, StreamingResponse

        api = FastAPI(title="MHM Research Agent", docs_url="/docs")
        api.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

        @api.get("/health")
        def health():
            return {"ok": True, "heroku_configured": bool(self.heroku_base)}

        async def _read_json(request: Request) -> dict[str, Any]:
            raw = await request.body()
            if not raw:
                raise ValueError("empty body")
            return json.loads(raw)

        @api.post("/agui")
        async def agui(request: Request):
            try:
                from pydantic_ai import Agent  # noqa: F401
                from pydantic_ai.ui.ag_ui import AGUIAdapter
            except ImportError:
                return JSONResponse(
                    {"detail": "pydantic-ai is not installed in this image."},
                    status_code=501,
                )

            try:
                body = await _read_json(request)
            except (ValueError, json.JSONDecodeError) as exc:
                return JSONResponse({"detail": f"Invalid JSON body: {exc}"}, status_code=400)

            grant = extract_grant(request, body)
            if not grant:
                return JSONResponse({"detail": "tool_grant is required."}, status_code=401)

            messages = _trim_messages(body.get("messages") or [])
            body = {**body, "messages": messages}

            question = last_user_text(messages)
            import hashlib

            text_hash = (
                hashlib.sha256(" ".join(question.split()).encode("utf-8")).hexdigest()
                if question
                else ""
            )
            project_id = project_id_from_grant(grant)

            # F1 gate (W-273) runs BEFORE the reply cache: a refused prompt
            # must never echo a cached answer (R23 caches successes only).
            from app.services.research_agent.jev_prefilter import (  # noqa: PLC0415
                JevPreFilter,
                prefilter_verdict_message,
            )

            verdict = JevPreFilter(
                api_key=os.environ.get("TYPESAFE_API_KEY") or ""
            ).check(question, project_id=project_id)
            if not verdict.allowed:
                refusal = prefilter_verdict_message(verdict)

                def refuse_events():
                    yield "data: " + json.dumps({"type": "RUN_STARTED"}) + "\n\n"
                    yield "data: " + json.dumps({"type": "TEXT_MESSAGE_START", "role": "assistant"}) + "\n\n"
                    yield "data: " + json.dumps({"type": "TEXT_MESSAGE_CONTENT", "delta": refusal}) + "\n\n"
                    yield "data: " + json.dumps({"type": "TEXT_MESSAGE_END"}) + "\n\n"
                    yield "data: " + json.dumps({"type": "RUN_FINISHED"}) + "\n\n"

                return StreamingResponse(refuse_events(), media_type="text/event-stream")

            if self.heroku_base and question and text_hash and project_id:
                cached = await reply_cache_lookup(grant, project_id, text_hash)
                if cached:
                    def cache_events():
                        yield "data: " + json.dumps({"type": "RUN_STARTED"}) + "\n\n"
                        yield "data: " + json.dumps({"type": "TEXT_MESSAGE_START", "role": "assistant"}) + "\n\n"
                        yield "data: " + json.dumps({"type": "TEXT_MESSAGE_CONTENT", "delta": cached}) + "\n\n"
                        yield "data: " + json.dumps({"type": "TEXT_MESSAGE_END"}) + "\n\n"
                        yield "data: " + json.dumps({"type": "RUN_FINISHED"}) + "\n\n"

                    return StreamingResponse(cache_events(), media_type="text/event-stream")

            from app.services.research_agent.model_fallback import (  # noqa: PLC0415
                humanize_model_error,
                is_quota_error,
                resolve_model_configs,
                trip_breaker,
            )

            configs = resolve_model_configs() or [None]
            last_error = ""
            for config in configs:
                agent = build_agent(
                    grant,
                    canvas_digest=str((body.get("forwardedProps") or {}).get("canvas_digest") or ""),
                    model_config=config,
                )
                try:
                    response = await AGUIAdapter.dispatch_request(request, agent=agent)
                    if isinstance(response, StreamingResponse):
                        return wrap_cache_capture(
                            response,
                            grant=grant,
                            question=question,
                            text_hash=text_hash,
                            project_id=project_id,
                        )
                    return response
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    if is_quota_error(last_error):
                        trip_breaker()
                    if is_quota_error(last_error) and config is not configs[-1]:
                        continue
                    break
            message, _code = humanize_model_error(last_error)
            return sse_error(message)

        @api.post("/agui-async")
        async def agui_async(request: Request):
            """Detach: 202 immediately; the planner relays events via webhook."""
            try:
                body = await _read_json(request)
            except (ValueError, json.JSONDecodeError) as exc:
                return JSONResponse({"detail": f"Invalid JSON body: {exc}"}, status_code=400)

            grant = extract_grant(request, body)
            if not grant:
                return JSONResponse({"detail": "tool_grant is required."}, status_code=401)
            forwarded = body.get("forwardedProps") or {}
            if not str(forwarded.get("callback_url") or "") or not str(forwarded.get("run_id") or ""):
                return JSONResponse({"detail": "callback_url and run_id are required."}, status_code=400)

            body = {**body, "messages": _trim_messages(body.get("messages") or [])}
            await run_agui_detached.spawn.aio(body)
            return JSONResponse({"accepted": True}, status_code=202)

        return api
