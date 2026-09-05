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
- A local entity reference blocks the Release until a two-phase edge executor exists.
- The former Studio `live` upload and single-item push routes return `410 Gone`.

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

The Publication API accepts only a credential reference. It does not accept a
bot password. A job parameter must not expose a plaintext credential in its
public payload. The gateway opens a server-held credential only inside the execution worker.

## Tests

- `backend/tests/unit/test_publication_module.py` tests digests, gates, and recovery states.
- `backend/tests/test_publication_repository.py` tests normalized persistence and action claims.
- `backend/tests/test_publication_router.py` tests the HTTP contract and access gate.
- `frontend/e2e/wikidata-publication.spec.ts` tests Review, Dry-run, Publish, audit, and bounded pages.
