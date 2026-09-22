# Background Run-Job Service — How it works

> Up: [Background Run-Job Service](README.md)

## Lifecycle

```
POST /runs/{run_id}/jobs {kind, params}
  └─ prepare_job_params()          # bounded validation + secrets → params["_*"]
  └─ create_job()                  # pre-check active job → 409 ActiveJobError
       │                           # INSERT (partial unique index backstops the race)
       └─ spawn_job(job_id) ──► asyncio task _execute_job()
                                    └─ _try_claim_job()   # atomic conditional UPDATE
                                    │    queued → running, claimed_by=WORKER_ID
                                    └─ dispatch by kind → run_<kind>_job(job_id)
                                          │ loop: update_job_progress(...)
                                          │       is_cancel_requested(...) → early exit
                                          └─ finish_job(status, result, progress)

Every 60 s (run_job_maintenance_loop):
  _heartbeat_owned_jobs()   # bump updated_at on rows whose task is alive here
  cancel_requested_queued_jobs()   # queued + flag → cancelled (no owner exists)
  cancel_requested_running_jobs()  # running + flag older than
                                   #   RUN_JOB_CANCEL_FORCE_AFTER_S → cancelled
  fail_stale_jobs()         # running + updated_at > 5 min old
                            # verify kinds with judged>0 → re-queue (W-134)
                            # else → failed (verify stamps resumable — W-130)
  _respawn_orphaned_jobs()  # queued rows > 90 s old with no local task → spawn

On process start (lifespan): ``startup_job_recovery()`` runs
``fail_stale_jobs`` → ``recover_interrupted_jobs`` →
``recover_resumable_verify_jobs`` (each step is isolated — a failure never
blocks boot). Resumable recovery re-queues at most one failed verify row per
``(run_id, kind)`` (highest judged progress wins).
```

Statuses: `queued → running → succeeded | failed | cancelled`. Active =
`{queued, running}` (`ACTIVE_JOB_STATUSES`, `run_job.py:53`).

Worker identity (`run_job_service.py:44-51`): `WORKER_ID = "{DYNO-or-hostname}:{uuid8}"`.
The dyno prefix lets a restarted `web.1` reclaim its dead predecessor's rows
instantly; the uuid suffix distinguishes live processes. Known caveat: with
`WEB_CONCURRENCY > 1` sibling uvicorn workers share the prefix — acceptable while
production runs one worker.

`wikidata_verify` is deliberately bounded at enqueue: `prepare_job_params`
checks the action and provider credentials but does not load or enrich the
Studio scope. After the job is committed and claimed, `verify_job.py` builds
that scope and records an invalid/empty scope as a failed job. This keeps the
`POST /jobs` request below Heroku’s 30-second router limit.

**Admission control (Rule W-129).** `create_job` still returns 201 immediately,
but `_execute_job` claims a `queued` row only when global and per-class
concurrency slots are free (`verify` / `build` / `upload` / `light`; defaults
tuned for a Basic dyno). Excess jobs stay `queued` with
`progress.message = Waiting for capacity…` until `admit_waiting_jobs()`
re-spawns them after a terminal job or on each maintenance tick. Caps:
`RUN_JOB_MAX_RUNNING`, `RUN_JOB_MAX_VERIFY`, `RUN_JOB_MAX_BUILD`,
`RUN_JOB_MAX_UPLOAD`, `RUN_JOB_MAX_LIGHT`.

**Heavy cap + worker split (Rules W-235 / R27, 2026-09-15).** The
build + verify + upload slot classes share one extra cap
(`RUN_JOB_MAX_HEAVY`, default 1) — a graph build and a bulk verify never
run concurrently on the same small dyno. The process role derives from
`DYNO` (`RUN_JOB_ROLE` overrides): `web.*` claims only light kinds,
`worker.*` everything. A queued heavy job waits `RUN_JOB_WORKER_GRACE`
(default 120 s) for a worker tick, then web claims it itself so nothing
queues forever. The worker is `python -m app.jobs_worker`
(Procfile `worker:`, `heroku ps:scale worker=1`).

**Streaming RDF builds (Rule W-234 / R26).** `rdf_build.py` maps one
record subgraph at a time, appends a Turtle chunk per record, checkpoints
every 25 records into `job.progress.checkpoint`, and rebuilds the graph
index + coverage reports in a subprocess (`rdf_coverage_reports`) that
also yields the authoritative distinct-triple count. A restarted build
truncates the artifact to the checkpoint byte offset and skips
already-mapped records. Since batch-build (R23, rdf-graph block) the job
never materialises the corpus: `rdf_build_batches.iter_rdf_build_batches`
pages `run_records` by the `(run_id, control_number)` keyset and the
resume signature comes from one server-side count/min/max aggregate. On
Modal the build fans out to shard containers (rdf-graph R24) that append
CN-ordered chunks under the same checkpoints.

**Interrupted verify resume (Rule W-130).** Stale/failed verify jobs carry
`result.resumable` + judged/total. Wikidata/HMO streams write each verdict to
the inference cache immediately so Continue (`override_cache=false`) warm-hits
already-judged items.
## Job kinds and owners

| Kind | Worker module | What it does |
|---|---|---|
| `extraction` | `extraction_job.py` | Streams `extract_entities_stream` over run records; persists entities from the results JSON at the end |
| `authority_re_enrich` | retired | Compatibility rows fail closed with HTTP 410 / terminal error; HMO Studio owns enrichment |
| `ner_verify` / `wikidata_verify` / `hmo_item_verify` | `verify_job.py` | Opens the corresponding eval-agent event stream, tracks unique candidate IDs (never aggregate stats or replayed events), mid-run **counters-only** progress (Rule W-128) and slim `session_snapshot` in `result` |
| `rdf_build` | `rdf_build_job.py` | Builds the TTL from keyset-paged loads (R23), write-throughs `RdfArtifact`, invalidates on-disk graph caches; on Modal, shard fan-out (R24) |
| `wikidata_studio_build` | `wikidata_studio_build_job.py` | Sequential: delegates to `execute_studio_build` (fingerprint cache per Rule W-26). Modal: shard fan-out via `modal_jobs.py` — streamed fingerprint, CN slices of 500, merge + corpus-wide finish + cache upsert (wikidata-studio "Sharded build") |
| `wikidata_upload` | `wikidata_upload_job.py` | Two-pass dry-run/test/live upload through `wikidata_upload.upload_items` (W-30 / W-192); same two-step `steps` payload for every `upload_target` |
| `wikidata_publication_prepare` | `wikidata_publication_prepare_job.py` | Streams the Studio source into a sealed immutable Release; the payload has request metadata and an actor ID but no credential secret |
| `wikidata_publication_execution` | `wikidata_publication_execution_job.py` | Runs a queued, digest-bound Publication Execution and resolves its server credential only inside the worker |
| `hmo_coverage` | `hmo_coverage_job.py` | 9-14 min coverage report; write-throughs `HmoCoverageCache` (Rule W-39) |
| `hmo_schema_bootstrap` | `hmo_schema_bootstrap_job.py` | Sequential `wbeditentity` per missing ontology class/property on Wikibase Cloud |
| `hmo_item_upload` | `hmo_item_upload_job.py` | Live per-item Wikibase Cloud writes + deferred links, with audit context |
| `hmo_item_bulk_approve` / `wikidata_item_bulk_approve` | `studio_item_bulk_approve_job.py` | Background approve of filtered Studio override rows (Rule W-105) |
| `hmo_item_build` | `hmo_item_build_job.py` | Authority refresh → RDF rebuild → HMO item export (`execute_hmo_item_build`; Rule W-106) |
| `hmo_manifest_build` | `hmo_manifest_build_job.py` | IIIF manifests from the run TTL (Rule W-106) |
| `hmo_manifest_upload` | `hmo_manifest_upload_job.py` | IIIF manifest dry-run / live upload (Rule W-107) |
| `hmo_item_upload` | `hmo_item_upload_job.py` | HMO item dry-run **and** live upload (Rule W-107) |
| `hmo_rule_verify` | `rule_verify_job.py` | Deterministic (non-AI) rule checks over HMO items; per-item `rule_verdict` on override rows, per-rule tallies in `result`; Modal shard fan-out (CPU rules only) + one dedicated API-pass container via `modal_jobs.py` (hmo-wikibase-studio R55–R62) |

Dispatch is the `if/elif` chain in `run_job_service.py::_execute_job`;
kinds are declared in `models/run_job.py` (`SUPPORTED_JOB_KINDS`).
Publication execution failures leave the durable Execution paused. A later
summary read repairs an older failed job that left the Execution running.

## Frontend attachment

`stores/runJobs.ts` polls `GET /api/jobs/mine?active=true` every 2 s while any job is
active (self-stops when the list empties), discards out-of-order responses via a
monotonic `refreshSeq`, and accepts WebSocket `run_job_update` pushes via
`upsertJob`. Components never call `jobForRun`/`activeJobs` inside a selector —
they select `s.jobs` and derive with `selectActiveJob(...)` in `useMemo` (Rule W-36).
`useRunJobAttachment(runId, kind, sync)` re-attaches to an already-running job on
mount, prefers the tracked job id through the terminal snapshot (upserts it into
the store before clearing — Rule W-108), and fingerprint-guards the `sync`
callback; `useVerifyJob` layers verify-session
loading (from `progress.session_snapshot` while running, disk/`result` at finish)
and partial-result messaging on top. Its optimistic start state is rolled back
when the enqueue HTTP request rejects, so a request that never created a job is
not rendered as a cancellable running job.
