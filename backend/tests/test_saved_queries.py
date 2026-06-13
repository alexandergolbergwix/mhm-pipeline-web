"""Tests for the Saved Queries CRUD API (Feature 2).

Endpoints under ``/api/projects/{project_id}/research/saved-queries``:
  GET    /                 → list (viewer)
  POST   /                 → create (editor)
  GET    /{query_id}       → get (viewer)
  PUT    /{query_id}       → update (editor, only own or owner)
  DELETE /{query_id}       → delete (editor, only own or owner)

Cross-project isolation: queries are scoped to a project; another project
cannot see them.

Param placeholders (``{{author}}``) in the query field are preserved verbatim.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
import uuid


# ── Helpers ──────────────────────────────────────────────────────────────────

def _list_url(project_id) -> str:
    return f"/api/projects/{project_id}/research/saved-queries"


def _item_url(project_id, query_id) -> str:
    return f"/api/projects/{project_id}/research/saved-queries/{query_id}"


_SAMPLE_BODY = {
    "name": "Works by author",
    "description": "Find all works attributed to a given author",
    "query": "SELECT ?w WHERE { ?w hm:has_author ?a . FILTER(?a = <{{author}}>) }",
}


# ── Create ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_returns_201(sample_run):
    resp = await sample_run["client"].post(
        _list_url(sample_run["project_id"]), json=_SAMPLE_BODY
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == _SAMPLE_BODY["name"]
    assert "{{author}}" in body["query"]
    assert "id" in body


@pytest.mark.asyncio
async def test_create_requires_auth(sample_run):
    """A request with no session cookie should get 401."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app as _app

    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            _list_url(sample_run["project_id"]), json=_SAMPLE_BODY
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_non_member_forbidden(sample_run, db_session, async_client):
    """A user who is not a member of the project cannot create."""
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    email = "outsider2@example.com"
    password = "Strong-Pass-99!"
    outsider = User(
        email_index=idx.blind_index(email),
        email_encrypted=pii.encrypt_pii(email),
        name_encrypted=pii.encrypt_pii("Outsider2"),
        password_hash=pw.hash_password(password),
        kek_salt=pii.random_bytes(16),
        role=ROLE_EDITOR,
    )
    db_session.add(outsider)
    await db_session.commit()
    kek = kek_mod.derive_kek(password, salt=outsider.kek_salt)
    row, secret = await create_session(db_session, user=outsider, kek=kek)
    await db_session.commit()
    cookie = f"{row.id}.{base64.urlsafe_b64encode(secret).decode().rstrip('=')}"
    async_client.cookies.set(COOKIE_NAME, cookie)

    resp = await async_client.post(
        _list_url(sample_run["project_id"]), json=_SAMPLE_BODY
    )
    assert resp.status_code == 403


# ── List ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_empty_initially(sample_run):
    resp = await sample_run["client"].get(_list_url(sample_run["project_id"]))
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_returns_created(sample_run):
    await sample_run["client"].post(_list_url(sample_run["project_id"]), json=_SAMPLE_BODY)
    resp = await sample_run["client"].get(_list_url(sample_run["project_id"]))
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["name"] == _SAMPLE_BODY["name"]


# ── Get ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_returns_saved_query(sample_run):
    create_resp = await sample_run["client"].post(
        _list_url(sample_run["project_id"]), json=_SAMPLE_BODY
    )
    query_id = create_resp.json()["id"]
    resp = await sample_run["client"].get(_item_url(sample_run["project_id"], query_id))
    assert resp.status_code == 200
    assert resp.json()["id"] == query_id


@pytest.mark.asyncio
async def test_get_unknown_returns_404(sample_run):
    resp = await sample_run["client"].get(
        _item_url(sample_run["project_id"], str(uuid.uuid4()))
    )
    assert resp.status_code == 404


# ── Update ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_name(sample_run):
    create_resp = await sample_run["client"].post(
        _list_url(sample_run["project_id"]), json=_SAMPLE_BODY
    )
    query_id = create_resp.json()["id"]
    resp = await sample_run["client"].put(
        _item_url(sample_run["project_id"], query_id),
        json={"name": "Renamed query"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed query"
    # query unchanged
    assert "{{author}}" in resp.json()["query"]


@pytest.mark.asyncio
async def test_update_unknown_returns_404(sample_run):
    resp = await sample_run["client"].put(
        _item_url(sample_run["project_id"], str(uuid.uuid4())),
        json={"name": "x"},
    )
    assert resp.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_removes_item(sample_run):
    create_resp = await sample_run["client"].post(
        _list_url(sample_run["project_id"]), json=_SAMPLE_BODY
    )
    query_id = create_resp.json()["id"]
    del_resp = await sample_run["client"].delete(
        _item_url(sample_run["project_id"], query_id)
    )
    assert del_resp.status_code == 204
    get_resp = await sample_run["client"].get(
        _item_url(sample_run["project_id"], query_id)
    )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_unknown_returns_404(sample_run):
    resp = await sample_run["client"].delete(
        _item_url(sample_run["project_id"], str(uuid.uuid4()))
    )
    assert resp.status_code == 404


# ── Cross-project isolation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_project_isolation(sample_run, db_session, auth_user):
    """A saved query in project A is not visible to a request scoped to project B."""
    from app.models.project import Membership, Project, PROJECT_ROLE_EDITOR
    import uuid as _uuid

    user, client = auth_user
    # Create a second project for the same user
    project_b = Project(owner_id=user.id, name="Project B", description="")
    db_session.add(project_b)
    await db_session.flush()
    db_session.add(Membership(project_id=project_b.id, user_id=user.id, role=PROJECT_ROLE_EDITOR))
    await db_session.commit()

    # Create a query under project A
    await sample_run["client"].post(_list_url(sample_run["project_id"]), json=_SAMPLE_BODY)

    # List under project B should be empty
    resp = await sample_run["client"].get(_list_url(project_b.id))
    assert resp.status_code == 200
    assert resp.json() == []
