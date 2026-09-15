# Deployment & Operations — How it works

> Up: [Deployment & Operations](README.md)

**Web dyno.** Heroku runs `scripts/start.sh`: it exports
`EVAL_AGENT_ROOT` (defaults to the bundled `eval-agent/` in the slug) and
`EVAL_AGENT_STATE_DIR` (default `/tmp/mhm-eval-agent-state`), cds into
`backend/` (so `from app.X` imports resolve), and execs uvicorn bound to
`$PORT` with `WEB_CONCURRENCY` workers. FastAPI also serves `frontend/dist`.
The slug filesystem is read-only except `/tmp` — all three verify channels
persist session state via `resolve_verify_state_dir()`
(`agent_runner.py:101-119`), which honours `EVAL_AGENT_STATE_DIR` and falls
back to `/tmp/mhm-eval-agent-state` whenever `DYNO` is set (Rule W-33 web).
That state is ephemeral per dyno; durability lives in Postgres
(`inference_cache` verdicts, `run_jobs.progress.session_snapshot` while a verify
job runs, `run_jobs.result.session_snapshot` at finish).

**Release phase / migrations.** Every deploy runs `scripts/release.sh` before
any new dyno boots: (1) fail fast if `locate_eval_agent()` can't find
`eval_agent/cli.py`; (2) `alembic upgrade head`. Migrations are plain Alembic
under `backend/app/migrations/versions/` (40 revisions to date). A failing
release script aborts the deploy — old dynos keep running.

**Environment variables** — see [Environment variables](env-vars.md) for the
grep-verified table of every var, its consumer, and its effect.

**Heroku Scheduler** (add-on; schedule per Rule W-21 and script docstrings):
00:05 / 08:05 / 16:05 UTC `python -m scripts.run_snapshot` (cold-tier entity
snapshots); 02:05 UTC `python -m scripts.run_prune_inference_cache`; 03:05 UTC
`python -m scripts.run_prune_events`. Each script self-inserts `backend/` on
`sys.path` and opens its own `session_scope()`, so the Scheduler command is
just `cd backend && python -m scripts.<name>`.

**Background jobs across dynos (Rule W-38).** `run_job_service.py` claims jobs
via a single atomic conditional UPDATE keyed on `claimed_by = WORKER_ID`
(dyno-prefixed, so a restarted dyno can reclaim its own orphans). The
lifespan-started maintenance loop heartbeats owned rows every 60 s, fails
stale ones after 5 min, and respawns queued orphans after 90 s. Rule W-39's
corollary: any per-run result cached under `backend/state/runs/...` on disk
MUST have a Postgres write-through (`RdfArtifact`, `WikidataStudioCache`,
`HmoStudioItemCache`, `HmoCoverageCache`) because dyno disk evaporates on
every deploy.

**Worker dyno split (Rule W-235, 2026-09-15).** The Procfile adds a
`worker:` formation running `scripts/start_worker.sh` →
`python -m app.jobs_worker` (same recovery + maintenance loop, 10 s tick).
The role derives from `DYNO`: `web.*` claims only light kinds, `worker.*`
everything — so a graph build or bulk verify never shares the 512 MB web
dyno's memory with the API. Heavy jobs also share one admission cap
(`RUN_JOB_MAX_HEAVY`, default 1). If the worker is scaled to 0, a queued
heavy job is claimed by web after `RUN_JOB_WORKER_GRACE` (120 s), so
nothing queues forever. Scale it with `heroku ps:scale worker=1` after the
first deploy that ships the split.

**Modal.** `modal/modal_app.py` bundles the four NER/genre models; deployed
once via `modal deploy modal_app.py` (never imported by the backend — Rule
W-15). Weights are pre-baked via `run_function(_bake_weights)` *before* the
`add_local_dir(..., copy=True)` vendored-code layers so code edits don't
invalidate the ~3 GB weight layer. Cold start ~30–60 s on CPU-2;
`scaledown_window=300` keeps it warm 5 min; free tier covers research
workloads. The legacy `modal_authority.py` backend is superseded by
`AUTHORITY_MODE=postgres`. A third app, `modal_research_agent.py`, hosts
the Research Assistant AG-UI loop (timeout 150 s). It calls
`POST /api/research-agent/tools` with a short-lived JWT. Wiki passwords
never enter that container (Rule W-228). `/agui` must bind FastAPI
`Request` and call `dispatch_request(request, agent=agent)` (Rule W-229).

**One-time data imports.** Mazal (~2.5 M authorities → ~600 MB Postgres) and
KIMA (48 K places) are imported from local SQLite into Heroku Postgres by
`import_mazal_to_postgres.py` / `import_kima_to_postgres.py` — run *locally*
against the remote `DATABASE_URL`; both TRUNCATE + re-import (idempotent).
Re-run Mazal after any `mazal_*` schema migration (e.g. 0020
`main_marc_tag`).
