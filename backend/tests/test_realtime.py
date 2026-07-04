"""Tests for the real-time broker and WebSocket auth gate."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid

import pytest
from starlette.testclient import TestClient

from app.auth.session import COOKIE_NAME
from app.realtime import MAX_CONNECTIONS_PER_USER, Broker


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest.mark.asyncio
async def test_broker_broadcast_reaches_room() -> None:
    broker = Broker()
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()

    assert await broker.join(project_id, ws1, user_id) is True
    assert await broker.join(project_id, ws2, user_id) is True

    msg = {"project_id": str(project_id), "type": "match.approved"}
    await broker.broadcast(project_id, msg)

    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1
    assert json.loads(ws1.sent[0]) == msg

    await broker.leave(project_id, ws1)
    await broker.broadcast(project_id, msg)
    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 2


@pytest.mark.asyncio
async def test_broker_culls_dead_sockets() -> None:
    broker = Broker()
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()

    class _BrokenWS:
        async def send_text(self, _text: str) -> None:
            raise RuntimeError("socket gone")

    dead = _BrokenWS()
    alive = _FakeWebSocket()
    await broker.join(project_id, dead, user_id)
    await broker.join(project_id, alive, user_id)

    await broker.broadcast(project_id, {"type": "ping"})
    assert len(alive.sent) == 1

    await broker.broadcast(project_id, {"type": "ping"})
    assert len(alive.sent) == 2


@pytest.mark.asyncio
async def test_broker_connection_cap_per_user() -> None:
    broker = Broker()
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()

    for _ in range(MAX_CONNECTIONS_PER_USER):
        assert await broker.join(project_id, _FakeWebSocket(), user_id) is True

    overflow = _FakeWebSocket()
    assert await broker.join(project_id, overflow, user_id) is False


def test_ws_rejects_missing_cookie(_app_factory, sample_run) -> None:
    project_id = sample_run["project_id"]
    with TestClient(_app_factory) as client:
        with pytest.raises(Exception):  # noqa: B017 — Starlette closes before accept
            with client.websocket_connect(f"/api/ws/projects/{project_id}"):
                pass


@pytest.mark.asyncio
async def test_ws_rejects_non_member(_app_factory, sample_run, db_session) -> None:
    from app.auth import password as pw
    from app.auth.session import create_session
    from app.crypto import index as idx
    from app.crypto import kek as kek_mod
    from app.crypto import pii
    from app.models.user import ROLE_EDITOR, User

    project_id = sample_run["project_id"]
    outsider_email = f"outsider+{uuid.uuid4().hex[:8]}@example.com"
    password = "Outsider-Password-1!"
    outsider = User(
        email_index=idx.blind_index(outsider_email),
        email_encrypted=pii.encrypt_pii(outsider_email),
        name_encrypted=pii.encrypt_pii("Outsider"),
        password_hash=pw.hash_password(password),
        kek_salt=pii.random_bytes(16),
        role=ROLE_EDITOR,
    )
    db_session.add(outsider)
    await db_session.commit()

    kek = kek_mod.derive_kek(password, salt=outsider.kek_salt)
    session_row, session_secret = await create_session(db_session, user=outsider, kek=kek)
    await db_session.commit()

    cookie_value = (
        f"{session_row.id}."
        f"{base64.urlsafe_b64encode(session_secret).decode('ascii').rstrip('=')}"
    )

    def _connect() -> None:
        with TestClient(_app_factory) as client:
            client.cookies.set(COOKIE_NAME, cookie_value)
            with pytest.raises(Exception):  # noqa: B017
                with client.websocket_connect(f"/api/ws/projects/{project_id}"):
                    pass

    await asyncio.to_thread(_connect)


@pytest.mark.asyncio
async def test_ws_accepts_member_and_receives_ping(_app_factory, sample_run, auth_user) -> None:
    _user, async_client = auth_user
    cookie_value = async_client.cookies.get(COOKIE_NAME)
    assert cookie_value
    project_id = sample_run["project_id"]

    def _connect() -> str:
        with TestClient(_app_factory) as client:
            client.cookies.set(COOKIE_NAME, cookie_value)
            with client.websocket_connect(f"/api/ws/projects/{project_id}") as ws:
                return ws.receive_text()

    first = await asyncio.to_thread(_connect)
    parsed = json.loads(first)
    assert parsed["type"] == "ping"
