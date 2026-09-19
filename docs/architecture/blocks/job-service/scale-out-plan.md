# Scale plan: Heroku orchestrates, Modal executes (fan-out)

*Written 2026-09-18 after R14 memory thrash on the 18k-item verify.*
*Research basis: Modal batch-processing guide (.map / .spawn_map /
trigger-deployed-functions), memory-snapshot guide.*

## Role split (invariant)

- **Heroku = orchestrator.** Claim, admit, heartbeat, persist `run_jobs`,
  poll/webhook. NEVER deserialise big payloads (W-246) and never run
  heavy compute (R27).
- **Modal = executor.** Containers read Postgres directly (DATABASE_URL
  in the `mhm-jobs2` secret) and write results straight back. Big
  payloads never transit Heroku.

## Current state (2026-09-18)

- `rdf_build`, `hmo_item_build`, `hmo_item_verify` (W-247) execute in ONE
  detached Modal container (`modal_jobs.py`), 2 CPU / 8 GB, lease
  heartbeat every 60 s, webhook completion.
- Verify prep yields + reports phases (W-245); verdict cache is batched
  (one round trip); merged items are fingerprint-cached in-process.

## Phase 2 — shard fan-out for `hmo_item_verify` (the big win)

Today one container judges 18,465 items serially at 4-way parallel
(~8–10 h). Optimal: split the scope into N shards and fan out.

1. **Prep container**: load scope once (merged items + MARC context),
   split deterministically by sorted `local_id` into N shards (e.g. 500
   items each), write shard payloads to a Modal Volume
   (`mhm-verify-shards/{session_id}/shard-{i}.json`).
2. **Judge containers** (`spawn_map` over shard indexes): each loads its
   shard from the Volume, runs the eval-agent judge with 4-way internal
   parallel, writes verdicts straight to `inference_cache`
   (`ai_verdict` kind) — the existing content-addressed cache keys make
   shards idempotent and resume-safe.
3. **Progress**: each judge container bumps its own counter row (or
   `run_jobs.progress` sub-key `shards/{i}`); the finalize container sums
   them into the main progress the tray renders.
4. **Finalize container**: writes the verify session snapshot +
   terminal state + completion webhook (same contract as today).

Expected: ~148 concurrent judges → 18k items in ~15–30 min. Cost is
per-second, so width costs the same as serial time.

Guardrails to keep: R28 cancel (check `cancel_requested_at` between
shards), R32 no open transactions across network phases, W-243 deploy
`modal_jobs.py` in the same change as runner code, W-244 lease heartbeat
+ terminal immutability (shards must not resurrect a finalised job —
reuse `finish_job`'s guard).

## Phase 3 — breadth for other heavy kinds

The same fan-out pattern applies to `rdf_build`'s per-record mapping and
any future bulk export. Keep `spawn_map` + external-result-store as the
default shape for every new heavy job kind; single-container execution
is the fallback, not the target.

## Non-goals

- Streaming judge calls through Heroku (R14 again).
- Heroku-side batch workers (R27 worker dyno) — Modal is strictly
  cheaper and wider; Heroku stays admission-only.

## Batched + cursor-based data access (mandatory for heavy paths)

Approved as policy (2026-09-19): heavy consumers MUST NOT materialise
the whole 18k-item scope in one step. Every heavy path uses:

1. **Cursor pagination** — keyset queries on `(local_id)` ordered
   ascending (`WHERE local_id > :cursor ORDER BY local_id LIMIT N`),
   never OFFSET. Each page is processed and freed before the next is
   fetched, so peak memory is one page.
2. **Batches** — write paths (verdict persistence, fixture writes,
   exports) flush per batch of ~500; read paths fetch pages of ~500.
3. **No whole-corpus JSONB deserialise** — `resolved_entities` reads
   become per-item or per-page; the merged view is served from the
   fingerprint cache or paginated endpoints (W-246).
4. **Resumable cursors** — the job `progress.checkpoint.cursor` stores
   the last processed `local_id` so a restarted job continues from the
   page boundary instead of the beginning.

Applies to: verify scope prep, fixture writing, verdict persistence,
merged-items endpoints, and any new bulk job.Violating this pattern needs a
written justification in the owning block's rules.
