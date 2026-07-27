# Platform Security — Rules

> Up: [Platform Security](README.md)

1. **R1 — The plaintext password MUST only be handled in `/auth/login`, `/onboarding/accept-invite`, and password change/reset; the KEK MUST never be persisted unwrapped.**
   *Why:* the zero-knowledge property (DB dump leaks no secrets) holds only while the KEK exists solely in per-request memory.
2. **R2 — Stored user secrets MUST be envelope-encrypted via `crypto/secrets.py` under the user's KEK, and MUST never be readable back through the API.**
   *Why:* read-back would turn a stolen session cookie into full credential exfiltration.
3. **R3 — Password change MUST re-wrap all DEKs and invalidate all sessions; password reset MUST wipe `api_keys` rows.**
   *Why:* wrapped DEKs under a dead KEK are unrecoverable ciphertext; pretending otherwise corrupts state silently.
4. **R4 — Every PII column MUST be AES-GCM encrypted with `encrypt_pii` and looked up only via `blind_index`; exports/responses MUST decrypt before serialising, never leak ciphertext bytes.**
   *Why:* GDPR posture and the searchability contract both depend on the pair being used together.
5. **R5 — One-time tokens (invites, resets, confirm/decision links) MUST be stored as SHA-256 digests only (`auth/tokens.py`).**
   *Why:* a DB leak alone must never yield a usable magic link.
6. **R6 — Both public endpoints MUST keep the full W-20 stack: slowapi limit, timing-parity dummy verify (login), Turnstile + honeypot + double opt-in (access-request), and byte-identical generic responses on every branch.**
   *Why:* removing any layer re-opens brute force, spam, or account enumeration; the response body/detail string is part of the contract.
7. **R7 — All outbound mail MUST route through `EmailSender`, which MUST never raise into the request path.**
   *Why:* the per-recipient throttle prevents email-bombing via our relay, and a mail-provider outage must not 5xx registration.
8. **R8 — Project routes MUST take a `require_viewer`/`require_editor`/`require_owner` dependency; NEVER re-implement membership checks inline.**
   *Why:* one audited resolver (`project_perms._resolve`) keeps the owner short-circuit and 403/404 semantics uniform.
9. **R9 — Admin mutations MUST preserve the last-admin and no-self-change guards.**
   *Why:* a lockout (zero admins) is unrecoverable without direct DB surgery.
10. **R10 — NEVER hold an open DB transaction across a slow/retrying external call (Rule W-40); commit before the call. The 120 s idle-in-transaction timeout in `app/db.py` is a backstop, not a pattern.**
    *Why:* Postgres kills the connection mid-job otherwise, and pool starvation turns unrelated logins into H12 timeouts.
11. **R11 — Rate-limit keying MUST use the left-most `X-Forwarded-For` entry (`_real_client_ip`).**
    *Why:* keying on the socket peer would rate-limit the Heroku router itself, throttling all users as one.
12. **R12 — Turnstile verification MUST fail closed (network error / non-2xx / bad JSON → reject) except for the explicit unset-secret dev bypass and `TEST_TOKEN_BYPASS`.**
    *Why:* an outage read as "human" reopens the bot funnel silently.
13. **R13 — Login / invite MUST NOT block on Wikibase Cloud wiki-account provisioning (Rule W-123).**
    Provision only with `attempt_provision=True`, treat `failed` as no-retry,
    and hard-timeout the remote call (≤5 s). `/me` never provisions.
    *Why:* Cloud retries exhausted the Heroku 30 s budget → H12 on
    `POST /api/auth/login` while the password was already valid.
