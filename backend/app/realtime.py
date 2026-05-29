"""Real-time collaboration broker.

Two pieces:

1. ``Broker`` — a single per-process WebSocket hub keyed by project_id.
   Connections subscribe by joining a project's room; ``broadcast``
   pushes a JSON payload to every connection in that room. Connection
   teardown is symmetric; broken sockets get culled on the next send.

2. ``EventStream`` — an asyncpg listener that subscribes to a single
   Postgres NOTIFY channel (``project_events``). Every append-event
   trigger fires a row with ``{project_id, event_id}``; we fan that out
   through the in-process Broker so other dynos receive their share.
   The trigger lives in migration 0006.

This pattern lets Heroku Postgres be the canonical pub/sub fabric — no
Redis bill — while in-process WebSocket fan-out keeps the latency low.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections import defaultdict
from typing import Any

import asyncpg
from fastapi import WebSocket
from sqlalchemy.engine.url import make_url

from app.settings import get_settings

logger = logging.getLogger(__name__)

NOTIFY_CHANNEL = "project_events"


class Broker:
    """Per-process WebSocket pub/sub keyed by ``project_id``."""

    def __init__(self) -> None:
        self._rooms: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def join(self, project_id: uuid.UUID, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms[project_id].add(ws)

    async def leave(self, project_id: uuid.UUID, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms.get(project_id, set()).discard(ws)
            if not self._rooms.get(project_id):
                self._rooms.pop(project_id, None)

    async def broadcast(self, project_id: uuid.UUID, message: dict[str, Any]) -> None:
        room = list(self._rooms.get(project_id, set()))
        if not room:
            return
        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for ws in room:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._rooms.get(project_id, set()).discard(ws)


broker = Broker()


# ── Postgres LISTEN bridge ─────────────────────────────────────────────


async def _asyncpg_dsn() -> str:
    """Convert the SQLAlchemy URL to a raw asyncpg DSN."""
    url = make_url(get_settings().database_url)
    # SQLAlchemy uses postgresql+asyncpg://; asyncpg wants plain postgresql://
    return f"postgresql://{url.username or ''}:{url.password or ''}@{url.host}:{url.port or 5432}/{url.database}"


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
            # Park forever until the connection drops or we're cancelled.
            while True:
                await asyncio.sleep(60)
                # cheap keepalive
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
