# Platform Security — Key files

> Up: [Platform Security](README.md)

| File | Purpose |
|---|---|
| `backend/app/auth/session.py` | Cookie format `mhm_session=<sid>.<secret_b64>`, `create_session`, `current_auth` dependency (cookie → `AuthContext{user, session, kek}`), sliding expiry |
| `backend/app/auth/project_perms.py` | `require_project_role(...)` factory; `require_viewer` / `require_editor` / `require_owner`; owner short-circuit |
| `backend/app/auth/admin.py` | `require_admin` app-level gate (`user.role == "admin"`) |
| `backend/app/auth/password.py` | Argon2id verify-path hashing (t=3, m=64 MiB, p=4) + `needs_rehash` opportunistic upgrade |
| `backend/app/auth/tokens.py` | One-time tokens: plaintext returned once, only SHA-256 persisted |
| `backend/app/crypto/kek.py` | Argon2id password → 32-byte KEK; AES-256-GCM wrap/unwrap with per-session secret |
| `backend/app/crypto/secrets.py` | Envelope encryption for user API keys: per-secret DEK, DEK wrapped by user KEK |
| `backend/app/crypto/pii.py` | AES-256-GCM PII columns under server `MASTER_KEY` (nonce ∥ ciphertext, self-contained) |
| `backend/app/crypto/index.py` | Blind index `HMAC-SHA256(EMAIL_HMAC_KEY, lower(email))` for searchable encrypted columns |
| `backend/app/crypto/keys.py` | Load/validate `MASTER_KEY` + `EMAIL_HMAC_KEY` (32 bytes; b64url/hex/b64 accepted) |
| `backend/app/routers/auth.py` | `/auth/login` (rate-limited, timing-parity), `/logout`, `/me`, `/change-password` (DEK re-wrap) |
| `backend/app/routers/access_request.py` | Public `POST /access-request` + confirm + admin queue/approve/deny + decision magic link |
| `backend/app/routers/onboarding.py` | Invite preview/accept, forgot/reset password (reset wipes API keys — zero-knowledge consequence) |
| `backend/app/routers/invites.py` | Admin invite CRUD; `create_invitation_for_email` shared with approve path |
| `backend/app/routers/admin.py` | Admin stats/users/projects, role changes, session invalidation, project transfer |
| `backend/app/routers/projects.py` | Project CRUD + memberships, gated by `require_viewer/editor/owner` |
| `backend/app/routers/api_keys.py` | `/me/api-keys` — write-only key store (gemini/wikidata/huggingface); no plaintext read-back |
| `backend/app/routers/health.py` | `/healthz` (no DB) and `/readyz` (SELECT 1); unauthenticated |
| `backend/app/middleware/rate_limit.py` | slowapi limiter keyed by left-most `X-Forwarded-For`; Redis storage via `RATELIMIT_STORAGE_URI`/`REDIS_URL`, else `memory://` |
| `backend/app/services/email.py` | `EmailSender` (Resend wrapper): throttled, log-only without `RESEND_API_KEY`, never raises |
| `backend/app/services/email_throttle.py` + `models/email_throttle.py` | Per-recipient 1/60 s + 5/day Postgres throttle, `SELECT … FOR UPDATE`, blind-indexed recipient |
| `backend/app/services/turnstile.py` | Cloudflare Turnstile siteverify; fail-closed on network/JSON errors, bypass when secret unset (dev) |
| `backend/app/services/auth_me.py` | Builds `LoginResponse`/`MeResponse` incl. Wikibase access fields |
| `backend/app/db.py` | Async engine (`pool_size=5, max_overflow=10`), forced SSL on managed Postgres, 120 s `idle_in_transaction_session_timeout` backstop |
