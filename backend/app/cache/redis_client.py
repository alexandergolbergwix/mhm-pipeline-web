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

    # Heroku Redis uses a self-signed TLS cert on rediss:// URLs.
    # ssl_cert_reqs=None disables hostname verification for that cert
    # while keeping the transport encrypted.
    _client = aioredis.from_url(
        url,
        decode_responses=False,
        ssl_cert_reqs=None,
    )
    return _client


async def close_redis() -> None:
    """Close the shared client on app shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
