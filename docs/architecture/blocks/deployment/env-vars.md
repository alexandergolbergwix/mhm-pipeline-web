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
| `AUTHORITY_MODE` | `pipeline/authority_backend.py` (10 sites) | `postgres` (production, Rule W-28) / `local` SQLite / `modal` legacy |
| `EVAL_AGENT_ROOT`, `EVAL_AGENT_STATE_DIR` | `agent_runner.py` | Bundle location + writable verify state dir |
| `DYNO` | `agent_runner.py`, `run_job_service.py` | Heroku detection: `/tmp` state default + `WORKER_ID` prefix |
| `MORATORIUM_LIFTED`, `WIKIDATA_TEST_MODE` | `wikidata_upload.py` (legacy), `uploader.py` | Legacy env overrides. Prefer curator `upload_target` in Wikidata Studio (Rule W-103): `dry_run` \| `test` \| `live` |
| `WEB_CONCURRENCY`, `PORT` | `start.sh` | uvicorn workers / bind port |
| `ENV`, `COOKIE_SECURE`, `FRONTEND_ORIGIN`, `SESSION_TTL_HOURS` | `settings.py` | Prod flags, cookie policy, link bases |
| `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `ADMIN_NOTIFICATION_EMAIL`, `TURNSTILE_SECRET_KEY`/`SITE_KEY` | email/turnstile services | Unset → log-only mail / Turnstile bypass (dev) |
| `WIKIBASE_CLOUD_*` | `settings.py:52-62` | Server-held OAuth for HMO Wikibase Cloud writes |
| `GEMINI_API_KEY`, `QUBRID_API_KEY`, `HF_*_REPO`, `MHM_MODEL_DIR`, `KIMA_DB_PATH`, `MAZAL_DB_PATH`, `DISABLE_VIAF/KIMA/WIKIDATA`, `MHM_NO_NETWORK`, `DISABLE_PG_LISTENER` | pipeline modules | LLM judge keys (Gemini default tier-1; Qubrid for Kimi K2.5 + DeepSeek V4 Flash — Rule W-46), model/data locations and kill switches |
