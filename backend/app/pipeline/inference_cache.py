"""Universal inference cache helper — two-tier: Redis L1 → Postgres L2.

One function (:func:`cache_lookup_or_call`) every cache-able call site
goes through. Lookups are keyed by ``(kind, sha256(canonical_json))``
and shared across users — first call across the team populates the
cache for everyone else.

Two-tier lookup order
---------------------
1. **Redis L1** (sub-millisecond in-memory): keyed ``ic:{kind}:{hash}``.
   On hit → return immediately, no Postgres round-trip.
2. **Postgres L2** (durable): the ``inference_cache`` table.
   On hit → backfill Redis, return.
3. **fetch()** (real call to Modal / VIAF / Wikidata / …):
   write to both tiers, return.

When ``REDIS_URL`` (or ``redis_url`` in settings) is absent, ``get_redis()``
returns ``None`` and the cache behaves exactly as before (Postgres only).
The Redis tier is therefore fully optional — dev/CI work without it.

Redis TTL policy
----------------
``ner.*`` and ``genre.classify`` — no expiry (content-addressed).
``ai_verdict`` — 7-day Redis hot window; Postgres holds a 90-day ground truth.
``authority.mazal`` — 24 h Redis hot window; Postgres holds 90-day ground truth.
``authority.viaf`` / ``authority.wikidata`` — 24 h Redis; 30-day Postgres.
``authority.kima`` — 24 h Redis; 180-day Postgres (geographic index rarely changes).

Hit-count accounting
--------------------
``hit_count`` / ``last_hit_at`` are updated only when Postgres is actually
read (L2 hit or write path). Redis-only hits skip the UPDATE intentionally —
this eliminates the per-warm-hit Postgres write that was the primary
latency cost of the old single-tier implementation.

Skip-cache
----------
``skip_cache=True`` bypasses both read tiers and **always writes back**:

* Non-empty result → replaces any existing row + resets ``expires_at`` to
  ``now + TTL``, so the stored value stays in sync with the latest upstream
  answer.
* None / empty result → deletes the Redis key and overwrites the Postgres
  row with ``{}`` so a stale positive match can never be served again after
  a force-refresh that found nothing.

Regular misses (``skip_cache=False``) still skip caching None / empty-list
results to avoid storing transient model errors.

Errors are never cached. ``fetch`` may raise; the exception propagates
unchanged and nothing is written to either tier.
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


# ── Per-kind TTL ──────────────────────────────────────────────────────

# Postgres TTL — None = never expires.
# Authority calls expire because upstream registries mutate over time.
# ai_verdict expires after 90 days to force a refresh when the model changes.
KIND_TTL: dict[str, timedelta | None] = {
    "ner.person":         None,
    "ner.provenance":     None,
    "ner.contents":       None,
    "genre.classify":     None,
    "authority.mazal":    timedelta(days=90),   # Mazal index changes slowly
    "authority.viaf":     timedelta(days=30),   # VIAF clusters merge often
    "authority.wikidata": timedelta(days=30),   # Wikidata is actively edited
    "authority.kima":     timedelta(days=180),  # geographic index rarely changes
    "ai_verdict":         timedelta(days=90),   # forces refresh when model changes
    # Research / provenance-map feature (DB-sourced, fingerprint-keyed):
    "wikidata.person_place": timedelta(days=180),  # biographical place rarely changes
    "research.provenance_map": timedelta(days=30),  # backstop; key carries fingerprint
    "research.manuscripts":    timedelta(days=30),  # backstop; key carries fingerprint
    "research.summary":        timedelta(days=7),   # invalidated by run-set fingerprint change
    "research.movement":       timedelta(days=30),  # full corpus; fingerprint-invalidated
    "research.movement_facets": timedelta(days=30), # facets; fingerprint-invalidated
    "wikidata.label":           timedelta(days=90),  # labels rarely change
    # A stale "absent" is how a duplicate slips through later, so existence
    # answers expire quickly; MARC prose does not change and the key already
    # carries the record text + model (Rule W-140).
    "wikidata.duplicate_probe": timedelta(days=7),
    "marc.llm_extract":         timedelta(days=90),
}

# Redis TTL in seconds — None = no expiry (SET without EX).
# Authority kinds use a 24 h hot-window; Postgres remains the ground truth.
_REDIS_TTL_SECONDS: dict[str, int | None] = {
    "ner.person":         None,
    "ner.provenance":     None,
    "ner.contents":       None,
    "genre.classify":     None,
    "authority.mazal":    86_400,
    "authority.viaf":     86_400,
    "authority.wikidata": 86_400,
    "authority.kima":     86_400,
    "ai_verdict":         86_400 * 7,   # 7-day Redis hot window for verdicts
    "wikidata.person_place":   86_400,
    "research.provenance_map": 86_400,
    "research.manuscripts":    86_400,
    "research.summary":        86_400 * 3,  # 3-day Redis window; invalidated by fingerprint
    "research.movement":       86_400,      # 1-day Redis window; invalidated by fingerprint
    "research.movement_facets": 86_400,     # 1-day Redis window; invalidated by fingerprint
    "wikidata.label":           86_400 * 7, # 7-day Redis hot window
    "wikidata.duplicate_probe": 86_400,      # 1-day Redis window on existence
    "marc.llm_extract":         86_400 * 7,  # 7-day Redis window on extraction
}


# ── Public helpers ────────────────────────────────────────────────────

_VOLATILE_PAYLOAD_KEYS = frozenset({
    "timestamp", "timestamp_ms", "date", "datetime", "created_at",
    "updated_at", "judged_at", "requested_at", "fetched_at", "now",
    "time", "nonce", "request_id", "trace_id",
})


def _strip_volatile(value: Any) -> Any:
    """Recursively drop keys that vary per-call but not per-answer.

    Dates/timestamps/nonces/request ids a caller may stamp onto an
    otherwise-identical payload must not fragment the cache key — two
    calls with the same endpoint + logical payload should always hash
    the same regardless of when they were made.
    """
    if isinstance(value, dict):
        return {
            k: _strip_volatile(v)
            for k, v in value.items()
            if k.lower() not in _VOLATILE_PAYLOAD_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def endpoint_query_summary(*, endpoint: str, payload: Any) -> dict[str, Any]:
    """Build a ``query_summary`` keyed by endpoint URL + payload.

    Use this for straightforward external HTTP lookups where "same
    endpoint + same logical payload → same answer" holds — i.e.
    idempotent reads (label lookups, SPARQL queries, reference-data
    fetches). Do NOT use it for anything that must observe live state
    at write-time (Wikidata reconciliation right before a create/merge,
    security checks like Turnstile) — caching those would let a stale
    answer silently drive a decision that must always see fresh data.
    """
    return {"endpoint": str(endpoint), "payload": _strip_volatile(payload)}


async def cache_http_call(
    db: AsyncSession,
    *,
    kind: str,
    endpoint: str,
    payload: Any,
    fetch: Callable[[], Awaitable[Any]],
    user_id: uuid.UUID | None = None,
    skip_cache: bool = False,
) -> Any:
    """``cache_lookup_or_call``, but the key is derived from *endpoint* +
    *payload* instead of a hand-built ``query_summary``.

    This is the "most external API calls should be cached" entry point:
    call sites that just need "endpoint + payload → cached answer"
    without hand-rolling their own summary dict should use this instead
    of building ``query_summary`` themselves.
    """
    return await cache_lookup_or_call(
        db,
        kind=kind,
        query_summary=endpoint_query_summary(endpoint=endpoint, payload=payload),
        fetch=fetch,
        user_id=user_id,
        skip_cache=skip_cache,
    )


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
    """Lookup-or-compute with two-tier cache (Redis L1 → Postgres L2).

    *fetch* is awaited only on a full miss (or when ``skip_cache=True``).
    Successful results are always written to both tiers; errors propagate
    unchanged and nothing is written.
    """
    from app.cache.redis_client import get_redis  # noqa: PLC0415

    query_hash = canonical_hash(query_summary)
    redis_key  = f"ic:{kind}:{query_hash}"
    now        = datetime.now(timezone.utc)
    redis      = await get_redis()

    if not skip_cache:
        # ── L1: Redis ────────────────────────────────────────────────
        if redis is not None:
            try:
                raw = await redis.get(redis_key)
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("redis L1 GET failed (kind=%s): %s", kind, exc)

        # ── L2: Postgres ─────────────────────────────────────────────
        hit = await _read(db, kind=kind, query_hash=query_hash, now=now)
        if hit is not None:
            # Backfill L1 so the next call doesn't touch Postgres.
            if redis is not None:
                await _redis_set(redis, redis_key, kind, hit)
            return hit

    # ── Miss (or skip_cache) — run the real call ──────────────────────
    result = await fetch()

    # When skip_cache=True we ALWAYS write back so the stored value stays
    # in sync with the latest upstream answer.  On a regular miss we skip
    # None / empty-list to avoid caching transient model errors.
    if not skip_cache:
        if result is None:
            return result
        if isinstance(result, list) and not result:
            return result
    else:
        # Force-refresh: if the upstream returned nothing, invalidate any
        # stale positive match that may be in the store.
        if result is None or (isinstance(result, list) and not result):
            expires_at = _pg_expires_at(kind, now)
            try:
                await _write(
                    db, kind=kind, query_hash=query_hash,
                    query_summary=query_summary,
                    result=result if result is not None else {},
                    user_id=user_id, now=now, expires_at=expires_at,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "inference_cache skip_cache invalidation failed (kind=%s): %s",
                    kind, exc,
                )
            if redis is not None:
                try:
                    await redis.delete(redis_key)
                except Exception:  # noqa: BLE001
                    pass
            return result

    # Write both tiers concurrently (errors are swallowed — must not
    # break the caller).
    if redis is not None:
        await _redis_set(redis, redis_key, kind, result)
    expires_at = _pg_expires_at(kind, now)
    try:
        await _write(
            db, kind=kind, query_hash=query_hash,
            query_summary=query_summary, result=result,
            user_id=user_id, now=now, expires_at=expires_at,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("inference_cache postgres write failed (kind=%s): %s", kind, exc)
    return result


# ── Internals ─────────────────────────────────────────────────────────


async def _redis_set(redis: Any, key: str, kind: str, value: Any) -> None:
    """Write a value to Redis with the appropriate TTL for *kind*."""
    try:
        raw = json.dumps(value, ensure_ascii=False)
        ttl = _REDIS_TTL_SECONDS.get(kind)
        if ttl is None:
            await redis.set(key, raw)
        else:
            await redis.set(key, raw, ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis L1 SET failed (kind=%s): %s", kind, exc)


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
        return None
    # Bump hit accounting (best-effort — only on Postgres reads).
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


def _pg_expires_at(kind: str, now: datetime) -> datetime | None:
    ttl = KIND_TTL.get(kind)
    return now + ttl if ttl is not None else None


async def write_to_inference_cache(
    db: AsyncSession,
    *,
    kind: str,
    query_summary: dict[str, Any],
    result: Any,
    user_id: uuid.UUID | None = None,
) -> None:
    """Write a result directly to both cache tiers without a fetch() callable.

    Used after subprocess-based inference (eval-agent AI verdicts) where the
    result is already known — the standard cache_lookup_or_call flow would
    require wrapping the entire subprocess in a fetch() which is impractical
    for streaming SSE runners.

    Silently swallows write errors (same contract as cache_lookup_or_call).
    """
    from app.cache.redis_client import get_redis  # noqa: PLC0415

    if result is None:
        return
    if isinstance(result, list) and not result:
        return

    query_hash = canonical_hash(query_summary)
    redis_key = f"ic:{kind}:{query_hash}"
    now = datetime.now(timezone.utc)
    redis = await get_redis()

    if redis is not None:
        await _redis_set(redis, redis_key, kind, result)
    expires_at = _pg_expires_at(kind, now)
    try:
        await _write(
            db, kind=kind, query_hash=query_hash,
            query_summary=query_summary, result=result,
            user_id=user_id, now=now, expires_at=expires_at,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("inference_cache direct write failed (kind=%s): %s", kind, exc)


async def read_from_inference_cache(
    db: AsyncSession,
    *,
    kind: str,
    query_summary: dict[str, Any],
) -> Any:
    """Read directly from the cache (Redis L1 → Postgres L2) without fetch().

    Returns the cached result, or None on miss / expired entry.
    Used for pre-checking before spawning an expensive subprocess so we can
    skip the call entirely for already-verified entities.
    """
    from app.cache.redis_client import get_redis  # noqa: PLC0415

    query_hash = canonical_hash(query_summary)
    redis_key = f"ic:{kind}:{query_hash}"
    now = datetime.now(timezone.utc)
    redis = await get_redis()

    if redis is not None:
        try:
            raw = await redis.get(redis_key)
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis L1 GET failed (kind=%s): %s", kind, exc)

    return await _read(db, kind=kind, query_hash=query_hash, now=now)


__all__ = [
    "KIND_TTL",
    "cache_http_call",
    "cache_lookup_or_call",
    "canonical_hash",
    "endpoint_query_summary",
    "read_from_inference_cache",
    "write_to_inference_cache",
]
