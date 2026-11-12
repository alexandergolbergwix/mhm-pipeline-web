# Wikidata Studio - Cache Freshness and Overrides

> Up: [Wikidata Studio](README.md) | Related: [Build and cache](build-and-cache.md)

## Build fingerprint

`compute_build_fingerprint` (`backend/app/pipeline/wikidata_studio.py`) hashes
the records, matches, entities, and item overrides. Each override contributes
its `local_id`, its `approved` flag, and a hash of its content fields.

The content hash includes `accepted_foreign_qid`. A changed accepted QID changes
the QID that the builder emits, so the cache becomes stale.

The `approved` flag is not inside the content hash. The top-level SHA-256 still
covers it, so an approval change also makes the cache stale. `ai_verdict` is
not part of the build fingerprint.

## Overrides apply once

The legacy build runs with `overrides={}`. The canonical stage applies each
override once, after it merges the legacy items
(`build_canonical_studio_result` in `backend/app/pipeline/hmo_canonical_wikidata.py`).

The fan-out orchestrator applies each override once, after `merge_shard_items`
(`backend/app/pipeline/wikidata_studio_build_shard.py`). The built cache row
then stores `overrides_applied: true` (`backend/app/routers/wikidata_studio.py`).

Read paths do not apply the full override again. For a cache with
`overrides_applied`, they re-apply only the keys in `_CACHE_REAPPLY_KEYS`
(`backend/app/pipeline/wikidata_item_views.py`): labels, descriptions, aliases,
`accepted_foreign_qid`, `ai_verdict`, and `ai_verdict_at`.

## Rebuild required

`_content_override_newer_than_build` (`wikidata_item_views.py`) returns true when
both conditions hold:

- the override changes content: labels, descriptions, aliases, `add_statements`,
  `remove_statements`, or `statement_edits`; and
- the override `updated_at` is later than the cache `built_at`.

Approval edits and verdict-only edits never set the flag. The flag applies only
when the cache has `overrides_applied`.

Both review paths set `rebuild_required` from the same rule:

- Merged path: `fetch_merged_wikidata_items` in `wikidata_item_views.py`.
- Paged path: `page_wikidata_items` in `wikidata_item_row_views.py` (Rule W-268).

The review table (`frontend/src/components/wikidata/WikidataItemTable.tsx`)
shows the **Rebuild required** badge when the flag is true. The badge tells the
curator to rebuild. It does not apply the edit. The upload preparation route
returns 409 when the cache is not current, so a rebuild is needed before upload.

## Tests

- `backend/tests/unit/test_wikidata_item_views_cache_overrides.py`
- `backend/tests/unit/test_wikidata_row_rebuild_required.py` (merged and paged parity)
- `backend/tests/test_wikidata_cache_freshness_gate.py`
- `backend/tests/unit/test_wikidata_studio_sharding.py`
- `backend/tests/unit/test_wikidata_canonical_reuse.py`

## Canonical reuse stamp

The canonical fingerprint re-projects every canonical entity. This took about 69 seconds for run `47e03437`. A stamp lets the check skip that work when the rows did not change.

- `_canonical_stamp` reads one aggregate row: `count|max id|max updated_at` for the run.
- The build reads the stamp before it loads the canonical rows. A resumed build that already loaded rows stores no stamp.
- The build stores two summary keys: `canonical_reuse_key` is `<stamp>|<enrichment fingerprint>`, and `canonical_reuse_fingerprint` is the canonical fingerprint.
- `_canonical_fingerprint_for_check` reuses the stored fingerprint only when the key matches the current stamp and enrichment fingerprint. Otherwise it runs the full recompute.

Fail-safe: a cache without these keys takes the full recompute. The shard build path (`backend/app/pipeline/wikidata_studio_build_shard.py`) does not write the keys yet, so its caches always take the full recompute.

Risk: a canonical row that changes without `updated_at` changing is missed. The ORM sets `updated_at` on every update, but raw SQL that skips the ORM can bypass it.

## Time budget

- HTTP routes check freshness inline with a 25-second budget (`_STUDIO_FRESHNESS_TIMEOUT_SECONDS`). Heroku closes requests after 30 seconds.
- Background upload jobs use `STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS` (300 seconds). The full check took 73.6 seconds on a Heroku dyno for run `47e03437`. The fingerprint alone takes under 1 second. The item digest and canonical fingerprint take most of the time.
- A timeout still fails closed with HTTP 409 and no write.

See [source repair safeguards](source-repair-safeguards.md) for post-override cleanup and the optional prose parameter.
