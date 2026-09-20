# Deployment & Operations — Environment variables

> Up: [Deployment & Operations](README.md)

**Environment variables** (grep-verified call sites):

| Var | Consumer | Effect |
|---|---|---|
| `DATABASE_URL` | `settings.py` (scheme rewritten), import scripts | Heroku Postgres; SSL forced by `db.py:_ssl_connect_args` |
| `REDIS_URL` | `app/cache/redis_client.py`, rate-limit fallback | L1 inference cache + slowapi storage; absent → Postgres-only / `memory://` |
| `RATELIMIT_STORAGE_URI` | `middleware/rate_limit.py:70` | Explicit slowapi store (shares Heroku Redis Mini) |
| `MASTER_KEY`, `EMAIL_HMAC_KEY` | `crypto/keys.py` | 32-byte PII / blind-index keys; app refuses to encrypt without them |
| `EXTRACTION_MODE`, `MODAL_NER_URL` | `pipeline/extraction.py` | `modal` → POST `<MODAL_NER_URL>/extract`; HTTPS is the only backend↔Modal channel (Rule W-15) |
| `RESEARCH_AGENT_MODAL_URL`, `RESEARCH_AGENT_ENABLED` | `settings.py`, `routers/research_agent.py` | Modal AG-UI base URL (no trailing slash). Empty URL → local AG-UI stub. Wiki credentials never go to Modal (Rule W-228). Planner model is a Modal secret (`RESEARCH_AGENT_MODEL`, default Qubrid `glm-5.3-flash`; secret also holds `QUBRID_API_KEY` + `HEROKU_TOOL_BASE_URL`; Chat Completions only — no `openai:` bare prefix). Heroku settings read `QUBRID_API_KEY` / `qubrid_base_url` / `research_agent_title_model` for thread AI titles |
| `AUTHORITY_MODE` | `pipeline/authority_backend.py` (10 sites) | `postgres` (production, Rule W-28) / `local` SQLite / `modal` legacy |
| `EVAL_AGENT_ROOT`, `EVAL_AGENT_STATE_DIR` | `agent_runner.py` | Bundle location + writable verify state dir |
| `DYNO` | `agent_runner.py`, `run_job_service.py` | Heroku detection: `/tmp` state default + `WORKER_ID` prefix |
| `MORATORIUM_LIFTED`, `WIKIDATA_TEST_MODE` | `wikidata_upload.py` (legacy), `uploader.py` | Legacy env overrides. Prefer curator `upload_target` in Wikidata Studio (Rule W-103): `dry_run` \| `test` \| `live` |
| `WIKIDATA_PUBLICATION_LIVE_TOKEN`, `WIKIDATA_PUBLICATION_TEST_TOKEN` | `publication/credentials.py` via the execution worker | Optional server-held fallback tokens when the account has no saved credential for the selected wiki (W-217). The worker resolves a target-bound token only after it claims a queued execution. Absent saved credentials and fallback tokens fail closed. |
| `WEB_CONCURRENCY`, `PORT` | `start.sh` | uvicorn workers / bind port |
| `RUN_JOB_MAX_RUNNING`, `RUN_JOB_MAX_VERIFY`, `RUN_JOB_MAX_BUILD`, `RUN_JOB_MAX_UPLOAD`, `RUN_JOB_MAX_LIGHT` | `run_job_service.py` | Per-dyno job admission caps (defaults 2 / 1 / 1 / 1 / 2); excess jobs stay `queued` until a slot frees (Rule W-129) |
| `RUN_JOB_MAX_HEAVY` | `run_job_service.py` | Shared cap across the build + verify + upload slots (default 1) — a build and a bulk verify never run concurrently on one small dyno (Rule W-235) |
| `RUN_JOB_ROLE`, `RUN_JOB_WORKER_GRACE`, `RUN_JOB_MAINTENANCE_INTERVAL` | `run_job_service.py`, `scripts/start_worker.sh` | Worker split (Rule W-235): role from `DYNO` unless overridden (`web` = light kinds only, `worker`/`all` = everything); a queued heavy job waits the grace window (default 120 s) for a worker tick before web self-heals and claims it; maintenance tick cadence (worker sets 10 s) |
| `MODAL_JOBS_URL`, `MODAL_JOBS_TOKEN` | `modal_job_client.py`, `modal/modal_jobs.py` | Modal execution of `rdf_build` / `hmo_item_build` / `hmo_item_verify` / `hmo_rule_verify` / `wikidata_studio_build` (Rule W-237): empty URL → Heroku-local runner; the token must match the `mhm-jobs` Modal secret (which also carries `DATABASE_URL` and `AUTHORITY_MODE=postgres` — W-243) |
| `RULE_VERIFY_WD_PROBE_MAX`, `RULE_VERIFY_WD_QID_MAX` | `rule_verify_job.py`, `rule_verify/rules/hmo.py` | Budgets for the API-backed rule checks (Wikidata label probes / QID liveness; defaults 300 / 2 000). Exhausted budgets report `error` — never a silent pass (R56) |
| `ENV`, `COOKIE_SECURE`, `FRONTEND_ORIGIN`, `SESSION_TTL_HOURS` | `settings.py` | Prod flags, cookie policy, link bases |
| `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `ADMIN_NOTIFICATION_EMAIL`, `TURNSTILE_SECRET_KEY`/`SITE_KEY` | email/turnstile services | Unset → log-only mail / Turnstile bypass (dev) |
| `WIKIBASE_CLOUD_*` | `settings.py:52-62` | Server-held OAuth for HMO Wikibase Cloud writes |
| `GEMINI_API_KEY`, `QUBRID_API_KEY`, `HF_*_REPO`, `MHM_MODEL_DIR`, `KIMA_DB_PATH`, `MAZAL_DB_PATH`, `DISABLE_VIAF/KIMA/WIKIDATA`, `MHM_NO_NETWORK`, `DISABLE_PG_LISTENER` | pipeline modules | LLM judge keys (Gemini default tier-1; Qubrid for Kimi K2.5 + DeepSeek V4 Flash — Rule W-46), model/data locations and kill switches |
