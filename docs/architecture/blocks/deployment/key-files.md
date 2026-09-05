# Deployment & Operations — Key files

> Up: [Deployment & Operations](README.md)

| File | Purpose |
|---|---|
| `Procfile` | `release: bash scripts/release.sh` · `web: bash scripts/start.sh` |
| `scripts/start.sh` | Web entrypoint: exports `EVAL_AGENT_ROOT`/`EVAL_AGENT_STATE_DIR`, `cd backend`, `uvicorn app.main:app` with `--proxy-headers --forwarded-allow-ips='*'`, `$PORT`, `WEB_CONCURRENCY` workers |
| `scripts/release.sh` | Release phase: fail-fast eval-agent bundle check (`locate_eval_agent()`), then `alembic upgrade head` |
| `backend/alembic.ini` + `backend/app/migrations/` | Alembic config (`script_location = app/migrations`); versions `0001`…`0040_publication_core` |
| `backend/app/settings.py` | Pydantic settings, canonical-first cohort/percentage rollout, and Heroku `postgres://` normalization |
| `backend/app/db.py` | Engine: forced SSL on managed Postgres, `pool_size=5 + max_overflow=10`, 120 s `idle_in_transaction_session_timeout` |
| `backend/app/main.py` | Lifespan starts `run_job_maintenance_loop()` task, tears down `close_redis()` |
| `backend/app/pipeline/run_job_service.py` | Multi-dyno job runner: `WORKER_ID = "{DYNO or hostname}:{uuid8}"` (`:50-51`), `STALE_JOB_AFTER=5min`, `ORPHAN_GRACE=90s` |
| `backend/app/pipeline/agent_runner.py` | `locate_eval_agent()` (`:61`), `resolve_verify_state_dir()` (`:112`) — `/tmp/mhm-eval-agent-state` when `DYNO` is set |
| `backend/app/middleware/rate_limit.py` | slowapi storage: `RATELIMIT_STORAGE_URI` → `REDIS_URL` → `memory://`; `ssl_cert_reqs=None` for `rediss://` |
| `backend/scripts/run_snapshot.py` | Scheduler job: entity snapshots, 00:05/08:05/16:05 UTC |
| `backend/scripts/run_prune_inference_cache.py` | Scheduler job: expired cache rows, 02:05 UTC |
| `backend/scripts/run_prune_events.py` | Scheduler job: 1000-event-cap prune, 03:05 UTC |
| `backend/scripts/import_kima_to_postgres.py` / `import_mazal_to_postgres.py` | One-time idempotent SQLite → Heroku Postgres imports (KIMA ~15 s, Mazal ~10 min) |
| `backend/scripts/backfill_versioning.py` | One-shot idempotent event-log backfill (`heroku run`) |
| `backend/scripts/run_hmo_production_e2e.py` | Read-only production migration/E2E gate before canonical rollout |
| `modal/modal_app.py` + `modal/README.md` | Modal NER+genre app (deploy target, never imported by backend); economics + cold-start docs |
| `docs/DEPLOY.md` | Long-form deploy runbook (Scheduler add-on setup §6.6–6.7) |
