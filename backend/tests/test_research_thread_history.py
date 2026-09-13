"""Thread history endpoints: PATCH transcript/title, list, auto-title."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.research_agent import ResearchAgentThread
from app.services.research_agent.title import fallback_title


pytestmark = pytest.mark.asyncio


async def _make_thread(db_session, sample_run, *, title="Research session", messages=None):
    thread = ResearchAgentThread(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        user_id=sample_run["user_id"],
        title=title,
        messages=messages or [],
        canvas_state={"artifacts": [], "active_key": None},
    )
    db_session.add(thread)
    await db_session.commit()
    await db_session.refresh(thread)
    return thread


async def test_patch_thread_saves_messages(db_session, sample_run):
    thread = await _make_thread(db_session, sample_run)
    messages = [
        {"id": "1", "role": "user", "content": "What links do we have?"},
        {"id": "2", "role": "assistant", "content": "P50 and P170."},
    ]
    resp = await sample_run["client"].patch(
        f"/api/research-agent/threads/{thread.id}",
        json={"messages": messages},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["message_count"] == 2
    await db_session.refresh(thread)
    assert thread.messages == messages


async def test_patch_thread_renames(db_session, sample_run):
    thread = await _make_thread(db_session, sample_run)
    resp = await sample_run["client"].patch(
        f"/api/research-agent/threads/{thread.id}",
        json={"title": "QID links"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "QID links"


async def test_patch_thread_unknown_id_is_404(sample_run):
    resp = await sample_run["client"].patch(
        f"/api/research-agent/threads/{uuid.uuid4()}",
        json={"title": "nope"},
    )
    assert resp.status_code == 404


async def test_list_threads_ordered_newest_first(db_session, sample_run):
    old = await _make_thread(db_session, sample_run, title="old")
    new = await _make_thread(db_session, sample_run, title="new")
    old.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new.updated_at = datetime(2026, 9, 13, tzinfo=timezone.utc)
    await db_session.commit()
    resp = await sample_run["client"].get(
        f"/api/research-agent/threads?project_id={sample_run['project_id']}"
    )
    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()]
    assert titles[0] == "new"
    assert "old" in titles


async def test_list_threads_rejects_foreign_project(db_session, sample_run):
    await _make_thread(db_session, sample_run, title="mine")
    resp = await sample_run["client"].get(
        f"/api/research-agent/threads?project_id={uuid.uuid4()}"
    )
    assert resp.status_code == 403


async def test_auto_title_uses_ai(db_session, sample_run):
    thread = await _make_thread(
        db_session, sample_run,
        messages=[{"id": "1", "role": "user", "content": "Which manuscripts mention Scholem?"}],
    )
    with patch(
        "app.services.research_agent.title.generate_thread_title",
        new=AsyncMock(return_value="Gershom Scholem manuscripts"),
    ):
        resp = await sample_run["client"].post(
            f"/api/research-agent/threads/{thread.id}/auto-title",
        )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Gershom Scholem manuscripts"
    await db_session.refresh(thread)
    assert thread.title == "Gershom Scholem manuscripts"


async def test_auto_title_falls_back_on_ai_failure(db_session, sample_run):
    text = "Which manuscripts mention Gershom Scholem and where were they copied?"
    thread = await _make_thread(
        db_session, sample_run,
        messages=[{"id": "1", "role": "user", "content": text}],
    )
    with patch(
        "app.services.research_agent.title.generate_thread_title",
        new=AsyncMock(return_value=None),
    ):
        resp = await sample_run["client"].post(
            f"/api/research-agent/threads/{thread.id}/auto-title",
        )
    assert resp.status_code == 200
    assert resp.json()["title"] == fallback_title(text)


async def test_fallback_title_truncates():
    assert fallback_title("  hello   world  ") == "hello world"
    assert len(fallback_title("x" * 300)) <= 80
    assert fallback_title("") == "Research session"


async def test_session_request_new_thread_flag():
    from app.routers.research_agent import SessionRequest
    assert SessionRequest(run_id=uuid.uuid4(), new_thread=True).new_thread is True
    assert SessionRequest(run_id=uuid.uuid4()).new_thread is False
