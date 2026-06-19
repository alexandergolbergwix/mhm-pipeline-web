"""Shared cache infrastructure (Redis L1 for inference + scoped read-model)."""

from app.cache.redis_client import close_redis, get_redis
from app.cache.scoped_cache import (
    cache_redis_key,
    invalidate_scope,
    scoped_cache_get,
    scoped_cache_lookup_or_call,
)

__all__ = [
    "cache_redis_key",
    "close_redis",
    "get_redis",
    "invalidate_scope",
    "scoped_cache_get",
    "scoped_cache_lookup_or_call",
]
