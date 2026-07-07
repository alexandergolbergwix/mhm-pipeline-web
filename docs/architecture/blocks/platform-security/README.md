# Platform Security — Auth, RBAC, Projects, Public-Endpoint Defenses, Secrets

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

Everything between an anonymous HTTP request and an authorized, project-scoped
handler: session-cookie auth with a zero-knowledge key hierarchy, app-level and
project-level RBAC, the self-service access-request funnel (the only public
write surface besides login), per-user encrypted API-key storage, PII
encryption at rest, and the anti-abuse stack (rate limits, Turnstile, honeypot,
double opt-in, email throttling, timing parity, non-enumeration).

## Contents

- [Key files](key-files.md) — every auth/crypto/router/service file and its purpose
- [How it works](how-it-works.md) — key hierarchy, secrets, PII, RBAC, defense stack, email, DB hygiene
- [Rules](rules.md) — the 12 invariants (R1–R12)
- [Skills & tests](skills.md) — playbooks (protected/public endpoints, key rotation, login debugging) and the pinning test suites

## Related blocks

- [Deployment & operations](../deployment/README.md) — where `MASTER_KEY`, `EMAIL_HMAC_KEY`, `RATELIMIT_STORAGE_URI`, Redis, and the Postgres timeouts are provisioned
