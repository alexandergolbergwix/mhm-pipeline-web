# Background Run-Job Service — Key files

> Up: [Background Run-Job Service](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/run_job_service.py` | Core: create/claim/heartbeat/reap/respawn, spawn + dispatch, progress, finish, cancel, serialise |
| `backend/app/pipeline/run_job_params.py` | Bounded per-kind request validation + server-side secret injection; slow scope loading stays in workers |
| `backend/app/routers/run_jobs.py` | HTTP API: list mine, list per run, start (201/409), get, cancel |
| `backend/app/models/run_job.py` | `RunJob` model, kind/status constants, `uq_run_jobs_active_kind` partial unique index |
| `backend/app/migrations/versions/0024_run_jobs.py` | Creates the `run_jobs` table + original active-kind index |
| `backend/app/migrations/versions/0030_run_job_claims.py` | Adds `claimed_by` + `uq_run_jobs_active_kind` |
| `backend/app/main.py` (lifespan, ~L35-60) | Startup: `fail_stale_jobs` → `recover_interrupted_jobs` → start `run_job_maintenance_loop`; shutdown cancels the loop first |
| `backend/app/pipeline/extraction_job.py` | `extraction` worker |
| `backend/app/pipeline/authority_re_enrich_job.py` | `authority_re_enrich` worker |
| `backend/app/pipeline/verify_job.py` | `ner_verify` / `authority_verify` / `wikidata_verify` / `hmo_item_verify` worker (shared) |
| `backend/app/pipeline/rdf_build_job.py` | `rdf_build` worker |
| `backend/app/pipeline/wikidata_studio_build_job.py` | `wikidata_studio_build` worker |
| `backend/app/pipeline/wikidata_upload_job.py` | `wikidata_upload` worker (dry-run + live) |
| `backend/app/pipeline/hmo_coverage_job.py` | `hmo_coverage` worker |
| `backend/app/pipeline/hmo_schema_bootstrap_job.py` | `hmo_schema_bootstrap` worker |
| `backend/app/pipeline/hmo_item_upload_job.py` | `hmo_item_upload` worker |
| `frontend/src/stores/runJobs.ts` | Zustand store: 2 s poll of `/jobs/mine?active=true`, out-of-order response guard, `upsertJob` for WS pushes |
| `frontend/src/hooks/useRunJobAttachment.ts` | Generic attach-to-active-job hook (fingerprint-guarded `sync` callback) |
| `frontend/src/hooks/useVerifyJob.ts` | Verify job lifecycle (four verify kinds): poll/attach, upserts into `useRunJobs` for the global tray, hydrate from `progress.session_snapshot`, session loading |
| `frontend/src/utils/fetchVerifySession.ts` | `jobVerifySessionSnapshot`, `fetchVerifySessionWithJobFallback` — multi-dyno session hydration |
| `frontend/src/utils/waitForRunJob.ts`, `frontend/src/components/jobs/JobProgressInline.tsx` | Await-terminal helper + inline progress widget |
| `backend/tests/unit/test_run_job_recovery.py` | Pins claiming, heartbeat, maintenance tick, and the create-race contract |
