# Caching stack — Tests pinning this block

> Up: [Caching stack](README.md)

- `backend/tests/test_inference_cache_helpers.py` — two-tier lookup, canonical hashing, skip-cache invalidation
- `backend/tests/test_scoped_cache.py` — Tier-2 key shapes, memory fallback, invalidation
- `backend/tests/test_extraction_entities_cache.py`, `test_extraction_entity_cache_invalidation.py` — run-scoped fingerprint/ETag + invalidation on mutation
- `backend/tests/test_research_summary_cache.py` — fingerprint-keyed research kinds
- `backend/tests/test_hmo_studio_coverage_router.py` — `test_coverage_restores_from_durable_db_cache_on_disk_miss`, `test_coverage_stale_db_cache_still_enqueues_rebuild` (Rule W-39)
