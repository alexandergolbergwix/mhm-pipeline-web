# Caching stack — Tests pinning this block

> Up: [Caching stack](README.md)

- `backend/tests/unit/test_wikidata_mirror.py` — full snapshots, ranks, index replacement, stale data, bounded SQL, partial discovery, API failure, and reference-only candidates.
  A 1,200-query scope requires at most four SQL reads when the request cap is one.
- `backend/tests/unit/test_wikidata_mirror_qlever.py` — literal-safe SELECT batches, fixed transport, invalid responses, non-item URIs, row caps, error stops, durable resume, and cache separation.

- `backend/tests/test_inference_cache_helpers.py` — two-tier lookup, canonical hashing, skip-cache invalidation
- `backend/tests/unit/test_inference_cache.py` — `cache_batch_warm` returns hits
  keyed by canonical hash, omits misses and expired rows, and updates hit count
  and last-hit time for returned rows. An absent cache table cannot block a request;
  the helper preserves an upstream error.
- `backend/tests/test_scoped_cache.py` — Tier-2 key shapes, memory fallback, invalidation
- `backend/tests/test_extraction_entities_cache.py`, `test_extraction_entity_cache_invalidation.py` — run-scoped fingerprint/ETag + invalidation on mutation
- `backend/tests/test_research_summary_cache.py` — fingerprint-keyed research kinds
- `backend/tests/test_hmo_studio_coverage_router.py` — `test_coverage_restores_from_durable_db_cache_on_disk_miss`, `test_coverage_stale_db_cache_still_enqueues_rebuild` (Rule W-39)
