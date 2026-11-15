# Local Wikidata candidate mirror

> Up: [Caching](README.md) · [Wikidata Studio](../wikidata-studio/README.md) · W-339/W-340

The mirror stores public candidate data in Postgres.
Local SQL and Python apply exact filters to that data.
Foreign-created items remain valid candidates and reference targets.
The mirror never grants permission to edit an item.

## Data contract

Migration `0056_wikidata_mirror` adds three tables.
`WikidataMirrorEntity` retains QID, revision, full JSON, content hash, observation time, and source.
The JSON retains labels, aliases, descriptions, statements, ranks, qualifiers, references, and sitelinks.
`WikidataMirrorFact` indexes active value snaks by property, value hash, and QID.
The text value also participates in each equality check.
The special `@name` index contains titles, labels, and aliases.
Deprecated statements and non-value snaks stay in JSON but cannot supply an active index fact.
`WikidataMirrorScope` retains scope status, requested count, stored count, cursor, and unresolved cases.

Snapshot replacement and fact replacement share one transaction.
A lower revision or an older observation cannot replace a newer snapshot.
Conflicting content at the same revision cannot replace the retained content.
A complete missing-item API snapshot invalidates only a local positive with an earlier observation.
A failed or incomplete response cannot invalidate that positive.

## Discovery and local filters

`wikidata_mirror_sync.py` uses broad identifier unions and quoted name or title seeds.
It uses native `haswbstatement:"P214=id|P244=id"` syntax, with quotes after the colon.
See the [official search syntax](https://www.mediawiki.org/wiki/Help:Extension:WikibaseCirrusSearch#haswbstatement).
It performs class, holder, shelfmark, and other conjunction checks locally.
It includes candidates without a creator filter.
The search cursor survives a page cap and a process restart.
The client reads saved query scopes in groups of 500 before it applies the request cap.
Fresh candidates from a completed query remain available beyond that cap.
The scope records partial results; query completion never means complete Wikidata coverage.
Entity reads request complete payloads in groups of at most 50 QIDs.
The client uses no wiki credential and has no edit operation.
It retains `maxlag=5` and stops after an upstream error.

New HTTP calls use `cache_http_call` and release the SQL transaction before the call.
An absent cache table cannot block a public read in the local SQLite database.
The shared helper logs the cache failure and releases its transaction before the request.
`wikidata.mirror_entities` and `wikidata.mirror_search` have a one-day Postgres TTL and a one-hour Redis TTL.
The payload retains its original observation time after a cache hit.
A normal refresh reuses entity snapshots younger than one day.
`--refresh` bypasses this reuse and the HTTP cache.

Duplicate probes read the local index in batches.
They inspect full candidate documents in groups of 100 and recheck the matched facts.
The lookup retains at most 100 candidates per anchor and always reports partial coverage.
A positive local match can avoid another remote probe for that key.
A local miss never proves global absence; the existing remote fallback remains active.
Local-only candidates require current identity confirmation before adoption or a reference choice.
The normal source-choice and public write checks still apply.

Before CREATE, the uploader retains its fresh existence check.
Before UPDATE, it retains fresh creator, source, revision, and exact-claim checks.
The Gila repair scope still requires creator user ID `6935067`.
An owned item may reference a verified foreign QID.
No foreign QID receives an edit or merge through the mirror.

## Operator commands

Use `backend/scripts/sync_wikidata_mirror.py`.
`--database-file` creates only the mirror tables in an explicit local SQLite file.
Production schema changes still run only through the release migration.

```sh
backend/.venv/bin/python backend/scripts/sync_wikidata_mirror.py \
  --database-file state/wikidata-mirror/mirror.sqlite \
  --snapshot state/public-snapshot.json --output state/mirror-import.json
```

The snapshot needs an `entities` map and `completed_at`.
The importer uses `started_at` as a conservative observation time when present.
It records redirect identity differences for review and skips their documents.
Its source hash and cursor support a later resume of the same input.

```sh
backend/.venv/bin/python backend/scripts/sync_wikidata_mirror.py \
  --database-from-heroku --items state/studio-items.json \
  --max-queries 100 --max-pages 5 --output state/mirror-sync.json
```

The item file contains an item list or an `items` object.
`--qids` accepts a JSON list of known QIDs without a search.
`--filter P195=Q123 --filter P217=Ms.1` applies a local AND query.
`--with-document` exports full matching snapshots; `--include-stale` permits explicit historical review.
The default candidate age limit is seven days.
These commands never write to Wikidata and never create a schedule or a job kind.

Tests: `backend/tests/unit/test_wikidata_mirror.py` and the duplicate, cache, and creator guard suites.
