# Post-pass hardening fixes — code issues found during the 2026-10-17 publication pass

Status: IMPLEMENTED 2026-10-17 — H1–H5 done and tested (190 backend tests green); H6–H12 remain planned.
Source: the live publication session (F1–F4 deploy → resolve → publish → adopt).
Rule of scope: fix in code every issue the pass surfaced. No behavior change
beyond the listed fixes.

## P0 — runner reliability (crashes and wedges)

### H1 — Modal runners die on `asyncpg.InterfaceError: connection is closed`
The execution workers crash after long runs (both passes; the crash text:
`cannot call Transaction.rollback(): the underlying connection is closed`).
Suspected cause: a held DB session goes stale during long maxlag sleeps.
Fix: per-claim short-lived sessions (or reconnect-on-stale in
`session_scope`), and release the session while sleeping between WBI
retries. File: `backend/app/publication/runtime.py` execution worker,
`backend/app/db.py`.

### H2 — Wedged claim loop with stale leases
A run stalled with 13 `in_flight` rows (leases held) while 1,131 rows sat
claimable and the heartbeat kept the job `running`. Fix: reap expired
in_flight leases inside the claim loop; add a no-progress watchdog (no
state change for N minutes → release leases and exit for resume).
File: `backend/app/publication/sql_repository.py` (`claim_*`),
`backend/app/pipeline/wikidata_publication_execution_job.py`.

### H3 — Modal env validation (`MASTER_KEY`, `MODAL_JOBS_TOKEN`)
The `mhm-jobs2` secret lost `MASTER_KEY` (crashed every sealed-credential
decrypt: job 56a636a4) and `MODAL_JOBS_TOKEN` (401 on every dispatch).
Fix: a `GET /healthz` on the `mhm-jobs` app listing missing required keys,
a deploy-time check in `modal/modal_jobs.py`, and make
`modal_job_client.py` surface 401 dispatches as hard errors instead of
silently continuing. Files: `modal/modal_jobs.py`,
`backend/app/pipeline/modal_job_client.py`,
`docs/architecture/blocks/deployment/env-vars.md`.

## P1 — plan vs wiki correctness (the 1,038 stale-create class)

### H4 — `absent` observation reuse outlives write passes
The run+target observation cache reuses `absent` across releases "by
design (content-independent)". After a write pass creates items, the next
dry-run still plans `create` for them; only the write preflight caught it
(1,043 fail-closed blocks). Fix: never reuse an `absent` observation on a
production target older than a TTL (e.g. 24 h) or when the previous plan
executed after the observation was taken.
File: `backend/app/publication/core.py` (`_dry_run` reuse rule),
`backend/app/publication/sql_repository.py` (`previous_plan_observations`).

### H5 — Adopt on identity in the CREATE preflight
When the final CREATE identity check finds a target whose **P3959 equals
the record's control number** (Rule W-270 channel) or whose creator is our
account, convert the action to an owned **UPDATE** instead of blocking.
The write gate already re-verifies ownership at write time. Absorbs the
whole blocked class without human clicks.
File: `backend/app/publication/wikidata_gateway.py` (`_preflight`).

### H6 — Adopt on label conflict for our own items
Production blocks creates on `Item [[Q…]] already has label …` while the
test wiki adopts the conflicting item. Adopt when the conflicting item is
ours (creator check / P3959), else keep the block.
File: `backend/converter/wikidata/uploader.py`, publication write path.

## P2 — ownership semantics ("we can modify items created by us")

### H7 — Creator check must recognise the same human owner
Three refusals hit items created by `Alexander Goldberg IL` while the write
credential is the bot-password form of the same account. Fix:
`_is_our_item` normalizes the account identity (bot-password suffix
`User@Bot` → `User`) and accepts the main-account/bot-password pair as the
same owner. Keep refusing other accounts.
File: `backend/converter/wikidata/uploader.py` (`_is_our_item`,
`_get_authenticated_user`).

### H8 — Open question: P3959 channel for works
`Q134610828`-class works carry P3959 too. Rule W-270 limits the identity
channel to manuscripts. Confirm with the curator whether works carrying an
exact P3959 may use the same channel.

## P3 — identity resolution for works (the 53 deferred)

### H9 — Sweep routing: send title+author pairs to the judge
`route_blocked_target` defers every match without a shared STRONG_ID. The
judge can verify `same_work_and_author` (title + P50) with primary
evidence; route those pairs to the dual judge instead of deferring.
File: `backend/app/publication/automatic_policy.py`.

### H10 — Review the `decide()` gate for title-only works
`decide()` requires `identifier_supported or same_work_and_author`.
Evaluate whether verified primary-evidence title matches (both judges,
cited evidence) may return `reuse_existing` for works without P50.

## P4 — hygiene (pre-existing, found while testing)

### H11 — Pre-existing test failures
7 failing unit tests + `test_wikidata_verify_heap` hangs (reproduced on
pristine HEAD cae51e5): `test_agent_actions`, `test_claim_provenance_completeness`,
`test_graph_builder_codicological_labels`, `test_hmo_canonical_wikidata`,
`test_hmo_ontology_graphbuilder_parity`, `test_wikidata_nonpassing_buckets`,
`test_wikidata_verify_scope_cache`.

### H12 — Corpus duplicate projections
4 QIDs are targeted by 2–3 records each; 38 label collisions come from two
records sharing one designation. Add a projection-time dedup/merge or an
adopt-link step keyed by P3959 + designation.

## Fixed already during the session (for the record)

- `tests/test_publication_migrations.py` died on SQLite (0044 touches
  `authority_matches`, which 0040 never creates) — test now creates legacy
  shell tables and pins `reconcile_method`.
- The Heroku build's `tsc --noEmit` gate caught the drawer's TS syntax
  error before release; keep the gate.
