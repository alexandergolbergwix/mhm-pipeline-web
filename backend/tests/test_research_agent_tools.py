"""Research Assistant session, tool JWT, canvas edit, download."""
from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_session_mints_grant_and_local_agent(sample_run, auth_user):
    _user, client = auth_user
    run_id = sample_run["run_id"]
    resp = await client.post("/api/research-agent/sessions", json={"run_id": str(run_id)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_mode"] == "local"
    assert body["agent_url"] == "/api/research-agent/agui"
    assert body["tool_grant"]
    assert body["thread_id"]
    token = body["tool_grant"]

    tool = await client.post(
        "/api/research-agent/tools",
        json={"name": "research_summary", "arguments": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Sample run has no RDF, so summary 404s — grant auth still succeeded if not 401.
    assert tool.status_code != 401
    assert tool.status_code in (200, 400, 404)


@pytest.mark.asyncio
async def test_tool_without_bearer_is_401(sample_run, auth_user):
    _user, client = auth_user
    resp = await client.post(
        "/api/research-agent/tools",
        json={"name": "research_summary", "arguments": {}},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_tool_is_400(sample_run, auth_user):
    _user, client = auth_user
    minted = await client.post(
        "/api/research-agent/sessions", json={"run_id": str(sample_run["run_id"])},
    )
    token = minted.json()["tool_grant"]
    resp = await client.post(
        "/api/research-agent/tools",
        json={"name": "drop_database", "arguments": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "Unknown tool" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_canvas_edit_and_download(sample_run, auth_user):
    _user, client = auth_user
    minted = await client.post(
        "/api/research-agent/sessions", json={"run_id": str(sample_run["run_id"])},
    )
    thread_id = minted.json()["thread_id"]
    patched = await client.put(
        f"/api/research-agent/threads/{thread_id}/artifacts/notes",
        json={
            "title": "Notes",
            "kind": "markdown",
            "content": {"text": "Edited on the canvas."},
            "created_by": "user",
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == 1
    assert patched.json()["content"]["text"] == "Edited on the canvas."

    download = await client.get(
        f"/api/research-agent/threads/{thread_id}/artifacts/notes/download?format=md",
    )
    assert download.status_code == 200
    assert b"Edited on the canvas." in download.content
    assert "attachment" in download.headers.get("content-disposition", "")

    bundle = await client.get(f"/api/research-agent/threads/{thread_id}/export")
    assert bundle.status_code == 200
    payload = json.loads(bundle.content)
    assert payload["thread_id"] == thread_id
    assert payload["artifacts"][0]["content"]["text"] == "Edited on the canvas."


@pytest.mark.asyncio
async def test_local_agui_refuses_off_topic(sample_run, auth_user):
    _user, client = auth_user
    minted = await client.post(
        "/api/research-agent/sessions", json={"run_id": str(sample_run["run_id"])},
    )
    token = minted.json()["tool_grant"]
    thread_id = minted.json()["thread_id"]
    resp = await client.post(
        "/api/research-agent/agui",
        json={
            "threadId": thread_id,
            "runId": "run-off",
            "messages": [{"role": "user", "content": "Write a poem about cats and a dinner recipe"}],
            "forwardedProps": {"tool_grant": token},
        },
    )
    assert resp.status_code == 200, resp.text
    assert "only helps with this project's Hebrew manuscript corpus" in resp.text
    assert "TOOL_CALL_START" not in resp.text


@pytest.mark.asyncio
async def test_local_agui_streams_run_finished(sample_run, auth_user):
    _user, client = auth_user
    minted = await client.post(
        "/api/research-agent/sessions", json={"run_id": str(sample_run["run_id"])},
    )
    token = minted.json()["tool_grant"]
    thread_id = minted.json()["thread_id"]
    resp = await client.post(
        "/api/research-agent/agui",
        json={
            "threadId": thread_id,
            "runId": "run-1",
            "messages": [{"role": "user", "content": "Give me a corpus overview"}],
            "forwardedProps": {"tool_grant": token},
        },
    )
    assert resp.status_code == 200, resp.text
    text = resp.text
    assert "RUN_STARTED" in text
    assert "RUN_FINISHED" in text
    assert "TEXT_MESSAGE" in text


@pytest.mark.asyncio
async def test_sparql_write_is_blocked_via_tool(sample_run, auth_user):
    _user, client = auth_user
    minted = await client.post(
        "/api/research-agent/sessions", json={"run_id": str(sample_run["run_id"])},
    )
    token = minted.json()["tool_grant"]
    resp = await client.post(
        "/api/research-agent/tools",
        json={
            "name": "research_sparql",
            "arguments": {"query": "DROP GRAPH <http://example.org>", "source": "hmo"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (400, 422)
    detail = str(resp.json().get("detail") or resp.text)
    assert "DROP" in detail or "not permitted" in detail.lower() or "Only SELECT" in detail
