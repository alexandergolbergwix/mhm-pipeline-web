# Caching stack — How it works

> Up: [Caching stack](README.md)

### Tier diagram — global inference cache

```
cache_lookup_or_call(db, kind, query_summary, fetch)
        │  query_hash = sha256(canonical JSON, sorted keys, ensure_ascii=False)
        ▼
 1. Redis L1   GET ic:{kind}:{hash}        <1 ms   ── hit → return (no hit_count bump)
        ▼ miss
 2. Postgres L2  SELECT inference_cache     ── hit → backfill Redis, bump hit_count, return
        ▼ miss (or expired)
 3. fetch()    real call (Modal / VIAF / Wikidata / Gemini / …)
        │  write BOTH tiers (errors swallowed, never break the caller)
        └─ None / empty-list results are NOT cached on a regular miss
```

`skip_cache=True` bypasses both read tiers but **always writes back**; if the
force-refresh returns nothing it overwrites the Postgres row with `{}` and
deletes the Redis key so a stale positive can never be served again
(`inference_cache.py:242-275`). Errors from `fetch` propagate and nothing is
written. `_strip_volatile` drops timestamp/nonce-style keys before hashing so
per-call noise never fragments the key (`inference_cache.py:121-144`).

`write_to_inference_cache` / `read_from_inference_cache` are the fetch-less
variants used around subprocess inference (eval-agent SSE verdict runs), where
wrapping the run in a `fetch()` callable is impractical.

### TTL table per kind (code is authoritative — `inference_cache.py:77-116`)

| Kind | Postgres TTL | Redis TTL |
|---|---|---|
| `ner.person` / `ner.provenance` / `ner.contents` | never expires | never expires |
| `genre.classify` | never expires | never expires |
| `authority.mazal` | 90 d | 24 h |
| `authority.viaf` | 30 d | 24 h |
| `authority.wikidata` | 30 d | 24 h |
| `authority.kima` | 180 d | 24 h |
| `ai_verdict` | 90 d | 7 d |
| `wikidata.person_place` | 180 d | 24 h |
| `wikidata.label` | 90 d | 7 d |
| `research.provenance_map` / `research.manuscripts` | 30 d | 24 h |
| `research.summary` | 7 d | 3 d |
| `research.movement` / `research.movement_facets` | 30 d | 24 h |

(The CLAUDE.md Rule W-25 summary predates the `research.*` / `wikidata.*`
kinds — the tables above reflect the current code.)

### Redis client

`get_redis()` (`redis_client.py:15`) is a lazy module-level singleton: no
`REDIS_URL` (env or settings) → returns `None` and every caller silently
degrades to Postgres-only / memory-only. Heroku's self-signed `rediss://`
cert is handled with `ssl_cert_reqs=None`. `close_redis()` runs in the
FastAPI lifespan teardown (`app/main.py:62`). The slowapi rate limiter shares
the same Redis via `RATELIMIT_STORAGE_URI`.

### Tier-2 scoped cache

`scoped_cache.py` handles context-dependent read models with key shapes
`rm:{run_id}:{kind}:{hash}`, `pm:{project_id}:…`, `u:{user_id}:…`. Lookup is
Redis → in-process memory dict (the memory tier is the CI/dev fallback).
There is **no Postgres tier** here — these are cheap-to-rebuild responses.
`invalidate_scope` SCAN-deletes by prefix; `extraction_entities_cache.py`
uses it (`kind="extraction.entities"`, run scope) with an approvals+NER-mtime
fingerprint that doubles as the HTTP ETag.

### Fingerprint-keyed durable build caches

All four follow the same shape: one Postgres row per run (or run+mode), a
`input_fingerprint CHAR(64)` column, and the full result stored as JSONB/Text
so the router can answer without recomputing anything.

| Cache | Key | Fingerprint covers | Stale behaviour |
|---|---|---|---|
| `WikidataStudioCache` | `(run_id, approved_only)` | control numbers, match approvals + payloads, NER overrides, item overrides, HMO instance QIDs (`wikidata_studio.py:84`) | serve cached + `cache_stale`; curator clicks Rebuild (`?force_rebuild=true` bypasses lookup, still writes back) |
| `HmoStudioItemCache` | `run_id` | RDF TTL bytes + schema-mapping version (count + max created_at of schema-level mappings, `hmo_item_build.py:53`) | rebuild; a schema bootstrap invalidates every run at once |
| `HmoCoverageCache` | `run_id` | RDF TTL bytes (`hmo_studio.py:405`) | `GET /coverage` restores from DB and re-seeds the on-disk file before enqueueing the 9–14 min rebuild job |
| `RdfArtifact` | `run_id` (PK) | n/a — it *is* the artifact (TTL text + counts) | re-seeds `backend/state/runs/{run_id}/…` on disk miss (`rdf_build.py:900`) |

Invalidation is never manual: changing any input changes the fingerprint,
and the next request notices.
