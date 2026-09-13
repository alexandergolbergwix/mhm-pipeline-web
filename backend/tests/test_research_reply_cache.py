"""Reply cache endpoints — grant-protected planner answer reuse."""
from __future__ import annotations

import hashlib

import pytest


pytestmark = pytest.mark.asyncio


def _hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


async def _mint_grant(sample_run) -> dict:
    resp = await sample_run["client"].post(
        "/api/research-agent/sessions",
        json={"run_id": str(sample_run["run_id"])},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_store_then_lookup(sample_run):
    session = await _mint_grant(sample_run)
    headers = {"Authorization": f"Bearer {session['tool_grant']}"}
    pid = sample_run["project_id"]
    h = _hash("Which manuscripts mention Scholem?")
    stored = await sample_run["client"].post(
        "/api/research-agent/reply-cache",
        headers=headers,
        json={"project_id": str(pid), "text_hash": h, "answer": "Three manuscripts."},
    )
    assert stored.status_code == 200, stored.text
    got = await sample_run["client"].get(
        "/api/research-agent/reply-cache",
        params={"project_id": str(pid), "text_hash": h},
        headers=headers,
    )
    assert got.status_code == 200
    assert got.json()["answer"] == "Three manuscripts."


async def test_lookup_miss_returns_none(sample_run):
    session = await _mint_grant(sample_run)
    headers = {"Authorization": f"Bearer {session['tool_grant']}"}
    got = await sample_run["client"].get(
        "/api/research-agent/reply-cache",
        params={"project_id": str(sample_run["project_id"]), "text_hash": "0" * 64},
        headers=headers,
    )
    assert got.status_code == 200
    assert got.json()["answer"] is None


async def test_lookup_rejects_foreign_project(sample_run):
    import uuid as uuid_mod

    session = await _mint_grant(sample_run)
    headers = {"Authorization": f"Bearer {session['tool_grant']}"}
    got = await sample_run["client"].get(
        "/api/research-agent/reply-cache",
        params={"project_id": str(uuid_mod.uuid4()), "text_hash": "0" * 64},
        headers=headers,
    )
    assert got.status_code == 403


async def test_store_without_grant_is_401(sample_run):
    resp = await sample_run["client"].post(
        "/api/research-agent/reply-cache",
        json={
            "project_id": str(sample_run["project_id"]),
            "text_hash": "0" * 64,
            "answer": "x",
        },
    )
    assert resp.status_code == 401


async def test_store_empty_answer_is_rejected(sample_run):
    session = await _mint_grant(sample_run)
    headers = {"Authorization": f"Bearer {session['tool_grant']}"}
    resp = await sample_run["client"].post(
        "/api/research-agent/reply-cache",
        headers=headers,
        json={
            "project_id": str(sample_run["project_id"]),
            "text_hash": _hash("q"),
            "answer": "   ",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "empty answer"}


async def test_reply_text_hash_normalizes_whitespace():
    from app.services.research_agent.grants import reply_text_hash

    assert reply_text_hash("a  b\nc") == reply_text_hash("a b c")
    assert reply_text_hash("a") != reply_text_hash("b")
