"""Tests for run / project / user scoped cache (Tier 2)."""

from __future__ import annotations

import pytest

from app.cache.scoped_cache import (
    cache_redis_key,
    clear_memory_cache_for_tests,
    invalidate_scope,
    scoped_cache_lookup_or_call,
)


@pytest.fixture(autouse=True)
def _wipe_memory_cache() -> None:
    clear_memory_cache_for_tests()
    yield
    clear_memory_cache_for_tests()


class TestScopedCacheKeys:
    def test_global_kind_uses_inference_prefix(self) -> None:
        key = cache_redis_key("global", None, "authority.viaf", "abc" * 16)
        assert key == f"ic:authority.viaf:{'abc' * 16}"

    def test_run_scope_includes_run_id(self) -> None:
        k1 = cache_redis_key("run", "run-a", "extraction.entities", "hash1")
        k2 = cache_redis_key("run", "run-b", "extraction.entities", "hash1")
        assert k1 == "rm:run-a:extraction.entities:hash1"
        assert k2 == "rm:run-b:extraction.entities:hash1"
        assert k1 != k2

    def test_user_scope_isolated(self) -> None:
        k1 = cache_redis_key("user", "user-a", "prefs", "hash1")
        k2 = cache_redis_key("user", "user-b", "prefs", "hash1")
        assert k1.startswith("u:user-a:")
        assert k2.startswith("u:user-b:")
        assert k1 != k2


class TestScopedCacheLookup:
    @pytest.mark.asyncio
    async def test_second_lookup_is_cache_hit(self) -> None:
        calls = 0

        async def fetch() -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"n": 42}

        qs = {"fp": "test-hit"}
        r1 = await scoped_cache_lookup_or_call(
            scope="run", scope_id="run-1", kind="test.kind",
            query_summary=qs, fetch=fetch,
        )
        r2 = await scoped_cache_lookup_or_call(
            scope="run", scope_id="run-1", kind="test.kind",
            query_summary=qs, fetch=fetch,
        )
        assert r1 == {"n": 42}
        assert r2 == {"n": 42}
        assert calls == 1

    @pytest.mark.asyncio
    async def test_skip_cache_always_refetches(self) -> None:
        calls = 0

        async def fetch() -> int:
            nonlocal calls
            calls += 1
            return calls

        qs = {"fp": "skip-test"}
        await scoped_cache_lookup_or_call(
            scope="run", scope_id="run-1", kind="test.skip",
            query_summary=qs, fetch=fetch, skip_cache=True,
        )
        await scoped_cache_lookup_or_call(
            scope="run", scope_id="run-1", kind="test.skip",
            query_summary=qs, fetch=fetch, skip_cache=True,
        )
        assert calls == 2

    @pytest.mark.asyncio
    async def test_different_users_do_not_share_user_scope(self) -> None:
        calls = 0

        async def fetch() -> str:
            nonlocal calls
            calls += 1
            return "secret"

        qs = {"view": "dashboard"}
        await scoped_cache_lookup_or_call(
            scope="user", scope_id="alice", kind="dashboard",
            query_summary=qs, fetch=fetch,
        )
        await scoped_cache_lookup_or_call(
            scope="user", scope_id="bob", kind="dashboard",
            query_summary=qs, fetch=fetch,
        )
        assert calls == 2

    @pytest.mark.asyncio
    async def test_invalidate_scope_clears_run_entries(self) -> None:
        calls = 0

        async def fetch() -> str:
            nonlocal calls
            calls += 1
            return "fresh"

        qs = {"fp": "inv"}
        await scoped_cache_lookup_or_call(
            scope="run", scope_id="run-x", kind="test.inv",
            query_summary=qs, fetch=fetch,
        )
        await invalidate_scope("run", "run-x")
        await scoped_cache_lookup_or_call(
            scope="run", scope_id="run-x", kind="test.inv",
            query_summary=qs, fetch=fetch,
        )
        assert calls == 2
