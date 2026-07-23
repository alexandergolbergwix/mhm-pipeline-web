# Deployment & Operations — Skills & tests

> Up: [Deployment & Operations](README.md)

## Skills

### Skill: pre-deploy docs audit (mandatory before push)
Before **any** `git push`, Heroku deploy, or `gh` action, run
[pre-deploy-docs-sync](../../../../.cursor/skills/pre-deploy-docs-sync/SKILL.md):
map the branch diff via [task-index.md](../../task-index.md) to
`docs/architecture/blocks/`, apply
[docs-architecture-sync](../../../../.codex/skills/docs-architecture-sync/SKILL.md),
then ask the user for push permission. See **Rule W-49** in `CLAUDE.md`.

### Skill: deploy the backend (Heroku)
1. Run the pre-deploy docs audit (above). Merge to `main`; push to the Heroku remote (or GitHub auto-deploy). **Ask the user before any push.**
2. Release phase runs automatically: eval-agent check → `alembic upgrade head`. Watch `heroku releases:output`.
3. Verify: `GET /api/healthz` then `/api/readyz`; `heroku logs --tail` for the lifespan startup line and `run-job-maintenance` task.
4. Remember dyno disk was wiped — first HMO/Studio requests may re-seed on-disk caches from their Postgres counterparts.

### Skill: run / add a migration
1. `cd backend && alembic revision -m "<slug>"` → new file in `app/migrations/versions/` (follow the `00NN_snake_case` naming).
2. Test locally: `alembic upgrade head` against the dev DB, plus the backend test suite.
3. Deploy — release phase applies it. For data backfills, ship a separate idempotent script and run `heroku run -- bash -lc "cd backend && python -m scripts.<name>"`.
4. If the migration touches `mazal_*`/`kima_*`, re-run the corresponding import script locally against the Heroku `DATABASE_URL`.

### Skill: add an env var
1. Add a typed field in `backend/app/settings.py` (with a safe dev default) or, for pipeline toggles, read `os.environ` at the call site as existing code does.
2. Document it in the table in [Environment variables](env-vars.md) and in `docs/DEPLOY.md`; `heroku config:set NAME=value`.
3. Unset-in-dev behavior must degrade gracefully (log-only mail, Turnstile bypass, `memory://` limiter are the models to copy).

### Skill: add a Heroku Scheduler job
1. Create `backend/scripts/run_<name>.py` mirroring `run_snapshot.py` (sys.path bootstrap, `session_scope`, printed summary, exit code).
2. Put the actual logic in `backend/app/jobs/<name>.py` so it's unit-testable.
3. Add the Scheduler entry: command `cd backend && python -m scripts.run_<name>`, at a time offset from 00:05/02:05/03:05/08:05/16:05.
4. Update Rule W-21's schedule summary in `CLAUDE.md` and `docs/DEPLOY.md` §6.6.

### Skill: deploy Modal
1. Edit `modal/modal_app.py` (keep `_bake_weights` before `add_local_dir`).
2. `cd modal && modal deploy modal_app.py` (or the `/deploy-modal` skill); note the printed URL.
3. If the URL changed: `heroku config:set MODAL_NER_URL=https://...modal.run` (with `EXTRACTION_MODE=modal`).
4. Debug cold starts with `modal app logs mhm-ner`.

### Skill: rebuild authority data in production
1. Ensure `AUTHORITY_MODE=postgres` is set.
2. Locally: `cd backend && DATABASE_URL=<heroku url> KIMA_DB_PATH=backend/data/kima/kima_index.db .venv/bin/python -m scripts.import_kima_to_postgres` (~15 s) and the Mazal equivalent with `MAZAL_DB_PATH` (~10 min).
3. Rebuild HMO Studio with `refresh_authority=true`, upload/update items, and run the canonical production E2E audit; standalone Authority re-enrichment is retired (HTTP 410).

## Tests pinning this block

- `backend/tests/unit/test_run_job_recovery.py` — claim / heartbeat / stale / orphan / dedup races (Rule W-38)
- `backend/tests/test_hmo_studio_coverage_router.py` — durable-DB-cache restore on disk miss (Rule W-39)
- `backend/tests/test_snapshot_prune_jobs.py` — scheduler job logic (~8 cases)
- `backend/tests/unit/test_wikidata_upload_guards.py` — moratorium / fail-closed upload path
- Smoke: `cd backend && .venv/bin/python -c "from app.main import app; print(len(app.routes))"` (router registration)


### Skill: canonical-first rollout preflight

Run the production migration in this order: apply Alembic head, run the
controlled HMO rebuild/upload/read-back command documented in the HMO Studio
skills page, then run the canonical backfill and readiness gate. Only after
those checks pass run `backend/.venv/bin/python backend/scripts/check_hmo_rollout.py --run-id <uuid>` before enabling `HMO_CANONICAL_FIRST`. Add `--legacy` and `--canonical` TTL paths to require identical shadow projections. The command is read-only and exits non-zero until all gates pass.
