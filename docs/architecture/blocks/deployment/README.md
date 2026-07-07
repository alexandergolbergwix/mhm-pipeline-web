# Deployment & Operations — Heroku, Modal, Scheduler

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

How the system ships and stays alive: the Heroku web dyno (FastAPI serving the
built frontend), the release-phase migration gate, Heroku Scheduler retention
jobs, the Modal serverless NER app, Postgres-hosted authority databases,
Redis, one-time import scripts, and the environment-variable surface that wires
it all together.

## Contents

- [Key files](key-files.md) — Procfile, scripts, migrations, scheduler jobs, Modal app
- [How it works](how-it-works.md) — web dyno, release phase, Scheduler, background jobs, Modal, data imports
- [Environment variables](env-vars.md) — the full grep-verified env-var table
- [Rules](rules.md) — the 11 invariants (R1–R11)
- [Skills & tests](skills.md) — deploy/migration/env-var/Scheduler/Modal/authority playbooks and the pinning test suites

## Related blocks

- [Platform security](../platform-security/README.md) — the keys, Redis limiter storage, and DB timeout this block provisions
