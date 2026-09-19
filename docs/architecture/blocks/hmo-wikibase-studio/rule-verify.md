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
  (`/rule-verify/catalog|results|bulk-approve*`) and
  `backend/app/routers/rule_verify_settings.py` (`/me/rule-verify-settings`).
- Frontend: `frontend/src/components/hmo/RuleVerificationPanel.tsx`,
  `HmoItemRuleBadge.tsx`, API module `frontend/src/api/ruleVerify.ts`.

## Result contract

One `RuleResult` per rule per entity with an explicit state:

- `pass` — check ran, no finding.
- `fail` — check ran and found a problem (carries `field` + `message` +
  `evidence`).
- `not_relevant` — `applies_to()` said this entity is out of scope.
- `error` — the check could **not** execute (API down, budget out).
  Never folds into `fail`, never reads as pass (abstain contract).

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
  one serialised entity per yield — so the first bytes go out before the
  merged-items load and the payload never buffers in memory. A cold-cache
  merge runs as a task raced against a 10 s keepalive (JSON whitespace /
  CSV blank lines) so the stream never sits silent for Heroku's 55 s
  idle window (H15).
- The no-build case answers 409 via a cheap pre-check; the heavy
  `fetch_merged_hmo_items_cached` call runs inside the generator. The
  endpoint takes no `Depends(get_session)` — DB work uses short-lived
  `session_scope` windows so the download never pins a pooled connection
  (2026-07-04 outage pattern).

## Related rules

- `rules.md` R36 — rule-verify invariants (advisory, fail-closed API
  rules, single throttle domain for API rules, shard/heartbeat rule).
