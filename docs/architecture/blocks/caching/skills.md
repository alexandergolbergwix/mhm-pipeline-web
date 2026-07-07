# Caching stack — Skills

> Up: [Caching stack](README.md)

### Skill: add a new cached inference kind

1. Pick a namespaced kind string (`authority.newapi`, `research.newthing`).
2. Add it to `KIND_TTL` and `_REDIS_TTL_SECONDS` in
   `backend/app/pipeline/inference_cache.py` with an explicit TTL decision
   (comment the rationale, as the existing entries do).
3. At the call site, wrap the external call:
   `await cache_http_call(db, kind=..., endpoint=..., payload=..., fetch=...)`
   — or `cache_lookup_or_call` with a hand-built `query_summary` if the key
   is not "endpoint + payload". Never include timestamps in the summary.
4. Extend `backend/tests/test_inference_cache_helpers.py`.

### Skill: add a durable per-run build cache

1. Model: new file under `backend/app/models/` mirroring
   `hmo_coverage_cache.py` — `run_id` (unique), `input_fingerprint CHAR(64)`,
   result JSONB, `built_at`. Add an Alembic migration.
2. Fingerprint: a `compute_*_fingerprint` in the pipeline module hashing
   *every* input that affects the result (see `wikidata_studio.py:84` for a
   multi-source example, `hmo_item_build.py:42` for file+version).
3. Read path: check fingerprint before building; on match return cached, on
   mismatch rebuild (or return `cache_stale` if rebuilds are curator-gated).
4. Write path: upsert the row after every successful build — including
   force rebuilds. If there is also an on-disk artefact, write through to
   both (Rule W-39) and re-seed disk from DB on disk-miss.
5. Add a `test_*_restores_from_durable_db_cache_on_disk_miss`-style test.

### Skill: add a scoped (run/project/user) read-model cache

1. Add the kind to `SCOPED_REDIS_TTL_SECONDS` + `SCOPED_MEMORY_TTL_SECONDS`
   in `backend/app/cache/cache_policy.py`.
2. Call `scoped_cache_lookup_or_call(scope="run", scope_id=str(run_id), ...)`.
3. Call `invalidate_scope("run", str(run_id), kind=...)` from every mutation
   that changes the response (see `invalidate_entities_cache`).

### Skill: change a TTL

Edit `KIND_TTL` / `_REDIS_TTL_SECONDS` only. Existing Postgres rows keep
their old `expires_at` until rewritten or force-refreshed; the daily prune
(02:05 UTC, `scripts.run_prune_inference_cache`) reclaims expired rows. The
read path already treats expired rows as misses, so no backfill is needed.
