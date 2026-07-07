# Platform Security — Skills & tests

> Up: [Platform Security](README.md)

## Skills

### Skill: add a protected (authenticated) endpoint
1. Take `auth: AuthContext = Depends(current_auth)` for user-scoped routes, or `ctx: ProjectContext = Depends(require_viewer|require_editor|require_owner)` for project routes (the dependency reads `project_id` from the path).
2. Choose the role tier: reads → viewer, mutations → editor, membership/deletion → owner; admin surface → `Depends(require_admin)`.
3. If the handler needs a user secret, unwrap via `secrets_mod.unwrap_secret(..., kek=auth.kek)` inside the request — never cache the plaintext.
4. Add a route test in `backend/tests/` covering the 401 (no cookie) and 403 (wrong role) branches.

### Skill: add a public (unauthenticated) endpoint safely
1. Justify it — currently only login, access-request(+confirm/decide), onboarding, and health are public.
2. Decorate with `@limiter.limit("N/period")` and accept `request: Request` (slowapi requires it).
3. Return generic, branch-independent responses; if the endpoint can reveal account existence, mirror the dummy-verify / silent-notice patterns from `auth.py` / `access_request.py`.
4. Any mail it triggers goes through `EmailSender`; any human-input form gets honeypot + Turnstile.
5. Extend `test_access_request_router.py` / `test_login_timing_parity.py` style suites — Rule W-20 makes tests the regression barrier.

### Skill: rotate server-held keys / secrets
- `MASTER_KEY` and `EMAIL_HMAC_KEY` (32 bytes, `heroku config:set ...`): rotating `MASTER_KEY` requires re-encrypting every `*_encrypted` column (decrypt-old/encrypt-new script — none exists yet); rotating `EMAIL_HMAC_KEY` invalidates every blind index and stored IP hash — all `email_index` columns must be recomputed from decrypted plaintext. Do not rotate casually.
- User API keys: users simply re-enter them (`PUT /me/api-keys/{name}` overwrites in place).
- Argon2 parameter bumps: tune `auth/password.py`; hashes upgrade lazily via `needs_rehash` on next login. KEK parameters in `crypto/kek.py` are frozen — changing them orphans every wrapped DEK (forward-only migration needed).

### Skill: debug a user who can't log in / gets 401 everywhere
1. Check cookie shape: `mhm_session=<uuid>.<43-char-b64url>`; malformed → 401 "Malformed session cookie".
2. "Session not found/expired" → row deleted (logout, password change invalidates all, admin `invalidate-sessions`) or past TTL.
3. "Session verification failed" → unwrap `InvalidTag`: cookie secret doesn't match `kek_wrapped` — stale cookie after password change; user must re-login.
4. Login 401 with correct password → check `users.email_index` matches `blind_index(lower(email))`; a changed `EMAIL_HMAC_KEY` breaks all lookups.

## Tests pinning this block

- `backend/tests/test_access_request_router.py` — honeypot, Turnstile, non-enumeration, double opt-in, admin decisions (~13 cases)
- `backend/tests/test_email_throttle.py` — 1/60 s + 5/day caps (6)
- `backend/tests/test_login_timing_parity.py` — dummy-verify branch (3)
- `frontend/e2e/access-request.spec.ts` — form click paths (~8)
- Route suites under `backend/tests/` exercising 401/403 on projects, invites, admin
