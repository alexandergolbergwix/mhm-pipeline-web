# Public endpoints, event log, export

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

### Rule W-20 — Public endpoints carry the full spam + brute-force stack (added 2026-06-01)

Two endpoints on the web port answer unauthenticated traffic:
`POST /api/access-request` (self-service request-access form) and
`POST /api/auth/login`. Both MUST carry the defences below; the
unit + e2e suites are the regression barrier.

- **slowapi rate-limit** per client IP (extracted from
  `X-Forwarded-For`): 3/hour on `/access-request`, 10/minute on
  `/auth/login`. Storage backend: Heroku Redis Mini (provisioned
  2026-06-03, `$3/month`) via `RATELIMIT_STORAGE_URI`; falls back
  to in-process memory when `REDIS_URL` is absent (dev/CI).
- **Timing-parity dummy Argon2 verify** on `/auth/login`'s
  missing-user branch so response time cannot leak account
  existence.
- **`/access-request` only**: Cloudflare Turnstile token verified
  server-side + honeypot field that must stay empty + free-text
  justification of at least 40 characters + double opt-in (email
  confirmation token before the admin queue sees the row).
- **All outbound mail** routes through `EmailSender` (Resend wrapper
  at `app/services/email.py`) which enforces a per-recipient
  throttle of max 1 email per 60 s and 5 per 24 h via the
  `EmailThrottle` Postgres table. `EmailSender` NEVER raises into
  the request path — it logs and degrades to a no-op so a Resend
  outage cannot turn the form into a 5xx.
- **Strict account non-enumeration**: `/access-request` returns the
  same generic `202` body whether the email is brand new, already
  has a pending request, or already has an account. The
  already-has-an-account branch sends an out-of-band "someone tried
  to register with your email" notice email to the existing user
  instead of leaking the collision.

Tests: `backend/tests/test_access_request_router.py` (~13 cases),
`test_email_throttle.py` (6), `test_login_timing_parity.py` (3),
`frontend/e2e/access-request.spec.ts` (~8). Anything added to
either public endpoint MUST extend at least one of those suites.

### Rule W-21 — Every curator decision routes through the entity_event log (added 2026-06-02)

Five entity types are versioned by the per-entity event log in
`project_events`: `marc_record`, `extraction_entity`,
`authority_match`, `wikidata_override`, `wikibase_item`. Every
mutation (any PATCH/POST/DELETE that touches a curator-mutable field)
MUST route through `backend.app.versioning.apply_event(...)` BEFORE
the read-model table is updated. The read-model tables
(`run_records`, `extraction_approvals`, `authority_matches`,
`wikidata_item_overrides`) stay as O(1) current-state caches; the
`project_events` table is the authoritative source of truth.

Storage model: the hot tier in `project_events` keeps the last 1000
events per entity (pruned daily at 03:05 UTC via Heroku Scheduler;
the latest create or snapshot event is never deleted). The cold tier
in `entity_snapshot` keeps 3 snapshots per UTC day forever, written
at 00:05 / 08:05 / 16:05 UTC. Additionally, every 50th event on a
given entity auto-writes a snapshot so `state_at_rev` replay cost
stays bounded.

Heroku Scheduler job schedule summary:
- 00:05 / 08:05 / 16:05 UTC — `scripts.run_snapshot` (entity snapshots)
- 02:05 UTC — `scripts.run_prune_inference_cache` (expired cache rows)
- 03:05 UTC — `scripts.run_prune_events` (1000-event-cap prune)

Library: `jsonpatch>=1.33` (RFC 6902). `apply_event` auto-computes
each patch via `jsonpatch.make_patch(prev, new)`. Revert is implemented
as `jsonpatch.make_patch(current, target)` applied as a fresh revert
event — old events are never mutated.

History endpoints live under `/api/projects/{id}/history/*` (timeline,
diff, at, revert, snapshots). The diff/at/revert routes gate on
project membership via `require_viewer` / `require_editor`.

Tests pinning the contract: `backend/tests/test_versioning_core.py`
(~15 cases), `test_history_router.py` (~11),
`test_versioning_integration.py` (~5),
`test_snapshot_prune_jobs.py` (~8),
`frontend/e2e/history-timeline.spec.ts` (~7). Any new mutation
surface MUST extend the integration suite with a matching
`test_*_emits_*_event` case.

Backfill (one-shot, idempotent):
`heroku run -- bash -lc "cd backend && python -m scripts.backfill_versioning"`.

### Rule W-22 — Project state + history are exportable as one JSON bundle (added 2026-06-02)

Three GET endpoints under `/api/projects/{id}/export/*` produce
`application/json` attachment downloads:

- `/export` — full bundle: current state of every entity type (MARC
  records, extraction approvals, authority matches, Wikidata
  overrides, Wikibase items) plus run metadata. Supports an
  `?entity_types=` repeat-query filter to slice by type.
- `/export/snapshots` — cold-tier archive: every `entity_snapshot`
  row for the project, optionally filtered by `entity_type` and
  `since` (YYYY-MM-DD). Survives the 1000-event prune.
- `/export/history` — full event log: either every event for one
  entity (`entity_type` + `entity_id`) or every event in the
  project.

Endpoints gate on project membership via `require_viewer`. PII
columns (email, name) are decrypted before serialisation — the
export must never leak ciphertext bytes. Response shape is defined
by Pydantic models in `backend/app/schemas/export.py`. Big exports
stream via `StreamingResponse` so the 100 MB project bundle does
not load fully into RAM, and the response carries
`Content-Disposition: attachment; filename=...` so the browser
treats it as a download.

Frontend: `frontend/src/api/export.ts` (typed client + browser
download trigger) + `ExportButton` + `ExportProjectDialog`. The
project header toolbar carries the "📥 Export…" entry-point.

Tests: `backend/tests/test_export_router.py` (8). Any addition of
a new entity type to the export bundle MUST extend
`/export?entity_types=...` and add a matching
`test_export_*_filter` case.
