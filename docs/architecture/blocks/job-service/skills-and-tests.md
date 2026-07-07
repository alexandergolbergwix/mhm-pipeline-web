# Background Run-Job Service — Skills & tests

> Up: [Background Run-Job Service](README.md)

## Skill: add a new job kind

1. Declare the kind: add `JOB_KIND_<NAME>` and include it in `SUPPORTED_JOB_KINDS`
   in `backend/app/models/run_job.py`. No migration needed — `kind` is a string.
2. Write the worker: `backend/app/pipeline/<name>_job.py` exposing
   `async def run_<name>_job(job_id: uuid.UUID) -> None`. Follow the canonical shape
   (see `hmo_coverage_job.py` for the simplest example):
   - open `session_scope()`, load the `RunJob`, copy what you need out of
     `job.params` / `job.run_id`, **close the session before slow work** (R12);
   - report `update_job_progress(job_id, {phase, processed, total, message})`;
   - check `await is_cancel_requested(job_id)` at loop boundaries (R8);
   - end with exactly one `finish_job(job_id, status=..., result=..., progress=...)`.
3. Dispatch it: add an `elif` branch in `run_job_service.py::_execute_job` with a
   local import (keeps startup light).
4. Params/secrets: add a branch in `run_job_params.py::prepare_job_params` for
   validation, defaults, and any `merged["_secret"]` injection (R7).
5. Frontend: call `RunJobs.start(runId, "<kind>", params)` and attach with
   `useRunJobAttachment(runId, "<kind>", sync)`; handle the 409-attach path (R14).
6. Tests: extend `backend/tests/unit/test_run_job_recovery.py` if you touched
   claiming/maintenance; otherwise add a worker test module mirroring e.g.
   `backend/tests/test_hmo_item_upload.py`.

## Skill: debug a stuck or repeatedly-failing job

1. Inspect the row:
   `heroku run -- bash -lc "cd backend && python -c \"...select from run_jobs...\""`
   or via psql: `SELECT id, kind, status, claimed_by, updated_at, error, progress FROM run_jobs WHERE run_id='...' ORDER BY created_at DESC;`
2. Read `claimed_by`: a fresh `WORKER_ID` on every retry means the dyno is
   restarting/deploying mid-job (this is exactly how the Rule W-39 coverage-cache
   incident was diagnosed).
3. `status=failed`, `error` = "Job interrupted — the server restarted…": the
   maintenance reaper fired — the owning process died or stopped heartbeating for
   5 min. Check `heroku logs` for R14/R15 memory kills or deploys at that time.
4. `status=running` but no progress movement: confirm `updated_at` is advancing
   (heartbeat alive → task exists but is blocked; look for a held DB transaction
   across external I/O, R12). If `updated_at` is frozen, the next tick will reap it.
5. Perpetual 409 on start: an active row exists — cancel it via
   `POST /runs/{run_id}/jobs/{job_id}/cancel`, or wait for the reaper.
6. Reproduce claim/maintenance behaviour locally:
   `cd backend && .venv/bin/python -m pytest tests/unit/test_run_job_recovery.py -v`.

## Skill: attach frontend UI to a background job

1. For a generic job: `const {activeJob, cancelJob} = useRunJobAttachment(runId, "rdf_build", sync)` —
   `sync` receives each new `RunJobSnapshot` (fingerprint-deduped); make it
   `useCallback`-stable or pass a ref (R13).
2. Start with `RunJobs.start(runId, kind, params)` (`frontend/src/api/runJobs.ts`);
   catch `ApiError` 409 and read `detail.job_id` to attach (R14).
3. Render progress with `JobProgressInline` (`frontend/src/components/jobs/JobProgressInline.tsx`)
   using `job.progress.{processed,total,message,phase}`.
4. For verify modals use `useVerifyJob({runId, kind, loadSession, onFailed, onComplete})` —
   it also loads the eval-agent session (`progress.session_id`) and phrases
   partial/skipped outcomes.
5. For a fire-and-forget await, `waitForRunJob` (`frontend/src/utils/waitForRunJob.ts`)
   polls until terminal.
6. Poll cadence is owned by the store (2 s while active, self-stopping); never add
   a parallel `setInterval` on the same endpoint.

## Tests pinning this block

| Test file | Contract pinned |
|---|---|
| `backend/tests/unit/test_run_job_recovery.py` | Startup respawn; queued-row claim; fresh-foreign-row rejection; unowned/own-dyno/stale reclaim; heartbeat bumps only live+owned+running rows; tick order (heartbeat → reap → respawn); create-race → `ActiveJobError` with the winner's id |
| `backend/tests/test_hmo_item_upload.py` | `hmo_item_upload` worker behaviour incl. once-per-run reconcile-PID resolution (Rule W-40) |
| `backend/tests/unit/test_hmo_item_reconcile.py` | No open transaction across the SPARQL reconcile call (R12) |
| `backend/tests/test_hmo_studio_coverage_router.py` | Coverage 409-attach + durable-cache restore around the `hmo_coverage` job (Rule W-39) |
| `backend/tests/unit/test_wikidata_upload_guards.py` | The fail-closed gate the `wikidata_upload` job routes through (Rule W-30) |
| `backend/tests/test_run_job_params_hmo_verify.py` | `hmo_item_verify` param validation: unknown action / empty scope rejected, valid scope accepted |
