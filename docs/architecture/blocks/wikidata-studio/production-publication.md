# Wikidata Publication

> Up: [Wikidata Studio](README.md)

## Purpose

The Publication service is the only path for a production write to
`www.wikidata.org`. It turns a source snapshot into an immutable Release.
It then requires Review, Dry-run, and Publish in that order.

## Lifecycle

1. `prepare` creates a build job that reads the selected source and stores one normalized entity row per entity.
2. The service seals the Release with a canonical digest and persistent Findings.
3. `review` stores digest-bound Approval Decisions on the sealed Release.
4. `dry_run` reconciles the approved set and seals a Plan plus a short-lived Receipt.
5. `publish` creates a queued Execution only.
6. A worker resumes the Execution and writes one leased action at a time.

The API never writes to Wikidata during `prepare`, `review`, or `publish`.
The worker commits a Write Intent before each remote call.
It writes a receipt after the remote result.
An uncertain remote result remains `outcome_unknown` until recovery proves it.

## Gates

- A Source Snapshot must still be current before dry-run and publish.
- A Release with a blocking Finding cannot receive approval.
- Every Release entity must have an approval before dry-run.
- A Dry-run Receipt must pass and remain unexpired before publish.
- A live target requires the canonical source.
- A foreign QID blocks the Plan unless a matching, digest-bound consent exists.
- The projector resolves local references to existing source QIDs before it seals the Release.
- Profile `1` blocks unresolved local references, including qualifiers and references.
- Profile `1-nodes` explicitly defers complete statements whose targets exist in the source.
  The Release retains those statements for review. A later Release must add the connections.
  See [upload readiness](upload-readiness.md).
- The former Studio `live` upload and single-item push routes return `410 Gone`.
- The Publication panel is the only default target selector. The compatibility
  upload panel appears only after the Publication API returns `404`, `405`, or
  `410`.

The PostgreSQL source uses `COLLATE "C"` for canonical key order (W-216).
Locale-sensitive order must not reach the release digest accumulator.

## Scale and recovery

The service stores entities, identity assertions, references, Findings, Plan
Actions, Execution Actions, Intents, Receipts, and audit rows as normalized data.
The read API uses keyset cursors and limits every page to 500 entities.
The planner reconciles batches of 50 entities.
The projector and router do not load an entire corpus into a browser or request.
Execution claims use a lease. A retry can repeat only a confirmed pre-send failure.

## API

- `POST /api/runs/{runId}/wikidata-publications/prepare`
- `POST /api/runs/{runId}/wikidata-publications/{publicationId}/advance`
- `POST /api/runs/{runId}/wikidata-publications/{publicationId}/read`

The wire commands are `review`, `dry_run`, `publish`, `resume`, and `cancel`.
The read queries are `summary`, `entities`, `operation`, and `audit`.
The API returns `source_current` from the backend. The browser does not calculate it.

## Credentials

The Publication API resolves the signed-in account's saved credential for the selected wiki.
Publish and Resume issue an encrypted, expiring worker grant; no plaintext secret enters a job parameter.
See [Publication credentials](publication-credentials.md) for account binding, target separation, fallback, and tests.

## Tests

- `backend/tests/unit/test_publication_module.py` tests digests, gates, and recovery states.
- `backend/tests/test_publication_repository.py` tests normalized persistence and action claims.
- `backend/tests/test_publication_router.py` tests the HTTP contract and access gate.
- `frontend/e2e/wikidata-publication.spec.ts` tests Review, Dry-run, Publish, audit, and bounded pages.
