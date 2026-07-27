# Platform Security — How it works

> Up: [Platform Security](README.md)

**Zero-knowledge key hierarchy.** Login (`routers/auth.py:57`) is the only
place the plaintext password reaches the server. Two independent Argon2id
operations run on it: verification against `users.password_hash`
(`auth/password.py`) and deterministic KEK derivation from `users.kek_salt`
(`crypto/kek.py:45`). A fresh random 32-byte `session_secret` AES-GCM-wraps the
KEK into `sessions.kek_wrapped`; the secret is sent to the browser as half of
the HTTP-only `mhm_session` cookie (`SameSite=Lax`, `Secure` when
`COOKIE_SECURE`). Per request, `current_auth` (`auth/session.py:120`) resolves
cookie → session row → unwrapped KEK, slides expiry forward
(`SESSION_TTL_HOURS`, default 12 h), and hands handlers an `AuthContext`. The
KEK exists in memory only for the request; a DB dump alone decrypts nothing.

**Secrets.** User API keys use envelope encryption (`crypto/secrets.py`): each
secret gets its own DEK; the DEK is wrapped by the user's KEK. `/me/api-keys`
never returns plaintext — only set/last-used status. `/auth/change-password`
re-wraps every DEK under the new KEK and invalidates all sessions
(`routers/auth.py:129-198`); `/onboarding/reset-password` cannot (no old
password) so it deletes all `api_keys` rows and reports `api_keys_wiped`
(`routers/onboarding.py:177`).

**PII.** Emails and names are stored as `AES-256-GCM(MASTER_KEY)` blobs plus a
deterministic blind index for lookup (`crypto/index.py`). Access-request
free-text fields get the same treatment; client IPs are stored as truncated
`HMAC-SHA256(EMAIL_HMAC_KEY, ip)` (`routers/access_request.py:85`) — forensically
comparable, not reversible.

**RBAC — two layers.** App-level: `users.role` ∈ {admin, editor};
`require_admin` gates the admin surface. Project-level: `Project.owner_id`
short-circuits to full access (`project_perms.py:49`); otherwise a `Membership`
row (owner/editor/viewer) must exist and match the route's
`require_viewer`/`require_editor`/`require_owner` dependency. Non-members get
403; missing project 404. Admin router protects against demoting/deleting the
last admin and self-role-change (`routers/admin.py:129-203`).

**Public-endpoint defense stack (Rule W-20, verified in code).**
`POST /access-request` (`3/hour`) runs, in order: honeypot `website` field →
server-side Turnstile verify → existing-user check (silent out-of-band notice
email, same generic 202) → pending-request dedup (silent) → row insert +
double-opt-in confirm token (24 h TTL). Every path returns the identical
generic 202 body. Confirmation advances to `pending_admin` and emails the admin
approve/deny magic links (one-time decision token, SHA-256 stored, 168 h TTL).
`POST /auth/login` (`10/minute`) burns a module-level `_DUMMY_HASH` Argon2
verify on the missing-user branch (`routers/auth.py:54,75`) and uses an
identical 401 detail for both failure branches. After a successful password
verify, login may best-effort provision a Wikibase Cloud local account
(`ensure_wikibase_access(..., attempt_provision=True)`) with a **5 s hard
timeout** and no retry once status is `failed`/`active`/`skipped` (Rule W-123)
— Wikibase Cloud outages must not H12 the auth response. `/me` authorizes
without calling createaccount. `/access-request/confirm` and
the decide magic link are `10/hour`. Note: the ≥40-char justification minimum
cited in Rule W-20 lives in the `AccessRequestCreateRequest` schema, not the
router. `/onboarding/forgot-password` also refuses to betray account existence
(`ok=True` either way).

**Outbound email.** All mail goes through `EmailSender._send`
(`services/email.py:276`): per-recipient Postgres throttle first (fail-closed
if the throttle itself errors), log-only mode without `RESEND_API_KEY`, and a
blanket never-raise contract so a Resend outage degrades to a no-op instead of
a 5xx.

**DB hygiene.** `app/db.py` sets `idle_in_transaction_session_timeout=120000`
as a server setting on every non-SQLite connection — a backstop that kills
connections left "idle in transaction" by handlers hanging on non-DB work, so
a handful of leaks can't starve the 5+10 connection pool. Rule W-40's real fix
is behavioral: commit/rollback *before* any slow external call (see
`hmo_item_reconcile.py`); the timeout is not a scheduling mechanism.
