# Wikidata Publication

> Current policy: [creator-only writes](creator-only-writes.md) supersedes the historical foreign-edit paths below.

> Up: [Wikidata Studio](README.md)

## Purpose

The Publication service can write to `www.wikidata.org`. It turns a source
snapshot into an immutable Release. It then requires Review, Dry-run, and
Publish in that order. The separate bulk upload API also accepts `live` and
does not require these Publication gates (W-291).

## Lifecycle

1. `prepare` creates a build job that reads the selected source and stores one normalized entity row per entity.
2. The service seals the Release with a canonical digest and persistent Findings.
3. `review` stores digest-bound Approval Decisions on the sealed Release.
4. `dry_run` queues `wikidata_publication_dry_run`. The worker reconciles the approved set and seals a Plan plus a short-lived Receipt.
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
- A foreign QID always blocks an update. Historical consent has no authority (W-324).
- A new Approval Set invalidates the current Plan. Publish, Resume, and the
  worker require the current Plan and Approval Set.
- A final CREATE check that finds any existing target blocks and requires a
  fresh Plan; it does not convert the action into an UPDATE.
- Work documents must contain `records` and `work_candidate_evidence` lists.
  Each record must be a non-empty string; evidence rows must be objects and
  any `identity_scope` must be `record`. Empty lists pass shape validation.
  The Release digest covers these fields. Automatic projection cannot change
  them or the entity type. Duplicate QIDs involving a work block preparation.
- The projector resolves local references to existing source QIDs before it seals the Release.
- Profile `1` blocks unresolved local references, including qualifiers and references.
- Profile `1-nodes` explicitly defers complete statements whose targets exist in the source.
  The Release retains those statements for review. A later Release must add the connections.
  See [upload readiness](upload-readiness.md).
- Reference views must agree, and old flat multi-snak provenance blocks
  preparation or gateway validation. Deferred statements retain their blocks;
  only deferred references may retain local targets at the gateway (W-304).
  See [reference blocks](reference-blocks.md). An older sealed Release needs a
  new valid source and Release; it cannot be changed in place.
- The single-item `live` push route returns `410 Gone`. The bulk `live` upload
  route and job accept the target and require the live Settings credential.
- The Publication panel is the only default target selector. The compatibility
  upload panel appears only after the Publication API returns `404`, `405`, or
  `410` on prepare. It lists `live` and `test`, but enables submission only
  for `dry_run`. This UI restriction is not a bulk API gate.

The PostgreSQL source uses `COLLATE "C"` for canonical key order (W-216).
Locale-sensitive order must not reach the release digest accumulator.

## Work Identity

The gateway reconstructs work identity from the sealed source evidence.
For record-scoped works, the source record set must match the remote reference
set exactly. The remote set comes from P3959 references and NLI P854 URLs.
The gateway also checks author identity. Conflicting, unresolved, or invalid
evidence blocks reconciliation or UPDATE preflight. A source-scoped work with
no completed duplicate check is unknown, not absent; a fresh work CREATE
without P50 or P2093 cannot prove absence. Publication also requires the
target's non-deprecated P31 values to be verified written-work types.
Generic test-wiki reconciliation blocks until a test-specific identity check
is available; a production QID is not a test-wiki QID.

An older immutable Release may lack the required work fields or contain invalid
shapes. Its affected actions cannot pass the current gateway; prepare and review
a new Release from a current Studio source instead of changing the sealed data.

## Authorization Race

For each action, the worker checks the current Execution, Plan, and Approval
Set after the remote preflight, during item preparation, and immediately before
each main `wbi_item.write` call.
A changed approval, Plan, or Execution blocks the write; a failed check is
retryable before send. This reduces the window for a stale authorization.
The database check and the Wikidata write are not atomic. An approval change
or cancellation after the check can race with a write already in progress.
An UPDATE also uses the expected Wikidata revision, but `baserevid` cannot
detect a change to the local approval. See [bulk upload](guards-and-upload.md)
for its separate approval check and the same cross-system limitation.
Test-wiki remapping can create property or item stubs during item preparation,
before the last main-item authorization check. The confirmation and recovery
paths also call the remapper and may create stubs. Those stub writes do not use
the Publication send callback or the main-item single-send options.

## Scale and recovery

The service stores entities, identity assertions, references, Findings, Plan
Actions, Execution Actions, Intents, Receipts, and audit rows as normalized data.
The read API uses keyset cursors and limits every page to 500 entities.
The planner reconciles batches of 50 entities.
The projector and router do not load an entire corpus into a browser or request.
Execution claims use a lease. A retry can repeat only a confirmed pre-send failure.
Each main WBI write uses `max_retries=1` and `allow_redirects=False`.
After a confirmed CSRF rejection, the gateway refreshes the token and may send
once more after a new authorization check. An ambiguous response is not retried.
An expired write timeout is `outcome_unknown`; a read that does not yet show
the mutation cannot prove that the original request has stopped (W-302).
The uploader normalizes Wikibase item values before it compares or writes them.
An existing `P31=Q5` and an item-valued local `P31=Q5` are the same claim.
If a worker fails before the runtime records a pause, a summary read reconciles
the failed execution job to `paused`. The browser shows the saved counts, the
worker error, and a Resume action. This recovery never retries a remote write.

The publication controls present the researcher workflow as Prepare, Review, and
Publish. A result summary separates records prepared for the Plan from records
that remain outside this publication. Omitted connections show a named property
and target first; technical JSON remains inside a disclosure. The manual editor
uses explicit English and Hebrew field labels, language direction metadata, and
one Hebrew alias per row. These labels change the interface only; the digest,
approval, and publication gates remain unchanged. The panel renders on the
shared `<Glass>` surface (Rule W-35) and sits below the always-visible review
table on the Studio page — the zone order is Review, then Publish, then one
collapsed Advanced area for exports and rebuild options.

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
  `test_rejection_during_preflight_blocks_the_write` covers CREATE and UPDATE
  rejection before the final authorization check; it does not prove atomicity.
- `backend/tests/unit/test_publication_source_evidence.py` checks work evidence,
  record-scoped identity, and UPDATE preflight. `backend/tests/test_publication_source_projection.py`
  checks sealed source fields and duplicate work QIDs.
- `backend/tests/unit/test_wikidata_publication_gateway.py` checks single-attempt
  main writes, redirects, ambiguous outcomes, and the authorized CSRF retry.
- `backend/tests/test_publication_repository.py` tests normalized persistence and action claims.
- `backend/tests/test_publication_router.py` tests the HTTP contract and access gate.
- `backend/tests/test_wikidata_publication_execution_job.py` tests worker failure
  recovery and encrypted execution parameters.
- `backend/tests/test_publication_migrations.py` tests the action-table schema
  used by the worker repository.
- `frontend/e2e/wikidata-publication.spec.ts` tests Review, Dry-run, Publish, audit, and bounded pages.
- `frontend/tests/unit/wikidataPublicationControls.spec.tsx` tests saved paused
  progress, the worker error, the Resume action, and included/omitted counts.
- `frontend/e2e/wikidata-item-drawer.spec.ts` tests labelled multilingual fields
  and the manual editor actions.

See [the asynchronous dry-run flow](publication-dry-run-job.md) for job polls, progress, and cancellation.

## Review statuses and creator-only writes

The entity read API maps stored `approve`/`reject` decisions to `approved`/`rejected` (W-220).
The Plan blocks every foreign update, including an exact historical consent.
Blocked action responses return `consent: null`.
The UI offers reference-only reuse and defer, with no foreign-update control.
The test target links to `test.wikidata.org`; the live target links to `www.wikidata.org`.
The gateway checks creator proof before every send.
