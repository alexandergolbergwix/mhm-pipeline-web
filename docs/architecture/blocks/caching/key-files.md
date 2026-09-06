# Caching stack — Key files

> Up: [Caching stack](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/inference_cache.py` | `cache_lookup_or_call`, `cache_http_call`, `canonical_hash`, `KIND_TTL`, `_REDIS_TTL_SECONDS`, direct read/write helpers |
| `backend/app/pipeline/ai_verdict_cache_common.py` | Shared normalization and volatile-metadata stripping for content fingerprints (Rule W-208) |
| `backend/app/cache/redis_client.py` | `get_redis()` lazy singleton (returns `None` without `REDIS_URL`), `close_redis()` |
| `backend/app/cache/scoped_cache.py` | Tier-2 scoped cache: `scoped_cache_lookup_or_call`, `invalidate_scope`, key prefixes `ic:/pm:/rm:/u:` |
| `backend/app/cache/cache_policy.py` | TTL registry for scoped kinds (`extraction.entities`, `dashboard.projects`, `wikidata.studio`) |
| `backend/app/models/inference_cache.py` | `InferenceCache` row: kind, query_hash, query_summary, result, hit_count, expires_at |
| `backend/app/models/wikidata_studio_cache.py` | `WikidataStudioCache` — one row per `(run_id, approved_only)`, migration 0015 |
| `backend/app/models/hmo_studio_item_cache.py` | `HmoStudioItemCache` — one row per run; fingerprint covers RDF bytes + schema-mapping version |
| `backend/app/models/hmo_coverage_cache.py` | `HmoCoverageCache` — durable counterpart of the on-disk coverage JSON (migration 0032, Rule W-39) |
| `backend/app/models/rdf_artifact.py` | `RdfArtifact` — the run's built TTL persisted in Postgres (restores on-disk TTL after dyno recycle, see `rdf_build.py:900`) |
| `backend/app/pipeline/wikidata_studio.py:84` | `compute_build_fingerprint` — SHA-256 over records + matches + entities + overrides + `approved_only` + HMO instance QIDs |
| `backend/app/pipeline/hmo_item_build.py:42` | `compute_hmo_build_fingerprint` — SHA-256 over `ttl_hash : schema_mapping_version` |
| `backend/app/pipeline/hmo_studio.py:405` | `compute_coverage_fingerprint` (TTL bytes) + `save_coverage_to_db` |
| `backend/app/pipeline/extraction_entities_cache.py` | Run-scoped fingerprint + ETag for `GET /extraction/entities`; `invalidate_entities_cache` |
| `backend/app/pipeline/ner_verdict_cache.py` | Content keys for `kind=ai_verdict` (`ner_verdict_query_summary`, `sanitise_stale_ai_verdict`) |
| `backend/app/pipeline/hmo_item_verdict_cache.py` | HMO item `ai_verdict` keys + `sanitise_stale_hmo_item_verdict` (entity_type, MARC slice, schema salt) |
| `backend/app/pipeline/marc_verify_context.py` | Multi-CN MARC merge/project for verify cache fingerprints |
| `backend/app/jobs/prune_inference_cache.py` | Deletes rows with `expires_at < now()`; NULL-TTL rows never touched |
| `backend/scripts/run_prune_inference_cache.py` | Heroku Scheduler entrypoint, daily 02:05 UTC |

[Automatic resolution](../wikidata-studio/automatic-resolution.md) lists the worker, evidence cache, structured verdict, subset, and tests.
