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


16. **R16 — Verify-job progress counts unique candidate identities only.** `agent.stats` is advisory and streamed/replayed `agent.verdict` events may repeat; `verify_job.py` MUST derive `processed` from the deduplicated candidate local IDs and cap it at `scope_size`. *Why:* a 294-item Wikidata verify displayed 395/294 while still running (Rule W-64).

17. **R17 — Studio “Approve all visible” MUST be a `run_jobs` kind, never a browser PATCH storm.** `hmo_item_bulk_approve` / `wikidata_item_bulk_approve` take `local_ids`, version via `apply_event`, report progress, and honour cancel. *Why:* thousands of sequential override PATCHes hung the curator UI and did nothing useful (Rule W-105).

18. **R18 — Studio / RDF builds MUST be `run_jobs` with inline progress (Rule W-106).** `hmo_item_build`, `hmo_manifest_build`, `rdf_build`, and `wikidata_studio_build` own heavy build/rebuild work; HTTP handlers enqueue and return immediately; the UI attaches via `JobProgressInline`. *Why:* authority+RDF+export and large Studio rebuilds exceed Heroku’s 30s router budget and must not block the curator with a bare spinner.

19. **R19 — Studio publish/upload MUST be `run_jobs` (Rule W-107).** `hmo_item_upload` (dry-run + live), `hmo_manifest_upload`, and `wikidata_upload` (including the legacy `POST …/wikidata-studio/upload` alias) enqueue; never run sequential Wikibase/Wikidata writes on the request path. *Why:* thousands of sequential writes H12; dry-run over ~2k items also exceeds the router budget.

20. **R20 — Live verify `session_snapshot` MUST be throttled / slim (Rules W-127 / W-128).** Mid-run progress is **counters only** (no snapshot). Progress DB writes ~every 2 s. Terminal `result.session_snapshot` is compact verdicts + empty events; `serialise_job` re-slims. *Why:* job ecfdcf29 R14'd + H12'd job polls while the modal showed VERDICTS (0) on a finished partial job.

21. **R21 — Queued jobs MUST pass admission before claim (Rule W-129).** `run_job_service` gates `_try_claim_queued_job` on global + per-class running counts (`verify` / `build` / `upload` / `light`); excess rows stay `queued` with `progress.phase=queued` and `message=Waiting for capacity…`. `finish_job` / `_fail_job` / `fail_stale_jobs` / the maintenance tick call `admit_waiting_jobs()` to re-spawn waiting rows. Never bypass admission by calling `_try_claim_job` on a fresh `queued` row. *Why:* unbounded parallel verify/build on one Basic dyno caused R14/H12 under load.

22. **R22 — Interrupted verify jobs MUST be Continuable (Rule W-130).** `fail_stale_jobs` stamps `result.resumable` + judged/total for verify kinds; verify streams persist each verdict to cache/overrides incrementally; the UI Continues with `override_cache=false`. *Why:* job 5bd42818 OOM'd at 61/313 and left only “Cancel and start again”.
