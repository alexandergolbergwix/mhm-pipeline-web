"""AG-UI SSE helpers and a local keyword agent for tests / Modal-less dev.

Production streams from Modal. When RESEARCH_AGENT_MODAL_URL is empty, the
Heroku stub still executes the same tools so Playwright and local curators
can exercise chat + canvas without an LLM key.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.services.research_agent.prompt import SYSTEM_PROMPT
from app.services.research_agent.sanitize import output_rail
from app.services.research_agent.scope import REFUSAL_MESSAGE, classify_scope, consecutive_rejects
from app.services.research_agent.tools import ToolContext, dispatch_tool, upsert_artifact

_QID_RE = re.compile(r"\bQ\d+\b", re.IGNORECASE)


def sse_pack(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def last_user_text(messages: list[Any]) -> str:
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in ("user", "human"):
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                    parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    parts.append(part)
            text = "".join(parts).strip()
            if text:
                return text
    return ""


def route_user_text(text: str) -> tuple[str, dict[str, Any]]:
    """Map a curator utterance onto one research tool. Deterministic, no LLM."""
    lower = text.lower()
    qid_match = _QID_RE.search(text)
    if "sparql" in lower or lower.startswith("select ") or lower.startswith("prefix "):
        source = "wikidata" if "wikidata" in lower else "wikibase" if "wikibase" in lower else "hmo"
        if "select" not in lower and "construct" not in lower:
            return "research_sparql", {"template_id": "sample_triples", "source": source, "limit": 25}
        return "research_sparql", {"query": text, "source": source, "limit": 25}
    if any(word in lower for word in ("co-occur", "cooccur", "cluster", "works together")):
        return "research_cooccurrence", {}
    if "network" in lower or "scribe" in lower or "people" in lower:
        return "research_network", {}
    if "ownership" in lower or "owner chain" in lower:
        return "research_ownership", {}
    if "movement" in lower or "provenance map" in lower or "map" in lower:
        return "research_manuscripts", {}
    if "geography" in lower or "heatmap" in lower or "places" in lower:
        return "research_geography", {}
    if "shortest" in lower or "path" in lower:
        return "research_shortest_path", {
            "from": _first_uri(text, 0),
            "to": _first_uri(text, 1),
        }
    if "neighbor" in lower:
        return "research_neighbors", {"uri": _first_uri(text, 0)}
    if "evidence" in lower:
        return "research_evidence", {"uri": _first_uri(text, 0)}
    if "entity" in lower or "uri" in lower:
        return "research_entity", {"uri": _first_uri(text, 0)}
    if "turtle" in lower or "ttl" in lower or "rdf file" in lower:
        return "fetch_rdf_ttl", {}
    if "wikibase" in lower and qid_match:
        return "wikibase_entity", {"qid": qid_match.group(0).upper()}
    if ("wikidata" in lower or "qid" in lower) and qid_match:
        return "wikidata_entity", {"qid": qid_match.group(0).upper()}
    if qid_match:
        return "wikidata_entity", {"qid": qid_match.group(0).upper()}
    return "research_summary", {}


def _first_uri(text: str, index: int) -> str:
    uris = re.findall(r"https?://[^\s]+|urn:[^\s]+", text)
    if len(uris) > index:
        return uris[index].rstrip(".,;)")
    return ""


def _artifact_for_tool(name: str, result: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    key = name.replace("_", "-")
    if name == "research_summary":
        text = (
            f"# Corpus overview\n\n"
            f"- Manuscripts: {result.get('total_manuscripts', result.get('result', {}).get('total_manuscripts', '—'))}\n"
            f"- Works: {result.get('total_works', '—')}\n"
            f"- Persons: {result.get('total_persons', '—')}\n"
            f"- Places: {result.get('total_places', '—')}\n"
            f"- Triples: {result.get('triples', '—')}\n"
        )
        return key, "markdown", "Corpus overview", {"text": text, "source": result}
    if name == "research_sparql":
        return key, "sparql", "SPARQL results", result
    if name == "research_movement_map":
        return key, "map", "Movement map", result
    if name in ("research_cooccurrence", "research_network", "research_ownership", "research_geography"):
        return key, "json", name.replace("_", " ").title(), result if isinstance(result, dict) else {"result": result}
    if name == "research_manuscripts":
        items = result if isinstance(result, list) else result.get("items") or result.get("result") or []
        return "movement-map", "map", "Movement map", {"manuscripts": items}
    if name in ("wikidata_entity", "wikibase_entity"):
        qid = result.get("id") or "entity"
        labels = result.get("labels") or {}
        title = labels.get("en") or labels.get("he") or str(qid)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return f"wiki-{qid}", "json", str(title), result if isinstance(result, dict) else {"text": text}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return key, "json", name.replace("_", " ").title(), result if isinstance(result, dict) else {"text": text}


async def run_local_agent(
    ctx: ToolContext,
    *,
    thread_id: str,
    run_id: str,
    messages: list[Any],
    state: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Execute one tool from the last user message and stream AG-UI events."""
    del state  # canvas truth lives on the thread row
    text = last_user_text(messages)
    yield {"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id}
    if not text:
        msg_id = str(uuid.uuid4())
        yield {"type": "TEXT_MESSAGE_START", "messageId": msg_id, "role": "assistant"}
        yield {
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": msg_id,
            "delta": "Ask about the corpus, or use a canvas action.",
        }
        yield {"type": "TEXT_MESSAGE_END", "messageId": msg_id}
        yield {"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id}
        return

    verdict = classify_scope(text)
    if verdict.decision == "reject" or consecutive_rejects(messages) >= 3:
        msg_id = str(uuid.uuid4())
        refusal = output_rail(REFUSAL_MESSAGE, system_prompt=SYSTEM_PROMPT)
        yield {"type": "TEXT_MESSAGE_START", "messageId": msg_id, "role": "assistant"}
        yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": msg_id, "delta": refusal}
        yield {"type": "TEXT_MESSAGE_END", "messageId": msg_id}
        yield {"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id}
        return

    tool_name, tool_args = route_user_text(text)
    call_id = str(uuid.uuid4())
    yield {
        "type": "TOOL_CALL_START",
        "toolCallId": call_id,
        "toolCallName": tool_name,
    }
    yield {
        "type": "TOOL_CALL_ARGS",
        "toolCallId": call_id,
        "delta": json.dumps(tool_args, ensure_ascii=False),
    }
    try:
        result = await dispatch_tool(ctx, tool_name, tool_args)
        yield {"type": "TOOL_CALL_END", "toolCallId": call_id, "result": result}
        key, kind, title, content = _artifact_for_tool(tool_name, result if isinstance(result, dict) else {"result": result})
        row = await upsert_artifact(
            ctx.db, ctx.thread,
            artifact_key=key, kind=kind, title=title, content=content, created_by="agent",
        )
        await ctx.db.commit()
        snapshot = dict(ctx.thread.canvas_state or {})
        snapshot["active_key"] = row.artifact_key
        yield {"type": "STATE_SNAPSHOT", "snapshot": snapshot}
        msg_id = str(uuid.uuid4())
        summary = output_rail(
            f"I ran `{tool_name}` and placed **{title}** on the canvas "
            f"(version {row.version}). You can edit the text there and download it.",
            system_prompt=SYSTEM_PROMPT,
        )
        yield {"type": "TEXT_MESSAGE_START", "messageId": msg_id, "role": "assistant"}
        yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": msg_id, "delta": summary}
        yield {"type": "TEXT_MESSAGE_END", "messageId": msg_id}
    except Exception as exc:
        await ctx.db.rollback()
        yield {
            "type": "TOOL_CALL_END",
            "toolCallId": call_id,
            "result": {"error": str(exc)},
        }
        msg_id = str(uuid.uuid4())
        yield {"type": "TEXT_MESSAGE_START", "messageId": msg_id, "role": "assistant"}
        yield {
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": msg_id,
            "delta": f"The tool `{tool_name}` failed: {exc}",
        }
        yield {"type": "TEXT_MESSAGE_END", "messageId": msg_id}
    yield {"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id}


def tool_catalog() -> list[dict[str, str]]:
    from app.services.research_agent.tools import TOOL_NAMES
    return [{"name": name, "description": name.replace("_", " ")} for name in TOOL_NAMES]


def system_prompt() -> str:
    return SYSTEM_PROMPT
