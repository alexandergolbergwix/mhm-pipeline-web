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
  fail_stale_jobs()         # running + updated_at > 5 min old → failed
  _respawn_orphaned_jobs()  # queued rows > 90 s old with no local task → spawn

On process start (lifespan): fail_stale_jobs() → recover_interrupted_jobs()
  (re-spawns every active row; foreign live rows lose the claim and no-op)
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

## Job kinds and owners

| Kind | Worker module | What it does |
|---|---|---|
| `extraction` | `extraction_job.py` | Streams `extract_entities_stream` over run records; persists entities from the results JSON at the end |
| `authority_re_enrich` | retired | Compatibility rows fail closed with HTTP 410 / terminal error; HMO Studio owns enrichment |
| `ner_verify` / `wikidata_verify` / `hmo_item_verify` | `verify_job.py` | Opens the corresponding eval-agent event stream, tracks unique candidate IDs (never aggregate stats or replayed events), embeds **throttled** partial `session_snapshot` in `progress` (live UI; Rule W-127) and full snapshot in `result` |
| `rdf_build` | `rdf_build_job.py` | Builds the TTL, write-throughs `RdfArtifact`, invalidates on-disk graph caches |
| `wikidata_studio_build` | `wikidata_studio_build_job.py` | Delegates to `execute_studio_build` (fingerprint cache per Rule W-26) |
| `wikidata_upload` | `wikidata_upload_job.py` | Item-by-item dry-run/live upload through the fail-closed `wikidata_upload.upload_items` gate (Rule W-30) |
| `hmo_coverage` | `hmo_coverage_job.py` | 9-14 min coverage report; write-throughs `HmoCoverageCache` (Rule W-39) |
| `hmo_schema_bootstrap` | `hmo_schema_bootstrap_job.py` | Sequential `wbeditentity` per missing ontology class/property on Wikibase Cloud |
| `hmo_item_upload` | `hmo_item_upload_job.py` | Live per-item Wikibase Cloud writes + deferred links, with audit context |
| `hmo_item_bulk_approve` / `wikidata_item_bulk_approve` | `studio_item_bulk_approve_job.py` | Background approve of filtered Studio override rows (Rule W-105) |
| `hmo_item_build` | `hmo_item_build_job.py` | Authority refresh → RDF rebuild → HMO item export (`execute_hmo_item_build`; Rule W-106) |
| `hmo_manifest_build` | `hmo_manifest_build_job.py` | IIIF manifests from the run TTL (Rule W-106) |
| `hmo_manifest_upload` | `hmo_manifest_upload_job.py` | IIIF manifest dry-run / live upload (Rule W-107) |
| `hmo_item_upload` | `hmo_item_upload_job.py` | HMO item dry-run **and** live upload (Rule W-107) |

Dispatch is the `if/elif` chain in `run_job_service.py::_execute_job`;
kinds are declared in `models/run_job.py` (`SUPPORTED_JOB_KINDS`).

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
