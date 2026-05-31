"""Universal inference cache helper.

One function (:func:`cache_lookup_or_call`) every cache-able call site
goes through. Lookups are keyed by ``(kind, sha256(canonical_json))``
and shared across users — first call across the team populates the
cache for everyone else.

Per-kind TTL is configured in :data:`KIND_TTL`. NER + Genre + AI
verdicts never expire (the model output is fully determined by the
input + model id, so reproducibility is the same as the cache key).
Authority queries (Mazal / VIAF / Wikidata / KIMA) expire after 30
days because the upstream registries mutate over time.

Skip-cache: pass ``skip_cache=True`` to force a fresh call. The
result is STILL written back into the cache so the NEXT call
warm-hits (so refreshing N times only pays for the N+1th time, not
N times).

Errors are never cached. ``fetch`` may raise; the exception
propagates out unchanged + nothing is written.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inference_cache import InferenceCache

logger = logging.getLogger(__name__)


# Per-kind TTL. ``None`` = never expires (cache is forever — the
# upstream answer is fully determined by the input).
#
# Authority calls expire because the upstream sources (Wikidata, VIAF)
# mutate over time: people get added, IDs deleted, labels corrected.
# 30 days is the conservative balance — medieval Hebrew authority
# records don't change often, but they do.
KIND_TTL: dict[str, timedelta | None] = {
    "ner.person":         None,
    "ner.provenance":     None,
    "ner.contents":       None,
    "genre.classify":     None,
    "authority.mazal":    timedelta(days=30),
    "authority.viaf":     timedelta(days=30),
    "authority.wikidata": timedelta(days=30),
    "authority.kima":     timedelta(days=30),
    "ai_verdict":         None,
}


def canonical_hash(query_summary: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of *query_summary*.

    Canonical = sorted keys + no extra whitespace + ensure_ascii=False
    (Hebrew survives). Two semantically-equal inputs always hash to the
    same digest regardless of key insertion order.
    """
    body = json.dumps(
        query_summary, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def cache_lookup_or_call(
    db: AsyncSession,
    *,
    kind: str,
    query_summary: dict[str, Any],
    fetch: Callable[[], Awaitable[Any]],
    user_id: uuid.UUID | None = None,
    skip_cache: bool = False,
) -> Any:
    """Lookup-or-compute. Returns the cached result OR calls ``fetch``.

    *fetch* is awaited only on a miss (or when ``skip_cache=True``).
    Successful results are always written back; errors propagate
    unchanged + nothing is written.

    Hit accounting (``hit_count`` + ``last_hit_at``) is updated on
    every cache read — useful for an admin-side cache-inspection UI
    later. Updates are best-effort: if the UPDATE races with
    concurrent eviction we silently log and move on.
    """
    query_hash = canonical_hash(query_summary)
    now = datetime.now(timezone.utc)

    if not skip_cache:
        hit = await _read(db, kind=kind, query_hash=query_hash, now=now)
        if hit is not None:
            return hit

    # Miss (or skip_cache). Run the real call.
    result = await fetch()

    # Don't cache None / empty errors — we can't tell the difference
    # between "model legitimately returned []" and "model errored
    # silently". Treat empty as a "don't poison the well" miss; only
    # cache non-empty results.
    if result is None:
        return result
    if isinstance(result, list) and not result:
        return result

    expires_at = _expires_at(kind, now)
    try:
        await _write(
            db, kind=kind, query_hash=query_hash,
            query_summary=query_summary, result=result,
            user_id=user_id, now=now, expires_at=expires_at,
        )
    except Exception as exc:  # noqa: BLE001 — cache write must never break the caller
        logger.warning("inference_cache write failed (kind=%s): %s", kind, exc)
    return result


# ── Internals ─────────────────────────────────────────────────────────


async def _read(
    db: AsyncSession, *, kind: str, query_hash: str, now: datetime,
) -> Any:
    row = (
        await db.execute(
            select(InferenceCache).where(
                InferenceCache.kind == kind,
                InferenceCache.query_hash == query_hash,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at < now:
        # Expired — treat as miss; the writer below will refresh.
        return None
    # Bump hit accounting (best-effort).
    try:
        row.hit_count = (row.hit_count or 0) + 1
        row.last_hit_at = now
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
    return row.result


async def _write(
    db: AsyncSession, *,
    kind: str, query_hash: str,
    query_summary: dict[str, Any], result: Any,
    user_id: uuid.UUID | None, now: datetime,
    expires_at: datetime | None,
) -> None:
    stmt = pg_insert(InferenceCache).values(
        kind=kind, query_hash=query_hash,
        query_summary=query_summary, result=result,
        created_by=user_id, hit_count=0,
        created_at=now, last_hit_at=now, expires_at=expires_at,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_inference_cache_key",
        set_={
            "query_summary": stmt.excluded.query_summary,
            "result":        stmt.excluded.result,
            "last_hit_at":   now,
            "expires_at":    expires_at,
        },
    )
    await db.execute(stmt)
    await db.commit()


def _expires_at(kind: str, now: datetime) -> datetime | None:
    ttl = KIND_TTL.get(kind)
    return now + ttl if ttl is not None else None


__all__ = [
    "KIND_TTL",
    "cache_lookup_or_call",
    "canonical_hash",
]
