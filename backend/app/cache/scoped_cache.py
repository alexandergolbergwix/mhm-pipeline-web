"""Run / project / user scoped cache (Tier 2).

Global external inference stays in :mod:`app.pipeline.inference_cache`
(``ic:{kind}:{hash}``). This module handles curator-facing read models
where the answer depends on run, project, or user context.

Lookup order when Redis is configured:
  Redis L1 → in-process memory L0 (always, for CI/dev fallback)

Redis key shapes:
  global  → ``ic:{kind}:{hash}``  (delegates prefix only; use inference_cache)
  project → ``pm:{project_id}:{kind}:{hash}``
  run     → ``rm:{run_id}:{kind}:{hash}``
  user    → ``u:{user_id}:{kind}:{hash}``
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable, Literal

from app.cache.cache_policy import SCOPED_MEMORY_TTL_SECONDS, SCOPED_REDIS_TTL_SECONDS
from app.pipeline.inference_cache import canonical_hash

logger = logging.getLogger(__name__)

CacheScope = Literal["global", "project", "run", "user"]

_SCOPE_PREFIX: dict[str, str] = {
    "global":  "ic",
    "project": "pm",
    "run":     "rm",
    "user":    "u",
}

# In-process fallback when Redis is absent or on L1 miss.
_memory: dict[str, tuple[Any, float | None]] = {}


def cache_redis_key(
    scope: CacheScope,
    scope_id: str | None,
    kind: str,
    query_hash: str,
) -> str:
    prefix = _SCOPE_PREFIX[scope]
    if scope == "global":
        return f"{prefix}:{kind}:{query_hash}"
    if not scope_id:
        raise ValueError(f"scope_id required for scope={scope!r}")
    return f"{prefix}:{scope_id}:{kind}:{query_hash}"


def _memory_ttl_seconds(kind: str) -> float | None:
    ttl = SCOPED_MEMORY_TTL_SECONDS.get(kind)
    return float(ttl) if ttl is not None else None


def _redis_ttl_seconds(kind: str) -> int | None:
    return SCOPED_REDIS_TTL_SECONDS.get(kind)


def _memory_get(key: str) -> Any | None:
    entry = _memory.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if expires_at is not None and time.monotonic() > expires_at:
        _memory.pop(key, None)
        return None
    return value


def _memory_set(key: str, value: Any, *, kind: str) -> None:
    ttl = _memory_ttl_seconds(kind)
    expires_at = (time.monotonic() + ttl) if ttl is not None else None
    _memory[key] = (value, expires_at)


async def _redis_get(key: str) -> Any | None:
    from app.cache.redis_client import get_redis  # noqa: PLC0415

    redis = await get_redis()
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scoped cache redis GET failed (%s): %s", key, exc)
        return None


async def _redis_set(key: str, value: Any, *, kind: str) -> None:
    from app.cache.redis_client import get_redis  # noqa: PLC0415

    redis = await get_redis()
    if redis is None:
        return
    try:
        raw = json.dumps(value, ensure_ascii=False)
        ttl = _redis_ttl_seconds(kind)
        if ttl is None:
            await redis.set(key, raw)
        else:
            await redis.set(key, raw, ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scoped cache redis SET failed (%s): %s", key, exc)


async def scoped_cache_get(
    *,
    scope: CacheScope,
    scope_id: str | None,
    kind: str,
    query_summary: dict[str, Any],
) -> Any | None:
    """Return a cached value without calling *fetch*."""
    query_hash = canonical_hash(query_summary)
    key = cache_redis_key(scope, scope_id, kind, query_hash)
    hit = await _redis_get(key)
    if hit is not None:
        return hit
    return _memory_get(key)


async def scoped_cache_lookup_or_call(
    *,
    scope: CacheScope,
    scope_id: str | None,
    kind: str,
    query_summary: dict[str, Any],
    fetch: Callable[[], Awaitable[Any]],
    skip_cache: bool = False,
) -> Any:
    query_hash = canonical_hash(query_summary)
    key = cache_redis_key(scope, scope_id, kind, query_hash)

    if not skip_cache:
        hit = await _redis_get(key)
        if hit is not None:
            return hit
        hit = _memory_get(key)
        if hit is not None:
            await _redis_set(key, hit, kind=kind)
            return hit

    result = await fetch()

    if skip_cache:
        await invalidate_scope(scope, scope_id, kind=kind)
        await _redis_set(key, result, kind=kind)
        _memory_set(key, result, kind=kind)
        return result

    if result is None:
        return result
    if isinstance(result, list) and not result:
        return result

    await _redis_set(key, result, kind=kind)
    _memory_set(key, result, kind=kind)
    return result


async def invalidate_scope(
    scope: CacheScope,
    scope_id: str | None,
    *,
    kind: str | None = None,
) -> None:
    """Drop cached entries for a scope (all kinds or one kind)."""
    from app.cache.redis_client import get_redis  # noqa: PLC0415

    prefix = _SCOPE_PREFIX[scope]
    if scope == "global":
        pattern = f"{prefix}:{kind}:" if kind else f"{prefix}:"
    elif scope_id:
        pattern = (
            f"{prefix}:{scope_id}:{kind}:"
            if kind
            else f"{prefix}:{scope_id}:"
        )
    else:
        return

    to_drop = [k for k in _memory if k.startswith(pattern)]
    for k in to_drop:
        _memory.pop(k, None)

    redis = await get_redis()
    if redis is None:
        return
    try:
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=f"{pattern}*", count=100)
            if keys:
                await redis.delete(*keys)
            if cursor == 0:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("scoped cache redis invalidate failed (%s): %s", pattern, exc)


def clear_memory_cache_for_tests() -> None:
    """Test helper — wipe the in-process tier."""
    _memory.clear()
