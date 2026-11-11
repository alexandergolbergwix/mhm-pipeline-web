# Review page reads

> Up: [Wikidata Studio](README.md) · Rule W-309

**R162 — A warm page must not read the complete cached corpus.**
`backend/app/pipeline/wikidata_item_row_views.py` checks only the cache ID before the row count.
Existing item rows return without a `result_items` or QuickStatements read.
An initial backfill reads `result_items` once.
The all-matches scope retains its approved-build fallback when it has no cache.
A missing build remains an error even if item rows still exist.

`test_wikidata_row_fallback.py` checks the actual SQL on a warm read.
It also checks the initial fallback and orphan-row behavior.
The frontend keeps its 100-row chunks and bounded body-decode retries.
