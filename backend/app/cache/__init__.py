"""Shared cache infrastructure (Redis L1 for inference)."""

from app.cache.redis_client import close_redis, get_redis

__all__ = ["close_redis", "get_redis"]
