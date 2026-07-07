# Deployment & Operations — Rules

> Up: [Deployment & Operations](README.md)

1. **R1 — Every schema change MUST ship as an Alembic revision in `backend/app/migrations/versions/`; migrations run ONLY via the release phase (`alembic upgrade head`), never from request handlers.**
   *Why:* the release gate guarantees no new dyno boots against an old schema.
2. **R2 — On Heroku, anything written outside `/tmp` (and Postgres/Redis) MUST be treated as lost on the next deploy; per-run on-disk caches MUST have a Postgres write-through (Rule W-39).**
   *Why:* the 9–14 min HMO coverage rebuild kept re-running because its cache lived only on dyno disk.
3. **R3 — Verify-channel state MUST go through `resolve_verify_state_dir()`; NEVER hardcode paths under the slug.**
   *Why:* the slug is read-only on dynos; only `EVAL_AGENT_STATE_DIR`/`/tmp` is writable (Rule W-33).
4. **R4 — The backend MUST NOT import `modal/` code; backend↔Modal is HTTPS-only via `MODAL_NER_URL` (Rule W-15).**
   *Why:* keeps the dyno slug small and the trust boundary explicit; Modal deploys independently.
5. **R5 — In `modal_app.py`, `_bake_weights` MUST run before any `add_local_dir` layer.**
   *Why:* Modal rejects misordered chains and a code edit would otherwise invalidate the 3 GB weight layer.
6. **R6 — Live wikidata.org writes MUST stay behind `MORATORIUM_LIFTED=true` (or `WIKIDATA_TEST_MODE`).**
   *Why:* the April incident moratorium; the env var is the last-resort kill switch.
7. **R7 — Job state transitions to `running` MUST go through `_try_claim_job`'s atomic UPDATE; NEVER flip a `run_jobs` row manually (Rule W-38).**
   *Why:* row ownership (`claimed_by`) is the only thing making the runner multi-dyno safe without a broker.
8. **R8 — New external inference/API call sites MUST use `cache_lookup_or_call` (Redis L1 + Postgres) — never call Modal/VIAF/Gemini/Wikidata directly (Rule W-25).**
   *Why:* dev/CI degrade gracefully without Redis and warm-run latency stays flat.
9. **R9 — `REDIS_URL` absence MUST remain a supported configuration (memory rate-limit store, Postgres-only cache).**
   *Why:* dev and CI run without Redis; code paths must not fork.
10. **R10 — Scheduler scripts MUST stay self-contained (`sys.path` bootstrap + own `session_scope`) and idempotent; stagger new jobs off the 02:05/03:05 slots.**
    *Why:* they run in one-off dynos with no app context, and concurrent retention jobs contend on the same small connection pool.
11. **R11 — `DATABASE_URL` scheme rewriting and forced-SSL connect args live in `settings.py`/`db.py`; NEVER construct a second engine with different connect args.**
    *Why:* Heroku Postgres refuses non-SSL connections and ships the legacy `postgres://` scheme; a second engine silently breaks in prod.
