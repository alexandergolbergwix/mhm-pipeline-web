# Incident 2026-09-18: HMO item AI verify — the full debug chain

Scope: run `3494ebf5-ceb0-4702-a94e-c8f10be7f251` (18,465 resolved HMO
Wikibase items). Goal: run `audit_hmo_wikibase_item` with the Qubrid
DeepSeek V4 Flash judge over the whole scope. Everything below is
evidence-verified; the single open issue is at the end.

## Chain of failures fixed today (all verified in prod)

| # | Symptom | Root cause | Fix (commit / rule) |
|---|---|---|---|
| 1 | Job dispatch rejected: `modal dispatch … rejected: 200 {"ok":true,"spawned":true}` | `_dispatch` required HTTP 202; Modal's `@modal.fastapi_endpoint` answers 200 | Accept any 2xx with `ok:true` (W-243, R35) |
| 2 | Double execution (web dyno + Modal container racing one row) | Consequence of #1: Heroku fell back to local while Modal still spawned | Same fix |
| 3 | Progress crawled at ~1 entity/s on Modal | Progress throttle set `last_emit` BEFORE awaiting a >1 s DB write → throttle never skipped → every entity paid the write | Reset throttle AFTER the write (R34) |
| 4 | Build failed mid-run: `cannot call Transaction.commit(): connection is closed` | Idle-in-transaction across the concurrent gather / export threadpool (120 s Postgres timeout) | Commit before the gather; commit before export CPU stages (R32/R34) |
| 5 | Job killed as "Job interrupted" during export | Stale reap: only progress writes bumped `updated_at`; long quiet CPU stretch tripped it | Container lease heartbeat every 60 s; `finish_job` terminal immutability (W-244, R36) |
| 6 | Verify wedged 12+ min, whole dyno frozen, heartbeat + stale reap dead | Verify scope prep (18k-item JSONB deserialise + sync MARC attach) blocked the event loop | `asyncio.sleep(0)` yields in the merge; `to_thread` for MARC context; publisher-backed phase labels (W-245, R37) |
| 7 | `/item-status` + `/status` took ~30 s (page sat on "Build the RDF graph first") | Scalar metadata endpoints loaded the ~100 MB `resolved_entities` JSONB | Scalar-only selects; `exists()` probe; `md5(ttl_content)` server-side probe; in-process fingerprint cache for merged items (W-246, R22-rdf) |
| 8 | `Cancel` unresponsive for minutes | Flag only polled inside the judging stream | 2 s watchdog finalising CANCELLED during prep (R28) |
| 9 | Tray "View" did nothing | `?job=` param was never read by HmoStudio | Reopen the verify modal for a running `hmo_item_verify` (W-141) |
| 10 | Verify wedged 4+ h on "building MARC context" | `attach_marc_context` re-rendered MARC per item (18k × full-record projection); `merge_marc_records` deep-compared large dict lists per distinct CN set | Memoise per record; merge slices, not records (py-spy confirmed `merge_marc_records:124` as the hot loop) |

## Current architecture after these fixes

- Heroku = orchestrator: claims/admits jobs, heartbeats, persists
  `run_jobs`. Never deserialises the big blobs (scalar endpoints only).
- Modal `mhm-jobs` = executor: one detached container per
  `rdf_build` / `hmo_item_build` / `hmo_item_verify` (2 CPU / 8 GB /
  12 h timeout), lease heartbeat, crash-safe finalisation (a dead
  runner now fails the row instead of leaving a zombie — W-247).
- eval-agent bundled into the image (`EVAL_AGENT_ROOT=/root/eval-agent`,
  `PYTHONPATH=/root/backend:/root/eval-agent`), `QUBRID_API_KEY` in the
  `mhm-jobs2` secret.
- Shard fan-out design (148 parallel judges, 15–30 min full corpus):
  `docs/architecture/blocks/job-service/scale-out-plan.md` — next step.

## OPEN ISSUE: eval-agent produces 0 verdicts on Modal

After fix #10 the prep completes in minutes (verified: job
`f9eb7744` reached the judge phase end-to-end). But the run ends with:

```
runner_error: "eval-agent stopped after 0 of 18465 verdicts
without a clean exit or checkpoint (likely hung judge API, OOM,
or pipe back-pressure on the web dyno)"
outcome: partial, judged: 0, session_snapshot.events: []
```

Evidence gathered:

1. Container logs show the prep phases complete, then an unrelated
   `asyncpg InterfaceError: cannot call Transaction.rollback(): the
   underlying connection is closed` during session teardown, and
   `read_run_verdicts: no results.jsonl found under
   /root/eval-agent/state/hmo-item-verify-sessions/<run>/...` — the
   subprocess wrote nothing to the session dir.
2. No `[STEP]`/`[STATS]` lines, no spawn failure output, and no
   eval-agent stderr tail surfaced in the job error — the runner
   drained stderr but the failure reason is not reaching
   `runner_error`.
3. `eval-agent --help` spawn probe (diag_imports) exits cleanly inside
   the image; all imports OK. `QUBRID_API_KEY` present in the secret.
4. The same judge call works from the web dyno era? Unverified — the
   web dyno never got this far without R14/timeout, so this subprocess
   failure may have existed all along.

Next diagnostic steps (in order):

1. Capture the eval-agent subprocess stderr/exit code reliably: log
   the full stderr tail + returncode in `agent_runner.spawn_eval_agent_run`
   when the process exits without verdicts (currently the tail is kept
   in memory but the 0-verdict path does not include it in the job
   error).
2. Reproduce inside the image via a diag function: run
   `eval-agent run` against a ONE-item fixture with
   `QUBRID_API_KEY` and watch stdout/stderr directly.
3. Likely suspects: (a) Qubrid rejects the 18k-item pipeline-output
   fixture size or the request shape; (b) the subprocess dies on an
   env/path difference (state dir is container-local `/tmp` — fine);
   (c) DeepSeek/Qubrid rate limit or auth failure on the first call —
   would normally print to stderr.

Note: the "no results.jsonl" warnings also appear at RE-READ time
(`read_run_verdicts`) because the session dir is container-local —
expected on Modal; the DB-embedded `session_snapshot` is the fallback
path (see `verify_session_store.py`).

## Update (2026-09-19): instrumentation deployed — leading hypothesis refined

- `agent_runner.spawn_eval_agent_run` now heartbeats the subprocess
  stderr tail every 30 s (logger.warning "eval-agent stderr tail").
- Restarted verify (job `2a323bb0`) completed in ~1 min total with the
  SAME 0-verdict outcome and **zero stderr-tail lines** in the container
  logs → the subprocess printed nothing and exited almost immediately.
- New leading hypothesis: **the pipeline-output fixture is empty or
  unwritten on the container.** The verify stream writes the 18k-item
  fixture to a tmp dir before spawn; a failed/empty fixture makes
  eval-agent exit 0 with no session.start — matching every observation
  (fast completion, empty stderr, no events, scope_size never known).
- Next instrumentation (exact): in the HMO verify stream, log the
  fixture path + byte size + item count right after writing
  (`logger.warning("verify fixture %s bytes items=%s", ...)`); log the
  subprocess exit code on exit. If bytes==0, the fixture writer is the
  bug (likely the same event-loop/dyno assumption as W-245).
- Interim workaround while unfixed: run verify from the UI on a small
  filtered scope (works — the fixture is small), or accept full-corpus
  runs only after the fixture check lands.
