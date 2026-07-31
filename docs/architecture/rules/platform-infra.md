# Platform, caching, Heroku, external calls

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

### Rule W-15 — modal/ is a deploy target, not a backend dependency

`modal/modal_app.py` is the Modal app definition (deployed once via
`modal deploy modal_app.py`). It is NEVER imported by the FastAPI
backend. Communication is HTTPS only — the backend's
`ModalInferenceBackend` POSTs to `<MODAL_NER_URL>/extract`.

The Modal app vendors the desktop's `ner/` + `converter/authority/`
modules via `image.add_local_dir(..., copy=True)`. Weight artefacts
(~3 GB) are pre-baked into the image at build time via
`run_function(_bake_weights)` BEFORE the local-dir adds so a
desktop NER code edit doesn't invalidate the weight layer.

`_bake_weights` runs first because Modal's rule is "add_local_*
must be last unless copy=True". A misordered chain like
`add_local_* → run_function` is rejected at build time. The fix
that landed in c19b9f3 + 98a0ca6 is the canonical layout.

Cold start: ~30-60s for all four BERT models on a CPU-2 container.
`scaledown_window=300` keeps it warm for 5 min after the last
call. After that, $0.

### Rule W-25 — Redis L1 in front of the Postgres inference cache (added 2026-06-03)

`backend/app/cache/redis_client.py` exposes `get_redis()` — a lazy
singleton that returns `None` when `REDIS_URL` is absent (dev/CI
falls back to Postgres-only with no code-path change).

`backend/app/pipeline/inference_cache.py::cache_lookup_or_call`
now uses a two-tier lookup:

1. **Redis GET `ic:{kind}:{hash}`** (<1 ms, in-memory)
2. **Postgres SELECT** (backfills Redis on hit)
3. **`fetch()`** (Modal / VIAF / Wikidata / …) — writes both tiers

Redis TTL policy: `ner.*` and `genre.classify` — no expiry
(content-addressed). `ai_verdict` — 7-day hot window; Postgres
holds 90-day ground truth. `authority.*` — 24 h hot window;
Postgres ground truth is 90 days (mazal), 30 days (viaf/wikidata),
or 180 days (kima).

`hit_count`/`last_hit_at` Postgres UPDATEs are skipped for Redis
hits (intentional — that UPDATE was the main warm-run latency cost).

`close_redis()` is called in the lifespan teardown in `main.py`.
The `slowapi` rate limiter shares the same Redis instance via
`RATELIMIT_STORAGE_URI`.

**Do not bypass this layer** for new inference call sites. All new
external calls (new authority APIs, new LLM calls) MUST go through
`cache_lookup_or_call`; the Redis tier is automatic.

### Rule W-39 — Every on-disk build cache needs a durable Postgres counterpart (added 2026-07-06)

Incident: `GET /hmo-studio/coverage` kept returning 409
`hmo_coverage_in_progress` for the same run over and over. The report
itself was fine (the background job legitimately takes 9-14 minutes —
it parses the run's RDF TTL via rdflib twice), but
`hmo_pipeline.cache_coverage_report` only wrote the result to the
dyno's local filesystem. Every Heroku deploy or dyno restart wipes that
cache (`claimed_by` on the job rows confirmed a fresh `WORKER_ID` each
time), so the *same run* paid the full 9-14 minute rebuild repeatedly
even though its RDF graph never changed.

Fix: `app/models/hmo_coverage_cache.py::HmoCoverageCache` — one row per
`run_id`, keyed by a SHA-256 fingerprint of the RDF TTL bytes (migration
`0032_hmo_coverage_cache`). `GET /coverage` now falls back to this
durable cache (and re-seeds the on-disk file) before enqueueing a
rebuild job; the background job write-throughs both caches on success.

**General invariant**: any per-run build result cached only under
`backend/state/runs/{run_id}/...` (on-disk) MUST also have a Postgres
write-through, mirroring `RdfArtifact` (the original TTL-restore fix,
2026-07-04) and `HmoStudioItemCache`/`WikidataStudioCache` (which
already did this correctly). A cache that only survives on local disk
is not a cache on Heroku — it is state that silently evaporates on
every deploy and forces a full, possibly multi-minute, rebuild.

Tests: `backend/tests/test_hmo_studio_coverage_router.py`
(`test_coverage_restores_from_durable_db_cache_on_disk_miss`,
`test_coverage_stale_db_cache_still_enqueues_rebuild`).

### Rule W-40 — Never hold an open DB transaction across a slow/retrying external write (added 2026-07-06)

Audit trigger: `hmo_item_upload_job.py` runs `upload_items_for_run` (up
to ~7800 sequential Wikibase Cloud writes) inside one long-lived
`session_scope()` for the whole job — that part is the established
pattern (mirrors `authority_re_enrich_job.py`) and is fine, because
each item's DB write commits immediately after it's made. The bug was
one level down: `hmo_item_reconcile.py::reconcile_item` ran a Postgres
SELECT (`_source_uri_property_pid`) and left that transaction **open**
while it then made a SPARQL reconcile call and, on a miss, the caller
made a `create_item`/`update_item` call — `cloud_client.py`'s
`_post_with_retry` does up to 6 attempts with exponential backoff
(1s→2s→4s→8s→16s→30s, ~61s of sleep alone, more with request
timeouts on top). That is squarely the failure mode `app/db.py`'s
2-minute `idle_in_transaction_session_timeout` backstop documents
verbatim ("a request handler that opens a session, runs one query,
then hangs forever on... an external HTTP call with no timeout") — a
slow/flaky Wikibase Cloud response could get the connection killed by
Postgres mid-job, crashing the whole upload instead of just failing
one item.

Fix, two parts:
1. `reconcile_item` now takes an optional `pid=` kwarg. A caller
   looping over many entities calls the new `resolve_source_uri_pid(db)`
   **once** before the loop and passes it through — the schema-level
   property id doesn't change during a run, so re-querying it ~7800
   times was also pure waste. `hmo_item_upload.py::upload_items_for_run`
   does this now.
2. When `pid` isn't pre-resolved (the single-item preview endpoint,
   `hmo_studio_items.py::reconcile_hmo_item`), `reconcile_item` commits
   right after the SELECT and before making any network call — the
   transaction is never open across slow I/O, regardless of caller.

**General invariant**: a background job (or request handler) that
interleaves a DB read/write with a slow external call (HTTP with
retry/backoff, subprocess, SSE stream) must close out its transaction
(`commit`/`rollback`) *before* the slow call, not rely on the
2-minute idle-in-transaction timeout to eventually clean up — that
timeout is a backstop against bugs, not a scheduling mechanism.

Tests: `backend/tests/unit/test_hmo_item_reconcile.py`
(`test_pid_lookup_transaction_is_closed_before_the_sparql_call`,
`test_pre_resolved_pid_skips_the_redundant_lookup`),
`backend/tests/test_hmo_item_upload.py`
(`test_live_upload_resolves_reconcile_pid_once_not_per_entity`,
`test_dry_run_never_resolves_reconcile_pid`).

**Recurrence (Wikidata verify scope, 2026-07-31).** The same rule was broken
again, this time on the read side. `_fetch_wikidata_verify_items` did its scope
reads, then — inside that still-open transaction — ran the Rule W-139 duplicate
probe (~100 s of throttled Wikidata HTTP) and the Rule W-140 LLM extraction
(one multi-second model call per manuscript). Postgres killed the connection at
the 2-minute mark and the first statement afterwards raised
`InterfaceError: connection is closed [SQL: SELECT ... FROM inference_cache
WHERE kind = 'marc.llm_extract' ...]`.

Note what the symptom is *not*: the query is a two-column index lookup behind
`uq_inference_cache_key` and Redis L1 already fronts it (Rule W-25). No query
was slow and no index was missing — the connection was dead *between*
statements. Indexing or caching cannot fix an idle-in-transaction kill; only
closing the transaction can.

Invariant, restated for read paths:

1. **Release the transaction before external I/O.** The scope builder calls
   `await db.rollback()` once its reads are done, before either enrichment step.
2. **A helper that interleaves cache reads with slow calls takes a session
   *factory*, not a session.** `marc_llm_extract.extract_for_record` and
   `wikidata_duplicate_probe.attach_duplicate_evidence` read the cache in one
   short transaction, make the network call with **no** transaction open, then
   write in another.
3. **Never cache a transient failure.** An `unavailable` model or lookup result
   is returned but not stored.
4. **Every cache kind declares a TTL.** `wikidata.duplicate_probe` (7 days) and
   `marc.llm_extract` (90 days) were missing from `KIND_TTL` /
   `_REDIS_TTL_SECONDS`, so their rows never expired — a permanently cached
   "this item is absent from Wikidata" is how a duplicate gets created months
   later.

Tests: `backend/tests/unit/test_wikidata_verify_scope_cache.py`
(`test_verify_fetch_ends_its_transaction_before_external_io`),
`backend/tests/unit/test_marc_llm_extract.py::TestSessionLifetime`,
`backend/tests/unit/test_wikidata_duplicate_probe.py::TestBatchedProbeCaching`.

**Follow-up (per-corpus cache access, 2026-07-31).** The first fix released the
transaction but kept a per-item loop, and "Loading Studio scope…" then hung for
five minutes on 313 items. Two causes, both about *shape* rather than latency:

1. `read_from_inference_cache` costs a SELECT **plus an UPDATE + COMMIT** for
   hit accounting, so a loop over 313 items was ~900 round trips.
2. LLM extraction ran one model call per manuscript, serially, on the path the
   curator waits on.

Invariant for any per-corpus cache user:

1. **Bulk, not loops.** `read_many_from_inference_cache` (one `IN (...)` query,
   Redis `MGET` first, no hit accounting) and
   `write_many_to_inference_cache` (one upsert, one commit) are the only
   acceptable access pattern once N scales with the corpus. The per-key helpers
   are for single-item paths.
2. **Independent external calls overlap.** LLM extraction runs under a bounded
   `asyncio.Semaphore` (`MARC_LLM_EXTRACT_CONCURRENCY`, default 6), so wall
   clock grows with N/6 rather than N.
3. **Only misses reach the model.** The corpus cache read happens first; a fully
   cached corpus makes zero model calls.
4. **Optional enrichment never sits on a path the curator waits on.** Provenance
   mining moved into the build job as its own reported phase ("mining provenance
   prose"); verify reads whatever the build stamped and reports `not_run`
   otherwise. An enrichment failure is logged, never fatal to the build.

Tests: `test_marc_llm_extract.py::TestScaling` (one bulk read for 50 items, two
short sessions, cached corpus makes no calls, calls overlap, budget still caps),
`test_wikidata_duplicate_probe.py::TestBatchedProbeCaching`.

---

### Rule W-123 — Login MUST NOT wait on Wikibase Cloud account provisioning (added 2026-07-27)

Incident: `POST /api/auth/login` on `mhm-pipeline.org` returned Heroku **H12**
(30 s) while logs showed
`wikibase account provision failed for shvedbook@gmail.com: All retries
exhausted`. Login (and `/me`) called `ensure_wikibase_access`, which re-ran
`WikibaseCloudWriter.create_local_account` whenever
`wiki_account_status` was `failed` (not treated as terminal). Cloud client
retries + backoff easily exceed the router budget, so a flaky Wikibase Cloud
made the app unusable to sign in even with a correct password.

Invariant:

1. Remote wiki provision runs only when `attempt_provision=True` (login /
   invite accept) — never on `/me`.
2. Statuses `active` / `skipped` / `failed` are **no-retry**; do not call
   Wikibase again on every login.
3. Provision is hard-capped (`asyncio.wait_for`, 5 s); timeout → `failed`
   without H12'ing the auth response. App authorization still succeeds.

Tests: `backend/tests/test_wikibase_user_access.py`.
