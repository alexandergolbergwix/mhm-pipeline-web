# MHM Pipeline — Web

A collaborative, multi-project, version-controlled web app for the Hebrew
manuscripts pipeline: MARC ingest → AI extraction → authority enrichment →
RDF graph → HMO Wikibase Studio → guarded Wikidata Studio, with curator review,
event-sourced history, and AI verification at every stage.

Designed to deploy on Heroku (FastAPI + React SPA + Postgres + Redis + Modal
for NER).

## Documentation

| Doc | Purpose |
|---|---|
| [docs/architecture/system-design.md](docs/architecture/system-design.md) | Navigation hub — runtime topology, data flow, links to every block |
| [AGENTS.md](AGENTS.md) | Agent/operator instructions and block index |
| [CLAUDE.md](CLAUDE.md) | Architectural invariants (Rules W-1…W-57) |
| [docs/testing.md](docs/testing.md) | Three-layer test pyramid (pytest, Vitest, Playwright) |
| [docs/project-hierarchy-plan.md](docs/project-hierarchy-plan.md) | Curator-facing stage map and route inventory |

Each logical block under `docs/architecture/blocks/<name>/` has a `README.md`
plus `key-files.md`, `how-it-works.md`, `rules.md`, and `skills.md`.

## Local quickstart

Prerequisites: Python 3.12+, Node 20+, Docker (for Postgres).

```bash
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web

# 1. Postgres
docker compose up -d postgres

# 2. Generate crypto keys + write .env (first time only)
python3 - <<'PY'
import secrets, pathlib
env = pathlib.Path(".env")
if not env.exists():
    template = pathlib.Path(".env.example").read_text()
    template = template.replace(
        "MASTER_KEY=replace_me_32byte_urlsafe_token",
        f"MASTER_KEY={secrets.token_urlsafe(32)}",
    ).replace(
        "EMAIL_HMAC_KEY=replace_me_32byte_urlsafe_token",
        f"EMAIL_HMAC_KEY={secrets.token_urlsafe(32)}",
    )
    env.write_text(template)
    print("wrote .env with fresh keys")
PY

# 3. Backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/alembic upgrade head
cd ..
python3 -m scripts.create_user --email you@example.org --name "Your Name"

# 4. Frontend
cd frontend
yarn install
yarn dev                                 # http://localhost:5173

# 5. Backend (another shell)
cd backend
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://localhost:5173>, sign in with the credentials you created. The
frontend dev server proxies `/api/*` to the backend.

## Common commands

```bash
# Backend tests
cd backend && .venv/bin/python -m pytest tests/ -v

# Frontend unit tests
cd frontend && yarn test:unit

# Browser e2e (one-time: npx playwright install chromium)
cd frontend && yarn test:e2e

# Typecheck frontend
cd frontend && yarn tsc --noEmit
```

See [CLAUDE.md](CLAUDE.md) for Modal deploy, Mazal/KIMA Postgres import, and
Heroku config notes.

## Architecture overview

```
Browser (React/Vite SPA)
   │ HTTPS /api/*  (session cookie + CSRF)
FastAPI on Heroku dynos
   ├─ Heroku Postgres  — read-models, event log, inference cache, run_jobs,
   │                     Mazal/KIMA authority tables
   ├─ Heroku Redis     — L1 inference cache + rate-limit storage
   ├─ Modal (HTTPS)    — NER + genre models (never imported by backend)
   ├─ eval-agent       — subprocess-only AI judge (verify sessions)
   └─ Wikibase Cloud / Wikidata — external write targets (guarded)
```

Background work runs as claimed, heartbeated **run jobs** (`run_job_service`).
AI verify jobs stream verdicts to the UI via `run_jobs.progress.session_snapshot`
while running (multi-dyno safe) and `run_jobs.result.session_snapshot` at
finish — see the [eval-agent](docs/architecture/blocks/eval-agent/README.md)
and [job-service](docs/architecture/blocks/job-service/README.md) blocks.

### Encryption layers

| What | Encrypted with | Where the key lives | Who can decrypt |
|---|---|---|---|
| `users.email`, `users.name`, invite emails | AES-256-GCM with **server `MASTER_KEY`** | Heroku Config Var | The server |
| `users.email_index` | HMAC-SHA256 with **`EMAIL_HMAC_KEY`** | Heroku Config Var | Lookup only |
| `users.password_hash` | Argon2id | n/a | Verify-only |
| User API keys (Gemini, Wikidata, Wikibase) | Envelope (DEK + KEK derived from password, wrapped per session) | `session_secret` in HTTP-only cookie | Only during an active session |

### Threat model (summary)

- **DB leak** → PII and API keys are encrypted at rest; API keys need the user's session cookie to unwrap.
- **Server compromise during a live session** → active KEKs are exposed; mitigated by short session TTL and audit logs.
- **Forgotten password** → user-derived KEK is unrecoverable; password reset wipes saved API keys (by design).

## Project layout

```
mhm-pipeline-web/
├── backend/app/          # FastAPI: routers, pipeline/, models/, auth/
├── backend/converter/    # Byte-identical mirror of desktop converter tree
├── backend/tests/        # pytest + httpx route tests
├── frontend/src/         # React routes, components/, api/, stores/
├── frontend/e2e/         # Playwright specs (canonical UI regression layer)
├── eval-agent/           # Vendored AI judge (subprocess only from backend)
├── modal/                # Modal NER app (deploy target, not a backend import)
├── docs/architecture/    # Per-block system documentation (keep in sync with code)
└── scripts/              # start.sh, release.sh, Heroku scheduler jobs
```

## License

GPL-3.0 — matching the parent pipeline project.
