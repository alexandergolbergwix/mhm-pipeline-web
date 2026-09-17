# Background Run-Job Service — Rules

> Up: [Background Run-Job Service](README.md)

1. **R1 — Never flip a row to `running` outside `_try_claim_job`.** All ownership
   transitions go through the single atomic conditional UPDATE (`run_job_service.py:182`).
   *Why:* it is the only thing that makes the cross-process race deterministic — exactly one claimant sees `rowcount == 1`.

2. **R2 — A running row owned by a fresh foreign worker MUST NOT be claimed.**
   Claimable only when queued, unowned, ours, same-dyno-prefix, or heartbeat-stale (> `STALE_JOB_AFTER` = 5 min).
   *Why:* otherwise two dynos execute the same job concurrently (double Wikibase writes, double entity upserts).

3. **R3 — Staleness is decided by the maintenance heartbeat, not worker progress.**
   `_heartbeat_owned_jobs` bumps `updated_at` on rows whose asyncio task is alive; workers must never be required to call `update_job_progress` just to stay alive.
   *Why:* a busy-but-quiet job (e.g. the RDF TTL-write tail) would otherwise be reaped as stale mid-work.

4. **R4 — The maintenance tick order is heartbeat → reap → respawn, and must stay so.**
   (`run_job_maintenance_tick`, `run_job_service.py:277`.)
   *Why:* heartbeating first means a process can never reap its own live jobs in the same tick.

5. **R5 — One active job per `(run_id, kind)`, enforced by `uq_run_jobs_active_kind`; the create race MUST surface as HTTP 409 with the existing `job_id`.**
   `create_job` catches the `IntegrityError` and re-raises `ActiveJobError`; the router maps it to `409 {"message": "job already running", "job_id": ...}` so the client attaches instead of duplicating.
   *Why:* the pre-check `find_active_job` is racy across dynos; the partial unique index is the real guard and the 409-attach protocol is the client contract.

6. **NEVER expose `claimed_by` or underscore-prefixed params through the API.**
   `serialise_job` omits `claimed_by` and `_public_params` strips `_hf_token`, `_api_key`, `_wikidata_token` (`run_job_service.py:481-505`).
   *Why:* params carry unwrapped user secrets; the wire payload shape is also pinned by frontend types.

7. **R7 — Secrets are unwrapped only in `prepare_job_params`, at start time, in the request context.**
   Workers read them back from `params["_*"]`; they never re-derive credentials.
   *Why:* the user's KEK exists only in the authenticated request; a background task cannot unwrap anything later.

8. **R8 — Cancellation is cooperative: workers MUST poll `is_cancel_requested` at every loop boundary and finish with `status=cancelled`.**
   `request_cancel` only stamps `cancel_requested_at`; nothing force-kills the task.
   *Why:* mid-item interruption of external writes (Wikibase, Wikidata) would leave half-applied state; the worker chooses safe stopping points.

9. **R9 — Terminal state is written only via `finish_job` / `_fail_job`, and `_fail_job` never overwrites `succeeded`/`cancelled`.**
   *Why:* the crash handler in `_execute_job` races with normal completion; the guard keeps a late exception from clobbering a good result.

10. **R10 — Verify jobs MUST embed `session_snapshot` in `progress` (live) and `result` (terminal).**
    (`verify_job.py`, Rule W-33.) Live progress snapshots are **throttled**
    (Rule W-127 / R20); the terminal `result` still carries the full
    snapshot. *Why:* `/tmp` verify state is per-dyno; the snapshot is what
    lets session GET handlers and `useVerifyJob` survive multi-dyno routing
    while the job is still running.

11. **R11 — Progress/NOTIFY pushes are best-effort and must never fail the job.**
    `_notify_job_update` skips non-Postgres dialects, refreshes the row before serialising (expired `updated_at` → `MissingGreenlet` otherwise), and swallows publish errors with a rollback.
    *Why:* a missed live push costs 2 s of poll latency; an exception here would abort a healthy job.

12. **R12 — A worker that interleaves DB work with slow/retrying external I/O must commit before the slow call** (Rule W-40), and any on-disk build result it caches needs a Postgres write-through (Rule W-39).
    *Why:* the 2-minute idle-in-transaction timeout kills connections held across retry backoff; local-disk caches evaporate on every deploy.

13. **R13 — Frontend: select primitives from `useRunJobs`, derive with `selectActiveJob` in `useMemo`, fingerprint-guard `sync`/`onComplete` callbacks** (Rule W-36); use `useRunJobAttachment`/`useVerifyJob`, never hand-rolled poll effects. Tracked jobs MUST still sync on terminal success (Rule W-108) so review tables reload Publication status.
    *Why:* store-method selectors and unguarded effect callbacks caused three production blank-UI incidents (React #185).

14. **R14 — On start, a UI that receives a 409 MUST attach to the returned `job_id` rather than error out.**
    *Why:* another tab/curator (or a respawned orphan) may already be running the job; attaching is the designed multi-client behaviour.

15. **R15 — Job-start HTTP requests MUST stay bounded: validate action IDs, requested parameters,
    and credentials, then commit the job; never materialise a
    slow build/verify scope before `create_job`.** The claimed worker validates
    the materialised scope and records a terminal job error. *Why:* loading 294
    Wikidata Studio items during `POST /jobs` hit Heroku’s 30-second H12 limit,
    leaving the modal running with no job or verdict events.


16. **R16 — Verify-job progress counts unique candidate identities only.** `agent.stats` is advisory and streamed/replayed `agent.verdict` events may repeat; `verify_job.py` MUST derive `processed` from the deduplicated candidate local IDs and cap it at `scope_size`. The identity keys MUST cover every channel's candidate shape — `_local_id` / `_item_id` / `local_id` / `id` / `_entity_id` (NER/extraction candidates carry `_entity_id` only). *Why:* a 294-item Wikidata verify displayed 395/294 while still running (Rule W-64); an 1829-item NER verify sat at 0/1829 for the whole run because `_entity_id` was not read (found 2026-09-15).

17. **R17 — Studio “Approve all visible” MUST be a `run_jobs` kind, never a browser PATCH storm.** `hmo_item_bulk_approve` / `wikidata_item_bulk_approve` take `local_ids`, version via `apply_event`, report progress, and honour cancel. *Why:* thousands of sequential override PATCHes hung the curator UI and did nothing useful (Rule W-105).

18. **R18 — Studio / RDF builds MUST be `run_jobs` with inline progress (Rule W-106).** `hmo_item_build`, `hmo_manifest_build`, `rdf_build`, and `wikidata_studio_build` own heavy build/rebuild work; HTTP handlers enqueue and return immediately; the UI attaches via `JobProgressInline`. *Why:* authority+RDF+export and large Studio rebuilds exceed Heroku’s 30s router budget and must not block the curator with a bare spinner.

19. **R19 — Studio publish/upload MUST be `run_jobs` (Rule W-107).** `hmo_item_upload` (dry-run + live), `hmo_manifest_upload`, and `wikidata_upload` (including the legacy `POST …/wikidata-studio/upload` alias) enqueue; never run sequential Wikibase/Wikidata writes on the request path. *Why:* thousands of sequential writes H12; dry-run over ~2k items also exceeds the router budget.

20. **R20 — Live verify `session_snapshot` MUST be throttled / slim (Rules W-127 / W-128).** Mid-run progress is **counters only** (no snapshot). Progress DB writes ~every 2 s. Terminal `result.session_snapshot` is compact verdicts + empty events; `serialise_job` re-slims. *Why:* job ecfdcf29 R14'd + H12'd job polls while the modal showed VERDICTS (0) on a finished partial job.

21. **R21 — Queued jobs MUST pass admission before claim (Rule W-129).** `run_job_service` gates `_try_claim_queued_job` on global + per-class running counts (`verify` / `build` / `upload` / `light`); excess rows stay `queued` with `progress.phase=queued` and `message=Waiting for capacity…`. `finish_job` / `_fail_job` / `fail_stale_jobs` / the maintenance tick call `admit_waiting_jobs()` to re-spawn waiting rows. Never bypass admission by calling `_try_claim_job` on a fresh `queued` row. *Why:* unbounded parallel verify/build on one Basic dyno caused R14/H12 under load.

22. **R22 — Interrupted verify jobs MUST auto-resume on the backend (Rule W-130 / W-134).** Incremental cache persist; `fail_stale_jobs` re-queues verify rows with judged>0 (`apply_verify_job_auto_resume`); startup runs `recover_interrupted_jobs` + `recover_resumable_verify_jobs`. UI **Continue** is fallback only. *Why:* partial verify after dyno restart/OOM should not require curator action when cache already holds verdicts.
23. **R23 — Publication prepare, dry-run, and execution must use run jobs (W-212, W-217, W-218).**
    `wikidata_publication_prepare` seals a Release. `wikidata_publication_dry_run`
    creates its plan and receipt. `wikidata_publication_execution` writes approved actions.
    Private encrypted grants carry saved account credentials to dry-run and execution workers.
    Public parameters contain no credential values. The generic job endpoint rejects all three kinds.
    *Why:* a full Release and its remote checks cannot fit inside the HTTP timeout.

24. **R24 — Publication AI review uses a private job route (W-222).** The generic job endpoint rejects this kind. Encrypted grants carry credentials. The worker saves progress and supports cancellation. *Why:* a report must survive refresh without a synchronous AI request.

25. **R25 — Verify stream `finally` blocks MUST never yield (W-233).** Every verify event stream (extraction/NER, authority, Wikidata Studio, HMO items, HMO schema) persists on-disk verdicts + `session.end` in its `finally`; guard each `yield` there with `agent_runner.generator_is_closing()`. A yield while `GeneratorExit` is pending raises `RuntimeError: async generator ignored GeneratorExit`, flips a cancelled job to *failed*, and skips verdict persistence entirely. *Why:* cancelling an 1829-item NER verify at 51 judged lost all 51 verdicts (job 993884a7, 2026-09-15).

26. **R26 — RDF graph builds MUST stream, never accumulate (W-234).** `_run_mapper_sync` maps one record subgraph at a time, applies overrides within that subgraph, appends a Turtle chunk to the artifact, and frees it. Checkpoints (`{record_index, file_bytes, manuscripts, signature}` in `job.progress.checkpoint`) are written every 25 records and honored on restart by truncating the artifact to the checkpoint byte offset. The graph index and coverage reports re-parse the artifact in a **subprocess** (`app.pipeline.rdf_coverage_reports`), which also yields the authoritative distinct-triple count. *Why:* the whole-corpus `combined` Graph is what R14/R15-killed the 512 MB dyno on ~900-record runs — three crashes on 2026-09-15 (jobs 476ec5b4-era, b3cb9546).

27. **R27 — Heavy jobs run one at a time, on the worker dyno (W-235).** Admission adds a shared heavy cap (`RUN_JOB_MAX_HEAVY`, default 1) across the build + verify + upload slot classes: a build and a bulk verify may never run concurrently on the same small dyno. Process role derives from `DYNO` (`RUN_JOB_ROLE` overrides): `web.*` executes only light kinds, `worker.*` everything; a queued heavy job waits `RUN_JOB_WORKER_GRACE` (default 120 s) for a worker tick, then any alive process claims it so jobs never queue forever. The worker runs `python -m app.jobs_worker` (Procfile `worker:`). *Why:* rdf_build + the NER eval-agent subprocess together exceeded the memory quota and SIGKILLed web.1, taking the UI down with the jobs (2026-09-15).

28. **R28 — Cancelling a queued job MUST finalize it (W-236).** `request_cancel` only stamps `cancel_requested_at`; a *running* job's owner polls that flag and finalizes itself, but a *queued* job waiting for capacity has no owner. The maintenance tick runs `cancel_requested_queued_jobs()` (queued + flag → `cancelled`, error "Cancelled by user"), and the claim path refuses any queued row carrying the flag — so Cancel always terminates the job and can never be raced by the grace self-heal. *Why:* cancelling two "Waiting for capacity…" builds on 2026-09-16 left them queued forever after the worker split added ownerless queued jobs.

29. **R29 — rdf_build / hmo_item_build MAY execute on Modal; Heroku always keeps the fallback (W-237).** When `MODAL_JOBS_URL` is set, the claiming process dispatches the job to the `mhm-jobs` Modal app (`modal/modal_jobs.py`, body-token auth) after the normal claim; the detached container runs the same backend runner against Heroku Postgres (`DATABASE_URL` in the `mhm-jobs` Modal secret) and writes progress + terminal state itself, while the web process waits on an asyncio.Event woken by the container's completion webhook (no busy waiting; a 60 s row check is the safety net). Dispatch failure, a 2xx without `ok:true`, non-2xx, poll timeout, or unset URL → the local runner executes (RDF resumes from its R26 checkpoint). The `mhm-jobs2` secret MUST carry `AUTHORITY_MODE=postgres` so the container's Mazal/KIMA lookups hit the imported Postgres tables instead of a missing SQLite file (W-243). Wiki credentials and publication tokens never enter Modal (W-217/W-228). Wikidata builds and Publication stay Heroku-side. *Why:* the 512 MB dyno cannot hold two heavy jobs (2026-09-15 R14/R15 chain); Modal gives parallel compute without resizing.

30. **R30 — A no-op authority refresh MUST NOT rebuild RDF + items (W-238).** `re_enrich_run` compares match-row content before writing and returns `content_changed`; `execute_hmo_item_build` sets `force_rdf_rebuild` only from that flag (plus an explicit `force_rebuild`). Confidence is written through but never counts as a change (volatile model score, absent from the TTL). An unchanged "Build items" click therefore hits the `hmo_studio_item_cache` fingerprint instead of re-running the VIAF/KIMA → RDF → export chain. *Why:* the cached path rebuilt everything on every click (2026-09-16), burning minutes of lookups per press.

31. **R31 — "Build items" on an unchanged run MUST return the cache instantly (W-239).** `execute_hmo_item_build` short-circuits before step 1: when `hmo_studio_item_cache` exists and no input changed since `built_at` (authority-match `approved_at`/`created_at`, `extraction_approvals.updated_at`, `rdf_triple_overrides.created_at`), the cached result returns without the authority pass, RDF rebuild, or export. The item fingerprint cache alone is not enough — it hashes the TTL, and a Modal container's empty `/tmp` plus an authority pass that changes nothing must never trigger a 5k-entity re-run. *Why:* the curator watched a completed 1.5 h build re-run from 0/5295 on 2026-09-16.

32. **R32 — No DB session may stay open across a network/CPU phase (W-240).** The engine sets `idle_in_transaction_session_timeout=120s`; any transaction left open while the code does minutes of HTTP lookups or TTL building gets its connection killed server-side, surfacing as `cannot call Transaction.rollback(): the underlying connection is closed` at the NEXT write. Long-running jobs therefore: commit per unit of work (`re_enrich_run` commits per entity), recycle the pooled connection (`await db.close()`) after each commit before a CPU-bound phase (`expire_on_commit=False` keeps loaded rows usable), and load all needed rows before the CPU stretch. Even a READ-only transaction must commit before a long CPU stretch — the HMO export stage's fingerprint read sat open across the exporter/resolve/SHACL threadpool work and killed the build (2026-09-17). *Why:* the HMO item build died twice at step 1 and step 3 with exactly this error (2026-09-16), and again from the export stage (2026-09-17).

33. **R33 — Enrichment is resumable per entity (W-241).** `authority_matches.enriched_at` stamps the last (re-)enrichment (migration 0044; unstamped rows backfill from `created_at`). When `skip_cache=false`, `re_enrich_run` computes the run's latest upstream change (`extraction_approvals.updated_at` max) and skips every entity whose row is enriched after it — a restarted or repeated pass visits already-enriched entities in milliseconds instead of re-matching them, and a changed NER row invalidates the skip. *Why:* every "Build items" attempt restarted the 5.3k-entity pass from 0/5295 (2026-09-16), burning the full VIAF/KIMA budget on each attempt.

34. **R34 — Authority enrichment matches concurrently (W-242).** `re_enrich_run` runs in two phases: network-bound `matcher.match` calls fan out with bounded concurrency (`ENRICH_CONCURRENCY`, default 8, each task with its own short `session_scope` — the inference cache commits internally), then the serial DB-apply phase upserts candidates with per-entity commits. The apply session MUST commit **before** the fan-out: its read phase leaves a transaction open, and an idle-in-transaction connection during the (possibly many-minute) gather gets killed by the 120 s timeout, so the first apply commit dies with "the underlying connection is closed". The fan-out also reports per-completion progress (`Matching pending entities… n/m` over unique pending keys, W-113) and the finalize pass per chunk — the sweep reaches the full bar in seconds, so without these emits the UI sits frozen at "5295 / 5295 entities" for the whole match (the curator reads that as "stuck", 2026-09-17). Never hold the apply session across a network call. *Why:* the 5.3k-entity pass ran lookups serially at ~2.5 s each — hours per attempt even with warm caches (2026-09-17); after the R34 fan-out landed, the read session still sat idle-in-transaction across the gather and the build failed twice more with the R32 error at the first apply commit (2026-09-17 17:00/17:12).

35. **R35 — Modal dispatch contract + image freshness (W-243).** `_dispatch` accepts any 2xx whose body carries `ok:true` (the `@modal.fastapi_endpoint` answers 200 `{"ok":true,"spawned":true}`, never 202); rejecting a real accept double-runs the job — Heroku falls back to the local runner while Modal's spawned container races the same row (double-writer progress, duplicate work). And every change to job-runner code must land with `modal deploy modal_jobs.py` in the same change: the detached container executes its own copy of `backend/`, so a stale image silently re-runs superseded code (the 2026-09-17 build crawled at ~1 entity/s on a pre-R34 serial image while the local R34 runner raced it). *Why:* job 2f78729e (2026-09-17) logged `rejected: 200 {"ok":true,"spawned":true}` and ran on BOTH executors; the Modal image predated the R34 commit deployed to Heroku an hour earlier.
