# MHM Pipeline — Web

A collaborative, multi-project, version-controlled web rewrite of the
MHM Pipeline desktop app, designed to deploy on Heroku for **~$16/month**.

This repo contains **Phase 1** — the foundation: invite-only auth with
zero-knowledge encryption of user API keys, PII at-rest encryption, and
a working login flow against a local Postgres.

## Status

| Phase | Status |
|---|---|
| 1. Bootstrap + zero-knowledge auth | ✅ scaffolded |
| 2. Invites + password change/reset | ⏳ next |
| 3. Projects + memberships | ⏳ |
| 4. Pipeline port (MARC → authority → RDF → SHACL → Wikidata) | ⏳ |
| 5. Authority Review UI | ⏳ |
| 6. Event-sourced history + restore | ⏳ |
| 7. Real-time collaboration (Yjs over WebSocket) | ⏳ |
| 8. Liquid-glass surfaces (R3F `MeshTransmissionMaterial`) | ⏳ |
| 9. Settings + encrypted API key entry | ⏳ |
| 10. Optional Gemini-as-NER toggle | ⏳ |
| 11. Heroku deploy | ⏳ |

See `docs/ARCHITECTURE.md` (to be written in Phase 2) for the long-form
design notes.

## Local quickstart

Prerequisites: Python 3.12+, Node 20+, Docker (for Postgres).

```bash
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web

# 1. Postgres
docker compose up -d postgres

# 2. Generate crypto keys + write .env
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
.venv/bin/alembic upgrade head            # creates users + sessions
cd ..
python3 -m scripts.create_user --email you@example.org --name "Your Name"

# 4. Frontend
cd frontend
npm install
npm run dev                                # http://localhost:5173

# 5. Backend (in another shell)
cd backend
.venv/bin/uvicorn app.main:app --reload    # http://localhost:8000
```

Open <http://localhost:5173>, sign in with the credentials you just
created. The frontend proxies `/api/*` to the backend automatically.

## Architecture overview

```
┌─────────────────────────────────────────────────────────────┐
│  Browser                                                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Vite + React + TypeScript + Tailwind                │    │
│  │   - Zustand (auth state)                            │    │
│  │   - TanStack Query (server state)                   │    │
│  │   - React Router                                    │    │
│  │   - (Phase 8) R3F + MeshTransmissionMaterial        │    │
│  └────────────────────┬────────────────────────────────┘    │
│                       │  fetch /api/* with HTTP-only cookie  │
└───────────────────────┼─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI (uvicorn)                                          │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ /api/auth/{login,logout,me}                         │    │
│  │ /api/healthz, /api/readyz                           │    │
│  │ (Phase 3+) /api/projects, /api/runs, /ws, …         │    │
│  └────────────────────┬────────────────────────────────┘    │
│                       │  SQLAlchemy 2 (async) + Alembic     │
└───────────────────────┼─────────────────────────────────────┘
                        ▼
                  ┌──────────────┐
                  │  PostgreSQL  │
                  │              │
                  │  users       │  ← email_index (HMAC), email/name ciphertext
                  │  sessions    │  ← kek_wrapped (AES-GCM with session_secret)
                  │  api_keys    │  ← envelope (DEK + KEK) — zero-knowledge
                  │  (Phase 3+)  │
                  └──────────────┘
```

### Encryption layers — the cheat sheet

| What | Encrypted with | Where the key lives | Who can decrypt |
|---|---|---|---|
| `users.email`, `users.name`, invite emails | AES-256-GCM with **server `MASTER_KEY`** | Heroku Config Var | The server (any request handler) |
| `users.email_index` | HMAC-SHA256 with **`EMAIL_HMAC_KEY`** | Heroku Config Var | The server (lookup only — no decryption) |
| `users.password_hash` | Argon2id (irreversible) | n/a | Nobody (verify-only) |
| User API keys (Gemini, Wikidata, Wikibase) | AES-GCM(DEK) + AES-GCM(KEK).<br/>**KEK derived from user's password**, wrapped with random `session_secret` per session. | The `session_secret` lives in the user's HTTP-only cookie | **Only** while the user's browser is presenting the cookie. **Server + DB dump together still leak nothing.** |

### Threat model

- **Network sniffing** → HTTPS (free on Heroku)
- **DB leak** → app-level encryption: emails/names AES-GCM, secrets envelope-wrapped, password hashes Argon2id. Even an attacker with raw `pg_dump` output learns nothing about API keys, and can only see HMAC-hashed emails (not plaintext).
- **Server compromise during a live session** → KEKs in active sessions are unavoidably exposed. Mitigated by short session TTL (12h default) and audit logs (Phase 6).
- **Forgotten password** → user-derived KEK is unrecoverable by design; password reset wipes saved API keys (user warned in the UI). This is the zero-knowledge trade-off we picked.

## Project layout

```
mhm-pipeline-web/
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI factory
│   │   ├── settings.py         # env-var config
│   │   ├── db.py               # async SQLAlchemy session
│   │   ├── crypto/             # MASTER_KEY/PII + KEK/secrets envelopes
│   │   ├── models/             # users, sessions (more in later phases)
│   │   ├── schemas/            # Pydantic request/response shapes
│   │   ├── auth/               # Argon2id + session cookie + KEK wrapping
│   │   ├── routers/            # /api/auth, /api/healthz, …
│   │   └── migrations/         # Alembic
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── App.tsx, main.tsx
│   │   ├── api/client.ts       # fetch wrapper with credentials: 'include'
│   │   ├── stores/auth.ts      # Zustand auth state
│   │   ├── components/ProtectedRoute.tsx
│   │   ├── routes/Login.tsx, Home.tsx
│   │   └── styles/index.css    # Tailwind + glass-fallback CSS
│   └── (vite + tailwind config)
├── scripts/
│   ├── start.sh                # Heroku web entry (uvicorn)
│   ├── release.sh              # Heroku release entry (alembic upgrade head)
│   └── create_user.py          # dev bootstrap
├── docker-compose.yml          # local Postgres
├── Procfile, runtime.txt       # Heroku
└── .env.example
```

## Tests

```bash
cd backend
.venv/bin/python -m pytest -q
```

Phase 1 ships 10 crypto round-trip + tampering tests (`tests/test_crypto.py`).

## License

GPL-3.0 — matching the parent pipeline project.
