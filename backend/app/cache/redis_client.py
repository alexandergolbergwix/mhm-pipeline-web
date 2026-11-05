"""Lazy singleton async Redis client for inference-cache L1.

Returns ``None`` when ``REDIS_URL`` / ``redis_url`` is unset so dev, CI,
and single-dyno deploys without the add-on keep Postgres-only caching.
"""

from __future__ import annotations

import os
from typing import Any

_client: Any = None


async def get_redis() -> Any:
    """Return the shared async Redis client, or ``None`` if not configured."""
    global _client
    if _client is not None:
        return _client
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        from app.settings import get_settings  # noqa: PLC0415

        url = get_settings().redis_url.strip()
    if not url:
        return None
    import redis.asyncio as aioredis  # noqa: PLC0415
    from redis.backoff import ExponentialBackoff  # noqa: PLC0415
    from redis.retry import Retry  # noqa: PLC0415

    # Heroku Redis uses a self-signed TLS cert on rediss:// URLs.
    # ssl_cert_reqs=None disables hostname verification for that cert
    # while keeping the transport encrypted.
    #
    # health_check_interval / socket_keepalive / retry: a pooled connection
    # that the server dropped under dyno memory pressure was written to
    # dead once and surfaced as an unhandled ConnectionError ("Connection
    # lost", production incident 2026-11-05). The ping refreshes stale
    # connections and the retry replays the one dropped command.
    _client = aioredis.from_url(
        url,
        decode_responses=False,
        ssl_cert_reqs=None,
        socket_keepalive=True,
        health_check_interval=30,
        retry=Retry(ExponentialBackoff(cap=1.0, base=0.1), 2),
    )
    return _client


async def close_redis() -> None:
    """Close the shared client on app shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def open_redis() -> Any:
    """Open a DEDICATED connection (not the shared singleton).

    Use for blocking commands (XREAD BLOCK). Cancelling a blocking read
    on the shared pool corrupts its connection and takes every other
    Redis user down with it ("Connection lost").
    """
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        from app.settings import get_settings  # noqa: PLC0415

        url = get_settings().redis_url.strip()
    if not url:
        return None
    import redis.asyncio as aioredis  # noqa: PLC0415

    # socket_timeout must be None: redis-py defaults it to 5s, which kills
    # any XREAD BLOCK longer than 5s (production incident 2026-09-14).
    return aioredis.from_url(
        url,
        decode_responses=False,
        socket_timeout=None,
        ssl_cert_reqs=None,
    )
