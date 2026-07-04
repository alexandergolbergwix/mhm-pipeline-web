"""Real-time collaboration broker.

Two pieces:

1. ``Broker`` — a single per-process WebSocket hub keyed by project_id.
   Connections subscribe by joining a project's room; ``broadcast``
   pushes a JSON payload to every connection in that room. Connection
   teardown is symmetric; broken sockets get culled on the next send.

2. ``EventStream`` — an asyncpg listener that subscribes to a single
   Postgres NOTIFY channel (``project_events``). Every append-event
   trigger fires a row with ``{project_id, event_id, type, ...}``; we
   fan that out through the in-process Broker so other dynos receive
   their share. The trigger lives in migration 0006 (trimmed in 0027).

Heroku Postgres LISTEN/NOTIFY is the default pub/sub fabric for
cross-dyno fan-out. Heroku Redis (Rule W-25) is available as a
fallback if NOTIFY reliability ever becomes an issue.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import asyncpg
from fastapi import WebSocket
from sqlalchemy.engine.url import make_url

from app.settings import get_settings

logger = logging.getLogger(__name__)

NOTIFY_CHANNEL = "project_events"
MAX_CONNECTIONS_PER_USER = 5


@dataclass(frozen=True, slots=True)
class _Connection:
    ws: WebSocket
    user_id: uuid.UUID


class Broker:
    """Per-process WebSocket pub/sub keyed by ``project_id``."""

    def __init__(self) -> None:
        self._rooms: dict[uuid.UUID, set[_Connection]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def join(
        self, project_id: uuid.UUID, ws: WebSocket, user_id: uuid.UUID,
    ) -> bool:
        async with self._lock:
            room = self._rooms[project_id]
            user_count = sum(1 for conn in room if conn.user_id == user_id)
            if user_count >= MAX_CONNECTIONS_PER_USER:
                return False
            room.add(_Connection(ws=ws, user_id=user_id))
            return True

    async def leave(self, project_id: uuid.UUID, ws: WebSocket) -> None:
        async with self._lock:
            room = self._rooms.get(project_id)
            if not room:
                return
            room.difference_update({conn for conn in room if conn.ws is ws})
            if not room:
                self._rooms.pop(project_id, None)

    async def broadcast(self, project_id: uuid.UUID, message: dict[str, Any]) -> None:
        room = list(self._rooms.get(project_id, set()))
        if not room:
            return
        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for conn in room:
            try:
                await conn.ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(conn.ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._rooms.get(project_id, set()).difference_update(
                        {conn for conn in self._rooms.get(project_id, set()) if conn.ws is ws},
                    )
                if not self._rooms.get(project_id):
                    self._rooms.pop(project_id, None)


broker = Broker()


# ── Postgres LISTEN bridge ─────────────────────────────────────────────


async def _asyncpg_dsn() -> str:
    """Convert the SQLAlchemy URL to a raw asyncpg DSN."""
    url = make_url(get_settings().database_url)
    return (
        f"postgresql://{url.username or ''}:{url.password or ''}"
        f"@{url.host}:{url.port or 5432}/{url.database}"
    )


async def _on_notify(_conn: asyncpg.Connection, _pid: int, _chan: str, payload: str) -> None:
    try:
        msg = json.loads(payload)
        pid = uuid.UUID(msg["project_id"])
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("malformed NOTIFY payload: %r", payload)
        return
    await broker.broadcast(pid, msg)


_listener_task: asyncio.Task[None] | None = None


async def start_listener() -> None:
    global _listener_task
    if os.environ.get("DISABLE_PG_LISTENER", "").strip() in {"1", "true", "yes"}:
        return
    if _listener_task is not None:
        return
    _listener_task = asyncio.create_task(_listener_loop(), name="pg-notify-listener")


async def stop_listener() -> None:
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _listener_task
    _listener_task = None


async def _listener_loop() -> None:
    """Forever-reconnecting listener — survives transient pg restarts."""
    while True:
        try:
            conn = await asyncpg.connect(await _asyncpg_dsn())
        except Exception as exc:  # noqa: BLE001
            logger.warning("asyncpg connect failed (%s); retrying in 5s", exc)
            await asyncio.sleep(5)
            continue
        try:
            await conn.add_listener(NOTIFY_CHANNEL, _on_notify)
            logger.info("listening on Postgres channel %s", NOTIFY_CHANNEL)
            while True:
                await asyncio.sleep(60)
                try:
                    await conn.fetchval("SELECT 1")
                except Exception:  # noqa: BLE001
                    break
        finally:
            with contextlib.suppress(Exception):
                await conn.remove_listener(NOTIFY_CHANNEL, _on_notify)
            with contextlib.suppress(Exception):
                await conn.close()
        await asyncio.sleep(2)
