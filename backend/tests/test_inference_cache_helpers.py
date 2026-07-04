"""Tests for the two direct-write/read helpers added to inference_cache.py.

Pins four contracts for :func:`write_to_inference_cache` and
:func:`read_from_inference_cache`:

1. ``write_to_inference_cache`` persists to Postgres (L2) and a
   subsequent ``read_from_inference_cache`` returns the same payload.
2. ``read_from_inference_cache`` returns ``None`` on a cold-cache miss.
3. Writing ``None`` (or an empty list) is a no-op — the row is not
   inserted and a subsequent read still returns ``None``.
4. Writing the same key twice performs an upsert: the second write wins.

Redis (L1) is absent in the SQLite CI environment — ``get_redis()``
returns ``None`` so both helpers degrade gracefully to Postgres-only.
"""

from __future__ import annotations

import pytest


class TestWriteToInferenceCache:
    @pytest.mark.asyncio
    async def test_write_then_read_roundtrip(self, db_session) -> None:
        from app.pipeline.inference_cache import (
            read_from_inference_cache,
            write_to_inference_cache,
        )

        qs = {"kind_hint": "test", "entity_text": "Maimonides", "type": "PERSON"}
        payload = {"overall": "pass", "name_ok": True, "reasoning": "Clearly Maimonides."}

        await write_to_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs, result=payload,
        )

        hit = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
        )
        assert hit is not None
        assert hit["overall"] == "pass"
        assert hit["reasoning"] == "Clearly Maimonides."

    @pytest.mark.asyncio
    async def test_read_returns_none_on_miss(self, db_session) -> None:
        from app.pipeline.inference_cache import read_from_inference_cache

        hit = await read_from_inference_cache(
            db_session,
            kind="ai_verdict",
            query_summary={"entity_text": "nobody-ever-wrote-this-before", "type": "PERSON"},
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_write_none_is_noop(self, db_session) -> None:
        from app.pipeline.inference_cache import (
            read_from_inference_cache,
            write_to_inference_cache,
        )

        qs = {"entity_text": "silent-null-test", "type": "PERSON"}

        await write_to_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs, result=None,
        )

        hit = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_write_empty_list_is_noop(self, db_session) -> None:
        from app.pipeline.inference_cache import (
            read_from_inference_cache,
            write_to_inference_cache,
        )

        qs = {"entity_text": "empty-list-test", "type": "PERSON"}

        await write_to_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs, result=[],
        )

        hit = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_second_write_upserts(self, db_session) -> None:
        """Writing the same key twice should update (upsert), not duplicate."""
        from app.pipeline.inference_cache import (
            read_from_inference_cache,
            write_to_inference_cache,
        )

        qs = {"entity_text": "Rambam", "type": "PERSON"}

        await write_to_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
            result={"overall": "partial", "reasoning": "First judgement."},
        )
        await write_to_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
            result={"overall": "pass", "reasoning": "Revised — clearly Rambam."},
        )

        hit = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
        )
        assert hit is not None
        assert hit["overall"] == "pass"
        assert hit["reasoning"] == "Revised — clearly Rambam."

    @pytest.mark.asyncio
    async def test_different_query_summaries_are_independent(self, db_session) -> None:
        """Two distinct query summaries must not collide in the cache."""
        from app.pipeline.inference_cache import (
            read_from_inference_cache,
            write_to_inference_cache,
        )

        qs_a = {"entity_text": "Rashi", "type": "PERSON", "role": "author"}
        qs_b = {"entity_text": "Rashi", "type": "PERSON", "role": "scribe"}

        await write_to_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs_a,
            result={"overall": "pass"},
        )

        hit_a = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs_a,
        )
        hit_b = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs_b,
        )
        assert hit_a is not None
        assert hit_b is None


class TestEndpointQuerySummary:
    def test_strips_volatile_keys(self) -> None:
        from app.pipeline.inference_cache import endpoint_query_summary

        qs = endpoint_query_summary(
            endpoint="https://example.org/api",
            payload={"qid": "Q42", "timestamp": "2026-07-04T12:00:00Z", "nested": {
                "created_at": "2026-01-01", "value": "keep-me",
            }},
        )
        assert "timestamp" not in qs["payload"]
        assert qs["payload"]["qid"] == "Q42"
        assert "created_at" not in qs["payload"]["nested"]
        assert qs["payload"]["nested"]["value"] == "keep-me"

    def test_same_endpoint_and_payload_hash_identically_despite_dates(self) -> None:
        from app.pipeline.inference_cache import canonical_hash, endpoint_query_summary

        qs_a = endpoint_query_summary(
            endpoint="https://example.org/api",
            payload={"qid": "Q42", "requested_at": "2026-07-04T12:00:00Z"},
        )
        qs_b = endpoint_query_summary(
            endpoint="https://example.org/api",
            payload={"qid": "Q42", "requested_at": "2026-07-05T09:30:00Z"},
        )
        assert canonical_hash(qs_a) == canonical_hash(qs_b)

    def test_different_endpoint_produces_different_hash(self) -> None:
        from app.pipeline.inference_cache import canonical_hash, endpoint_query_summary

        qs_a = endpoint_query_summary(endpoint="https://a.example.org/api", payload={"qid": "Q42"})
        qs_b = endpoint_query_summary(endpoint="https://b.example.org/api", payload={"qid": "Q42"})
        assert canonical_hash(qs_a) != canonical_hash(qs_b)


class TestCacheHttpCall:
    @pytest.mark.asyncio
    async def test_second_call_skips_fetch(self, db_session) -> None:
        from app.pipeline.inference_cache import cache_http_call

        calls = {"n": 0}

        async def _fetch() -> dict:
            calls["n"] += 1
            return {"label": "Maimonides"}

        for _ in range(2):
            result = await cache_http_call(
                db_session,
                kind="wikidata.label",
                endpoint="https://www.wikidata.org/w/api.php",
                payload={"id": "Q133337", "lang": "en", "fetched_at": "2026-07-04"},
                fetch=_fetch,
            )
            assert result == {"label": "Maimonides"}

        assert calls["n"] == 1


class TestCanonicalHash:
    def test_key_order_does_not_affect_hash(self) -> None:
        """canonical_hash must be stable regardless of dict insertion order."""
        from app.pipeline.inference_cache import canonical_hash

        h1 = canonical_hash({"a": 1, "b": 2})
        h2 = canonical_hash({"b": 2, "a": 1})
        assert h1 == h2

    def test_different_values_produce_different_hashes(self) -> None:
        from app.pipeline.inference_cache import canonical_hash

        h1 = canonical_hash({"text": "Maimonides", "type": "PERSON"})
        h2 = canonical_hash({"text": "Rashi", "type": "PERSON"})
        assert h1 != h2
