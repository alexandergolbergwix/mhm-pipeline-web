# Run jobs (claim, admission, progress, publish)

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

### Rule W-38 — Run jobs are claimed, heartbeated, and reconciled (added 2026-07-04)

The background job runner (`run_job_service.py`) is multi-dyno safe via
row ownership, not a queue broker:

- **Ownership**: `run_jobs.claimed_by` stores `WORKER_ID`
  (`{DYNO-or-hostname}:{uuid8}`). `_try_claim_job` is a single atomic
  conditional UPDATE — a job is claimable iff `queued`, or `running` and
  (unowned | mine | same-dyno prefix | heartbeat stale). Never flip a row
  to `running` outside this claim path.
- **Heartbeat**: a lifespan-started `run_job_maintenance_loop()` ticks
  every 60 s — bump `updated_at` on rows whose asyncio task is alive here,
  then `fail_stale_jobs()` (`STALE_JOB_AFTER` = 5 min), then respawn
  queued orphans older than `ORPHAN_GRACE`. Workers no longer need to
  call `update_job_progress` merely to stay alive.
- **Dedup**: partial unique index `uq_run_jobs_active_kind` on
  `(run_id, kind) WHERE status IN ('queued','running')` (migration 0030);
  `create_job` catches the `IntegrityError` race and re-raises
  `ActiveJobError` → the existing 409-attach protocol.
- `claimed_by` is intentionally NOT in `serialise_job` — API/frontend
  payloads are unchanged.

Tests: `tests/unit/test_run_job_recovery.py` (claim/heartbeat/tick/race
cases). Any change to claiming, staleness, or the maintenance loop MUST
extend that suite.

### Rule W-59 — Commit Wikidata verify jobs before materialising their Studio scope (added 2026-07-11)

A 294-item Wikidata Studio AI verification called `POST /api/runs/{id}/jobs`
twice and both requests hit Heroku’s 30-second H12 router timeout. The generic
job parameter validator was calling `_fetch_wikidata_verify_items()` inside
the request; that path can load or rebuild the Studio cache and is exactly the
slow work the run-job service exists to move off the request path. No job row
was available for the modal to attach to, so it remained visually “running”
with zero events.

`prepare_job_params` now validates only the action and tier-1 credentials for
`wikidata_verify`, then commits the queued row. After ownership is claimed,
`verify_job._open_verify_stream` builds/enriches the selected Studio scope,
checks its minimum size, and converts scope errors into a terminal failed job.
Future job-start handlers MUST not materialise a build, network-enriched scope,
or large candidate collection before `create_job` commits. Test:
`test_run_job_params_wikidata_verify.py`.

---

### Rule W-61 — Rejected verify-job enqueue requests MUST reset the curator modal (added 2026-07-11)

The same 294-item Wikidata Studio verification that hit Heroku H12 never
created a `run_jobs` row. `useVerifyJob.start` had already set `running=true`,
but did not clear it when `RunJobs.start` rejected. The modal caught the error
yet continued to show **Stop**, an empty flow, and zero verdicts — a phantom
job the curator could neither inspect nor cancel.

`useVerifyJob` now rolls back `running` and `jobId`, then rethrows the failed
enqueue so the modal displays the API error and leaves the curator able to
retry. Every job-start UI that sets optimistic local state MUST undo that state
when the start request rejects; only a returned or 409-attached job may be
shown as cancellable. Test: `frontend/tests/unit/useVerifyJob.spec.ts`.

---

### Rule W-64 — Verify-job progress MUST count distinct candidates, not stream events (added 2026-07-11)

A Wikidata AI verify job with a 294-item scope displayed **395 / 294** while it was running. The worker updated its counter from `agent.stats.judged`, incremented it again for streamed verdicts, and the Wikidata stream re-emitted results from disk during final persistence. These are transport events, not independent candidates, so the progress display became impossible to trust.

`verify_job.py` now derives progress only from the set of stable candidate local IDs carried by `agent.verdict`, ignores aggregate stats for counting, and caps the count at the `session.start.scope_size`. Replayed cache or disk verdicts therefore update the live snapshot but never advance progress twice. Every verify channel MUST preserve a stable candidate identity in its verdict event. Test: `test_verify_job_progress.py`.

---

### Rule W-105 — Studio “Approve all visible” MUST run as a background job (added 2026-07-25)

Clicking **Approve all visible** on a large HMO/Wikidata Studio filter (often
1k–2k rows) used to fire sequential browser `PATCH …/override` chunks. That
blocked the UI for a long time, risked Heroku H12 / browser aborts, and left
curators with no job-tray progress — often looking like “it did nothing”.

Invariant:

1. New `run_jobs` kinds `hmo_item_bulk_approve` and
   `wikidata_item_bulk_approve` (separate kinds so the active-job unique index
   is per channel).
2. Params: non-empty `local_ids` (deduped, capped at 5000) validated in
   `prepare_job_params`; worker upserts override rows with `approved=true`,
   emits `apply_event` (Rule W-21), commits in batches, reports progress, and
   honours `is_cancel_requested`.
3. Studio panels (`HmoItemsPanel`, `WikidataItemsPanel`) start the job via
   `ensureRunJob` + `useRunJobAttachment` / job tray; single-row approve stays
   a synchronous PATCH.

Tests: `backend/tests/test_studio_item_bulk_approve.py`,
`frontend/e2e/hmo-wikibase-items.spec.ts`,
`frontend/e2e/wikidata-item-table.spec.ts`.

### Rule W-106 — All Studio / RDF builds MUST run as `run_jobs` with inline progress (added 2026-07-25)

Authority refresh + RDF rebuild + HMO item export (and IIIF manifest generation)
routinely exceed Heroku's 30s HTTP timeout. Keeping **Build / Rebuild** on the
request path either H12'd or blocked the curator UI with only a spinner.

Invariant:

1. New kinds `hmo_item_build` and `hmo_manifest_build` join the existing
   `rdf_build` and `wikidata_studio_build` kinds. `POST …/build-items` and
   `POST …/build-manifests` always enqueue and return **201** `serialise_job`
   (409-attach when an active job of that kind exists).
2. Workers live in `hmo_item_build_job.py` / `hmo_manifest_build_job.py`;
   item builds call `execute_hmo_item_build` (authority → RDF → export) with
   progress callbacks. Generic `POST /runs/{id}/jobs` with the same kinds is
   equivalent.
3. Curator UI (`ItemBuildPanel`, HMO Studio manifests, Wikidata force-rebuild,
   RDF Graph) uses `ensureRunJob` + `useRunJobAttachment` + `JobProgressInline`
   — never a synchronous browser wait on the heavy build body.

Tests: `backend/tests/test_hmo_studio_build_items_router.py`,
`test_hmo_studio_ttl_restore.py`, `frontend/e2e/wikidata-studio.spec.ts`
(force-rebuild starts `wikidata_studio_build`).

### Rule W-107 — All Studio publish/upload paths MUST run as `run_jobs` (added 2026-07-25)

IIIF manifest upload and HMO item dry-run still ran inline on the HTTP
request. Live Wikidata ``POST …/wikidata-studio/upload`` was a parallel
sync landmine next to the job-backed UI. Large corpora H12'd or blocked
the curator with only a busy spinner.

Invariant:

1. ``hmo_manifest_upload`` — dry-run and live enqueue from
   ``POST …/upload-manifests`` (201); worker audits intent, uploads with
   per-manifest progress, caches the report.
2. ``hmo_item_upload`` — dry-run **and** live enqueue from
   ``POST …/upload-items`` (live still requires Wikibase Cloud config;
   dry-run does not). Missing item build → 409 before enqueue.
3. ``POST …/wikidata-studio/upload`` enqueues ``wikidata_upload`` (same as
   ``POST /jobs``); never runs ``upload_items`` on the request path.
4. Curator UI uses ``ensureRunJob`` + ``JobProgressInline`` for manifest
   upload (parity with item upload / Wikidata upload panels).

Remaining long tasks still sync (not publish): gated QuickStatements GET,
bulk Wikidata reconcile preview, MARC ``create_run``, RDF ``POST /build``
legacy sync body, single-item push, SHACL validate, section import.

Tests: `test_hmo_studio_upload_manifests_router.py`,
`test_hmo_studio_upload_items_router.py`, `test_hmo_item_upload_job.py`.

### Rule W-108 — Job-backed publish MUST refresh curator tables on terminal success (added 2026-07-25)

After HMO/Wikidata upload jobs moved off the request path (Rule W-107), the
progress bar could finish while **Publication status** stayed on
`NEW (NOT UPLOADED)` until a manual reload. Root cause: `useRunJobAttachment`
derived only from `selectActiveJob`, which drops terminal rows, and the
shared `/jobs/mine?active=true` poll never delivered the final snapshot —
so `onUploaded` / table `load()` never ran.

Invariant:

1. `useRunJobAttachment` prefers the explicitly tracked job id (including
   succeeded/failed/cancelled) while that id is set; the per-job poll
   `upsertJob`s every snapshot (including terminal) before clearing the id.
2. `jobFingerprint` includes result/error presence so a terminal body is
   distinct from the last running progress tick.
3. Upload panels `upsertJob` on start and call `onUploaded` once per job id
   on succeed; HMO `HmoItemsPanel` reloads the items list immediately
   (`handleLifecycleRefresh`) so Publication status reflects
   `status` / `upload_outcome` without a page refresh.

Tests: `frontend/tests/unit/renderStable.spec.ts` (terminal fingerprint).

### Rule W-110 — Live Studio upload MUST patch changed rows, not flicker the table (added 2026-07-25)

Mid-run Publication status updates after Rule W-108 still **unmounted** the
HMO/Wikidata review table: `ItemUploadPanel` called `onUploaded` →
`load()` → `setLoading(true)` while the UI gated the table on `!loading`.

Invariant:

1. Pass-1 upload progress includes `item_outcome` and a rolling
   `recent_item_outcomes` window **after** each item write (not before).
2. The curator panel patches matching rows by `local_id` via
   `frontend/src/utils/studioUploadProgress.ts` — no mid-run full list
   reload and no loading gate that hides an already-populated table.

**Extension to AI verify (2026-07-31).** The same rule was only half-applied:
the verify modal accumulated verdicts live, but the panel was told about them
solely through `onVerdictsLanded`, which fires on **completion**. A curator
watching 303/313 stream past therefore stared at `— —` on every row until the
run ended.

3. `WikidataVerificationModal` publishes `onVerdictStream(overallByItemId)`
   whenever its verdict map changes, and the panel patches the matching rows by
   `local_id`. Only `overall` crosses the boundary — the table renders nothing
   else, and passing the full event would re-render every row per verdict.
4. The patch is identity-preserving: rows whose verdict did not change are
   returned unchanged (`return prev` when nothing changed), so React re-renders
   the minimum and the table never flickers.
5. A streamed value outside the rendered enum is ignored rather than shown as
   `unknown` — an unrenderable verdict is worse than no verdict.

Tests: `frontend/tests/unit/wikidataVerdictStream.spec.ts`.
3. Terminal succeed/fail MAY silent-reload from the API to reconcile with
   durable mappings/audit rows; the table stays mounted when items exist.
4. Bulk approve / verify may still throttle silent reloads, but MUST NOT
   flicker by unmounting the table.

Tests: `test_hmo_item_upload_job.py` (progress item outcomes),
`frontend/tests/unit/studioUploadProgress.spec.ts`.

### Rule W-112 — Pipeline job progress MUST use 1-based steps with a unit label (added 2026-07-25)

HMO **Rebuild (skip cache)** showed `0 / 3` for the whole authority-refresh
phase. That counter is three pipeline steps (authority → RDF → export), but
progress was written as `processed=0` while step 1 was actively running, so
the tray looked stuck / impossible for a ~2k-item corpus.

Invariant:

1. An in-progress pipeline step reports its 1-based index (`1/3`, `2/3`,
   `3/3`), never `0/N` while work is underway.
2. Progress carries `unit` (e.g. `steps`) plus a "Step N of M: …" message.
3. Job tray / inline progress show `N / M steps · message`, not a bare
   fraction that reads like item counts.

**Follow-up (Studio build, 2026-07-30).** The Wikidata Studio build job showed a
fixed `0 / 1` for the whole build because `execute_studio_build` was one opaque
await.

A first fix wired the `progress_cb(done, total)` that
`WikidataItemBuilder.build_all` already accepted and nothing passed. That was
not enough, and the bar still read `0 / 1` in production: for
`source=canonical` the item loop is the *fourth* of five stages, and the three
before it (row load, canonical read-back, transliteration prewarm) are the slow
ones. Reporting only the loop leaves the bar dead for most of the build.

Invariant for this job:

1. Outer progress is the **1-based build phase** (`unit=steps`) from
   `BUILD_PHASES`, published from the first write onward — never `0 / N`.
   `_phase_plan(source)` drops the phases a legacy build never reaches, so the
   denominator is honest for the path actually taken.
2. The record loop nests underneath as `sub_processed` / `sub_total` /
   `sub_unit=records` (Rule W-113), never as the outer counter — records and
   phases are different units.
3. `build_all` reports from a `run_in_threadpool` worker, so both callbacks only
   mutate an in-memory `state` dict; a single `_publish_build_progress` task
   owns every DB write and publishes at most every 1.5 s (Rule W-128).
4. Terminal progress switches to `unit=items` and names both totals
   ("Built 313 items from 105 records").
5. Optional enrichment gets its own phase rather than hiding inside another —
   "mining provenance prose" (Rule W-140) reports `x / n records` underneath and
   never fails the build.

Tests: `test_wikidata_studio_build_job.py` —
`test_build_job_reports_phase_steps_and_nested_records`,
`test_legacy_source_omits_the_canonical_phases`,
`test_progress_falls_back_to_the_first_step_for_an_unknown_phase`.

Tests: `backend/tests/unit/test_hmo_item_build_progress.py`.

### Rule W-113 — Long pipeline steps MUST report nested sub-progress (added 2026-07-25)

HMO **Rebuild (skip cache)** still looked stuck after W-112: the outer bar
showed `1 / 3 steps` for the entire authority refresh of a ~2k-entity corpus,
with no indication of how far through entities (or RDF records) the worker
was. Curators could not tell a hung job from a slow match.

Invariant:

1. Outer progress remains 1-based pipeline steps (`unit=steps`, Rule W-112).
2. Long steps also write nested fields on the same progress blob:
   `sub_processed`, `sub_total`, `sub_unit`, `sub_message`.
3. Authority refresh reports `sub_unit=entities` (throttled ~1s from
   `re_enrich_run`); RDF rebuild reports `sub_unit=records`.
4. `JobProgressInline` and the job tray render a second, thinner bar when
   `sub_total > 0`, and include the sub-fraction in the tray label.

Tests: `backend/tests/unit/test_hmo_item_build_progress.py`,
`backend/tests/unit/test_authority_re_enrich_progress.py`.

Wikidata Studio **upload** uses the same outer/inner contract: `1/2` then
`2/2` `steps` (write items, add connections) with `sub_unit=items|links`,
plus `eta_seconds` / `steps[]` for the upload modal only (Rule W-192).

### Rule W-129 — Run jobs MUST pass admission control before claim (added 2026-07-27)

Capacity review for 50 parallel curators found the real queue already
existed (`run_jobs.status=queued`) but every enqueue immediately
`spawn_job()`'d into the same Basic web dyno — N concurrent verify/build
tasks caused R14/H12 long before Postgres or Redis became the bottleneck.

Invariant:

1. **Postgres `run_jobs` stays the queue** — no Celery/RQ broker. Admission
   gates the transition `queued → running`, not job creation (201/409-attach
   unchanged).
2. **Slot classes** — `verify` (all `*_verify`), `build` (RDF/Studio/HMO
   build/coverage/schema bootstrap), `upload` (item/manifest/Wikidata upload),
   `light` (`extraction`, `*_bulk_approve`). Defaults on Basic: global **2**,
   verify/build/upload **1** each, light **2**.
3. **Denied claim** — row stays `queued`; `progress.phase=queued`,
   `message=Waiting for capacity…`; task exits without claiming.
4. **`admit_waiting_jobs()`** — re-spawns queued rows without live asyncio
   tasks after `finish_job`, `_fail_job`, `fail_stale_jobs`, and every
   maintenance tick (not only the 90 s orphan grace).
5. **Atomicity** — `pg_advisory_xact_lock` + in-process asyncio lock around
   count + claim so two spawns cannot oversubscribe a class cap.
6. **Env caps** — `RUN_JOB_MAX_RUNNING`, `RUN_JOB_MAX_VERIFY`,
   `RUN_JOB_MAX_BUILD`, `RUN_JOB_MAX_UPLOAD`, `RUN_JOB_MAX_LIGHT`.

This prevents crash-on-load from parallel heavy jobs; it does **not**
replace a separate worker dyno for HTTP isolation.

Tests: `backend/tests/unit/test_run_job_recovery.py` (admission cases),
`frontend/tests/unit/waitForRunJob.spec.ts` (`runJobQueuedMessage`).

### Rule W-141 — The job tray's "View" MUST reopen the surface that started the job (added 2026-07-31)

"View" on a Wikidata AI verify job navigated to `/runs/<id>/wikidata-studio` and
stopped there: the run's live progress lives in the **Verify with AI** modal, so
the curator landed on the table with no way back to the run they clicked.

Invariant:

1. `jobRunHref` appends `?job=<id>` for every kind whose progress lives in a
   modal rather than on the page (`JOB_KINDS_WITH_MODAL`: `wikidata_verify`,
   `wikidata_upload`, `hmo_item_verify`, `ner_verify`). Page-level jobs stay a
   plain route.
2. The receiving surface opens that modal once on mount and then **removes the
   param** (`setSearchParams(..., {replace: true})`), so closing the modal does
   not immediately reopen it and the back button behaves. On Wikidata Studio,
   `wikidata_verify` reopens **Verify with AI**; `wikidata_upload` reopens the
   **live upload progress** modal (`WikidataUploadProgressModal`).
3. A new job kind with modal-hosted progress must be added to the set, or its
   tray entry silently becomes a dead end.

Tests: `frontend/tests/unit/runJobsHref.spec.ts`.


### Rule W-147 — Verify scope preparation MUST report steps, not a static string (added 2026-08-02)

The verify job published one progress row for the whole pre-judge phase:

```json
{"phase": "preparing", "processed": 0, "total": 0,
 "message": "Loading Studio scope…"}
```

With `total: 0` and a fixed string there is **no way to distinguish real work from
a hang**. That is how the Rule W-144 duplicate-probe 429 stall was reported —
twice — and on both occasions the job was in fact progressing normally, once at
2 min 20 s into a phase whose healthy duration is 1-3 minutes.

`_fetch_wikidata_verify_items` now takes `phase_cb` / `progress_cb` and announces
`VERIFY_SCOPE_PHASES`: assembling Studio scope → loading MARC records → checking
Wikidata for duplicates → building verification evidence. `verify_job` owns a
publisher task on the same pattern as the Studio build job: the callbacks only
mutate shared state, the task performs every DB write, throttled to 1.5 s
(Rule W-128 keeps polls light on the web dyno).

1. **1-based steps with a unit label** (Rule W-112) — `Step 3 of 4: checking
   Wikidata for duplicates`.
2. **Nested counts where a step has them** (Rule W-113) — the duplicate step
   reports `12 of 40 lookups`, which is exactly the loop that stalled.
3. **A progress callback MUST NOT be able to break the work it reports on.**
   `_report` swallows and logs a callback error; a broken progress bar must never
   fail a verify job.

No frontend change was needed: `JobProgressInline` already renders `sub_*` from
the build job's Rule W-113 work.

### Rule W-148 — Subset AI verification MUST retain freshly persisted verdicts when only derived evidence scope changes (added 2026-08-02)

Verifying a subset of the corpus re-derives `local_reference_targets` and the MARC
slice against the *subset*, not the whole run. A verdict persisted moments earlier
therefore hashed a wider evidence scope than the read path could reproduce, and
sanitisation dropped it — the curator watched verdicts stream in and then vanish.

Invariant: a change confined to **derived** evidence scope MUST NOT invalidate a
verdict. `sanitise_stale_wikidata_verdict` accepts on the stable key and rewrites
`cache_key` forward, so the verdict survives and the reproducible key catches up.
A change to what the item ASSERTS still invalidates.

### Rule W-149 — Wikidata verify fingerprints MUST use the curator override-merged item state (added 2026-08-02)

Verify hashed the raw Studio-cache item; the review table hashed the item with
curator overrides applied. Any item a curator had edited therefore read as stale
immediately after being judged.

Invariant: the verify scope applies overrides **before** fingerprinting, so the
state that keys a verdict is the state the table shows — one item state, not two.

### Rule W-150 — Wikidata verdict reads MUST validate against the retained pre-derived projection (added 2026-08-02)

`fetch_merged_wikidata_items` enriches rows for display: ledger QIDs, resolved
`__LOCAL:` display labels, upload outcomes. Validating a stored verdict against
that enriched row compares it to a shape that never existed when it was written.

Invariant: `sanitise_stale_wikidata_verdict` takes a `stable_item` — the retained
pre-derived projection — and validates against it. Presentation enrichment is
presentation, and MUST NOT key a verdict.

### Rule W-151 — Wikidata verdict reads MUST reuse the persist-slim projection (added 2026-08-02)

The read path built its own approximation of the slim item. Any divergence from
`slim_item_for_verdict_persist` is an unreproducible key.

Invariant: the read path calls `slim_item_for_verdict_persist` itself. One
function, both sides — the same reasoning as Rule W-136's "one projection".

### Rule W-152 — Wikidata stable verdict keys MUST ignore subset-derived `__LOCAL:` display labels (added 2026-08-02)

A `__LOCAL:` statement's `value_label` is filled in from whichever items are in
scope, so the same statement carries a label in a full run and none in a subset.
Hashing it made subset verdicts unreadable by the full-run table and vice versa.

Invariant: `wikidata_verdict_stable_input_fingerprint` strips `value_label` from
`__LOCAL:` statements. The reference itself still keys the verdict; its rendering
does not.

### Rule W-167 — Verify scope cache partitioning lives in one function (added 2026-08-05)

The loop that decides which items are already judged and which go to the judge was
copy-pasted between `start_verify_stream` and `verify_job`. A rule added to one path
was silently absent from the other — which is how a re-judge trigger (Rule W-157)
would have ended up existing only in the interactive path and never in the
background job the curator actually runs.

Invariant: `partition_wikidata_verify_cache` is the single implementation, and both
entry points call it. A test asserts the hand-rolled loop is gone from both.
