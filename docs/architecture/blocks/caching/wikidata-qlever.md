# QLever candidate discovery

> Up: [Local mirror](wikidata-mirror.md) · R14 · W-339

The mirror tool accepts `--discovery-provider qlever` for an explicit alternate public index.
The default remains `wikidata`, which uses the Action API search.
The tool never changes the provider after a failure.
No new job, migration, credential, or environment variable is required.

## Query contract

`wikidata_mirror_qlever.py` sends fixed SELECT queries to `https://qlever.dev/api/wikidata`.
Each query has one predicate and at most 50 source seeds.
Strong identifier seeds use P3959, P214, P8189, P244, or P227.
Name and title seeds use labels, aliases, and P1476.
Those queries include Hebrew, English, and untagged literals.
The builder escapes every source string as a literal.
The query has no class, author, or holder join.
The local mirror applies those filters after the full entity read.
The remote query has no FILTER, UNION, DISTINCT, ORDER BY, or SERVICE.

Only exact Wikidata Q-item URIs become candidates.
The parser counts and excludes lexemes and other URIs.
An invalid or incomplete result stops the operation.
The transport sends no wiki credential, cookie, proxy credential, or edit request.
It waits at least two seconds between requests and stops at the first provider error.
It never follows a redirect or repeats a failed request automatically.

## Limits and evidence

Each query has `LIMIT 1001`.
A response at that limit saves `query_capped`, retains its candidates, and remains partial.
A normal resume retains that scope without the same incomplete query again.
`--refresh` permits an explicit new read.
The operator must narrow the input or inspect the capped scope to obtain more evidence.
The tool does not use an offset without a stable order.
`--max-queries` bounds requests; each QLever query has one response page.
The existing groups of 500 bound the saved scope reads.

The shared `wikidata.mirror_search` cache retains the provider endpoint in its key.
QLever scopes also include the endpoint and a separate schema version.
The saved observation time records the response time, not the age of the provider index.
The provider index date remains unverified.
An empty result never proves global absence.
Query completion never grants identity approval or edit permission.

The normal Action API still supplies complete entity documents in groups of at most 50.
That read retains `maxlag=5`.
Local filters use the full statements, ranks, qualifiers, and references.
The fresh CREATE and UPDATE checks remain unchanged.
Each Gila repair requires entity creation by user ID `6935067`.
Foreign-created QIDs remain read-only reference targets.
This option does not resolve a public write refusal from `maxlag`.

## Operator command

```sh
backend/.venv/bin/python backend/scripts/sync_wikidata_mirror.py \
  --database-file state/wikidata-mirror/mirror.sqlite \
  --items state/studio-items.json --discovery-provider qlever \
  --max-queries 10 --output state/mirror-sync.json
```

Use `--database-from-heroku` instead of `--database-file` for the production mirror.
Repeat the same command to resume fresh saved scopes.
The command writes only the local mirror and shared cache.
It performs no public edit or source correction.

Tests: `backend/tests/unit/test_wikidata_mirror_qlever.py` and `test_wikidata_mirror.py`.
