# Rule-Based Verification (deterministic, non-AI)

> Up: [HMO Wikibase Studio](README.md) · Sibling surface: [Upload outcomes + verify](upload-outcomes-and-verify.md)

## What this adds

A rule engine that runs every deterministic check the AI verify rubric
covered — plus Wikidata/Wikibase API checks — **without any AI**. It
complements AI verify: same review surface, no judge model, no API key,
no eval-agent subprocess.

- Engine: `backend/app/pipeline/rule_verify/` (`base.py`, `context.py`,
  `engine.py`, `persist.py`, `scope.py`, `api_fetcher.py`).
- HMO catalog: `backend/app/pipeline/rule_verify/rules/hmo.py` (30 rules).
- Job runner: `backend/app/pipeline/rule_verify_job.py` (kind
  `hmo_rule_verify`); Modal shard fan-out in `modal/modal_jobs.py`.
- Endpoints: `backend/app/routers/hmo_studio_items.py`
  (`/rule-verify/catalog|results|bulk-approve*`,
  `/rule-verify/results/entities` for the paginated drill-down,
  `/rule-verify/results/entities/{local_id}` for one entity's non-pass
  results — the item detail drawer's Rule check card) and
  `backend/app/routers/rule_verify_settings.py` (`/me/rule-verify-settings`).
- Frontend: `frontend/src/components/hmo/RuleVerificationPanel.tsx`,
  `HmoItemRuleBadge.tsx`, `HmoItemRuleVerdictCard.tsx` (drawer card),
  API module `frontend/src/api/ruleVerify.ts`.

### Drill-down + drawer gotchas

- The summary's per-rule fail/error counts are tallied over ALL verdict
  rows, but the entity table is server-paginated (50/page, offset). The
  panel resets `entityPage` whenever the view, rule filter, or debounced
  search changes — a stale page fetches an out-of-range offset and shows
  "No entries match." next to a non-zero total footer.
- The search box is a server-side `q` filter (verdict label snapshot or
  `local_id`, `ilike`), not a client-side filter over the loaded page.
- The SQL rule filter binds its JSONB containment RHS as the Python
  object — a `json.dumps` string double-encodes under asyncpg and
  matched nothing (Rule W-250; 2026-09-19: every drill-down returned
  `total: 0` while the summary counted the fails).

## Result contract

One `RuleResult` per rule per entity with an explicit state:

- `pass` — check ran, no finding.
- `fail` — check ran and found a problem (carries `field` + `message` +
  `evidence`).
- `not_relevant` — `applies_to()` said this entity is out of scope, or a
  probe backed by a protective budget did not execute (budget exhausted
  / HTTP 429 — Rule W-255): an operational guard, not a data defect.
- `error` — the check could **not** execute (API down via `_api_gate`, a
  partially-executed liveness check, a mid-check lookup failure). Never
  folds into `fail`, never reads as pass (abstain contract).

The owning `HmoStudioItemOverride` row stores a compact `rule_verdict`
JSONB (`rule_verdict_v1`: worst-state `overall` + per-rule entries;
passes store only `rule_id`+`state`). Migration `0045_rule_verify`
added the columns (also on `wikidata_item_overrides` for phase 3).

## Rule families (HMO channel)

1. Wrapped validators — SHACL (`hmo.shacl.blocking` / `hmo.shacl.warning`)
   and one rule per `hmo_export_quality` issue code
   (`hmo.quality.<code>`, 13 rules) so each code is individually blockable.
2. Deterministic checks — class/source-uri/claims presence, claim
   datatype shapes, skipped statements, MARC grounding
   (`hmo.marc.linked`, `hmo.marc.label_grounded`), within-run duplicates
   (`hmo.duplicate.in_run`, precomputed label index), language hygiene.
3. API-backed (`uses_api`) — live Wikibase liveness + label drift,
   Wikidata QID liveness (reuses `wikidata_existence`), Wikidata label
   collision probe (CirrusSearch via the duplicate-probe client, sharing
   its 1.1 s throttle, Rule W-139). Without a fetcher they return
   `error` — fail closed.

## Execution model

- Heroku fallback: single-process loop, 1 000-item chunks off the event
  loop, cancel checks between chunks (W-245/W-128 patterns).
- Modal (preferred, `MODAL_JOB_KINDS`): the claimed container loads the
  scope and fans out `run_rule_verify_shard` via `.starmap()` over
  1 500-item shards (parallel containers, cpu 1 / 4 GB each). Shard
  results are collected on a thread — the Modal starmap iterator is
  synchronous and must never block the heartbeat loop (W-244). The
  orchestrator merges per-rule tallies and owns progress + terminal
  state. Dispatch failure degrades to the local path (Rule W-15).
- Job result carries the per-rule summary only — per-entity data lives
  in the override rows and the results endpoint (R14 lesson).

## Blocking rules + approve-by-filter

- Nothing blocks automatically. `user_rule_settings` (per user) stores
  `{rule_id: true}` — the rules the curator chose to make blocking.
- Bulk approve flows through a preview:
  `POST /rule-verify/bulk-approve/preview` partitions the selection by
  the persisted verdicts + blocking set; `POST /rule-verify/bulk-approve`
  starts the existing `hmo_item_bulk_approve` job with the eligible ids,
  so tray/progress/refresh behaviour is identical to bulk approve.
- "Check all items" starts the `hmo_rule_verify` job; "Approve by
  filter" operates on the panel's current filter result.
- Advisory contract: rule results never gate upload and never auto-approve
  (eval-agent R13 parity). Only SHACL keeps its existing upload gate.

## Export (streaming)

`GET /rule-verify/export?format=json|csv&scope=failures|all` streams one
row per entity with the full entity data (labels, claims, authority
evidence) attached. Two invariants (block R58, Rule W-247):

- The JSON document streams with the same conventions as
  `export/formatters.py:json_array_stream` — header prefix first, then
  one serialised entity per yield. The data source is
  `hmo_item_views.iter_rule_verify_export_rows`: verdicts stripped +
  scope-filtered in SQL, entities streamed off a `jsonb_array_elements`
  server-side cursor — O(chunk) memory, no >55 s silent gap between
  bytes (the full merged view measured ~1.1 GB RSS and R15'd the dyno).
- The no-build case answers 409 via a cheap pre-check; the heavy
  `fetch_merged_hmo_items_cached` call runs inside the generator. The
  endpoint takes no `Depends(get_session)` — DB work uses short-lived
  `session_scope` windows so the download never pins a pooled connection
  (2026-07-04 outage pattern).

## Related rules

- `rules.md` R36 — rule-verify invariants (advisory, fail-closed API
  rules, single throttle domain for API rules, shard/heartbeat rule).
