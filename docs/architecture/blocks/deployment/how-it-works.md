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
under `backend/app/migrations/versions/` (34 revisions to date). A failing
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

**Modal.** `modal/modal_app.py` bundles the four NER/genre models; deployed
once via `modal deploy modal_app.py` (never imported by the backend — Rule
W-15). Weights are pre-baked via `run_function(_bake_weights)` *before* the
`add_local_dir(..., copy=True)` vendored-code layers so code edits don't
invalidate the ~3 GB weight layer. Cold start ~30–60 s on CPU-2;
`scaledown_window=300` keeps it warm 5 min; free tier covers research
workloads. The legacy `modal_authority.py` backend is superseded by
`AUTHORITY_MODE=postgres`.

**One-time data imports.** Mazal (~2.5 M authorities → ~600 MB Postgres) and
KIMA (48 K places) are imported from local SQLite into Heroku Postgres by
`import_mazal_to_postgres.py` / `import_kima_to_postgres.py` — run *locally*
against the remote `DATABASE_URL`; both TRUNCATE + re-import (idempotent).
Re-run Mazal after any `mazal_*` schema migration (e.g. 0020
`main_marc_tag`).
