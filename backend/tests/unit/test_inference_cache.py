"""Tests for the two-tier inference cache helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.inference_cache import InferenceCache
from app.pipeline.inference_cache import (
    cache_batch_warm,
    canonical_hash,
    write_to_inference_cache,
)

KIND = "wikidata.duplicate_probe"


async def test_cache_batch_warm_returns_hits_and_skips_misses(db_session) -> None:
    """One SELECT per 500 keys: hits come back keyed by hash, misses absent."""
    summaries = [
        {"endpoint": "https://x/w/api.php?a=1", "payload": {}},
        {"endpoint": "https://x/w/api.php?a=2", "payload": {}},
    ]
    bodies = [
        {"query": {"search": [{"title": "Q1"}]}},
        {"query": {"search": []}},
    ]
    for summary, body in zip(summaries, bodies, strict=True):
        await write_to_inference_cache(
            db_session, kind=KIND, query_summary=summary, result=body,
        )

    missing = {"endpoint": "https://x/w/api.php?a=3", "payload": {}}
    found = await cache_batch_warm(
        db_session, kind=KIND, summaries=[*summaries, missing],
    )

    assert found[canonical_hash(summaries[0])] == bodies[0]
    assert found[canonical_hash(summaries[1])] == bodies[1]
    assert canonical_hash(missing) not in found

    rows = (
        await db_session.execute(select(InferenceCache).where(InferenceCache.kind == KIND))
    ).scalars().all()
    assert len(rows) == 2
    for row in rows:
        assert (row.hit_count or 0) >= 1
        assert row.last_hit_at is not None


async def test_cache_batch_warm_skips_expired_rows(db_session) -> None:
    summary = {"endpoint": "https://x/w/api.php?e=1", "payload": {}}
    await write_to_inference_cache(
        db_session, kind=KIND, query_summary=summary, result={"ok": True},
    )
    row = (
        await db_session.execute(select(InferenceCache).where(InferenceCache.kind == KIND))
    ).scalars().one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    found = await cache_batch_warm(db_session, kind=KIND, summaries=[summary])
    assert found == {}
