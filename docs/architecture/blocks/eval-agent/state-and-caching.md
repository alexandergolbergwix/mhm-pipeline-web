# Eval-Agent AI Verification System — State, jobs & caching

> Up: [Eval-Agent AI Verification System](README.md)

## Background jobs and session snapshots

Four of the five channels can run headless via `POST /runs/{id}/jobs`
(legacy `authority_verify`; live kinds `ner_verify` / `wikidata_verify` /
`hmo_item_verify`; the HMO schema channel is SSE-only). `run_verify_job`
(`verify_job.py:52`) re-opens the same channel generator, folds events into
`run_jobs.progress`, honours cancel requests, and on every streamed event also
stores a partial `progress.session_snapshot` (same shape as the final snapshot)
so the UI can render verdict rows while the job is still running on another
dyno. `wikidata_verify` starts by loading its Studio scope only after its row
is committed and claimed, keeping the job-start HTTP request bounded. On
success it also stores `result.session_snapshot` =
`{session_id, run_id, events, verdicts}` built by
`snapshot_from_collected_events`. Session GETs go through
`load_verify_session` (`verify_session_store.py:60`): disk trace first, job
snapshot when the trace is missing (or has fewer verdicts) — because per-dyno
`/tmp` state is invisible to other dynos.

## State-dir layout (Rules W-14 / W-18 / W-33)

```
<base>/                                # resolve_verify_state_base():
  <channel>/<run_id>/                  #   EVAL_AGENT_STATE_DIR > /tmp/mhm-eval-agent-state (DYNO) > eval-agent/state
    cache/verdict_cache.jsonl          # eval-agent's own cache — per-RUN so sessions warm-hit each other
    runs/<run_ts>/results.jsonl        # eval-agent run artefacts (+ summary.csv, report.md)
    sessions/<session_id>/
      trace.jsonl                      # SSE audit log (every AgentEvent, timestamped)
      pipeline-output/                 # the filtered fixture fed to the subprocess
```

`_resolve_session_dir` (`agent_runner.py:561`) also accepts the **legacy**
layout (`<run_id>/<session_id>` directly, no `sessions/` level) read-only;
`list_verify_sessions` merges both and dedupes by session id. Session ids are
UTC timestamps (`new_session_id`). Cleanup is deliberately a no-op — replay
depends on the dir surviving the modal close.

`eval_agent/cli.py` honours state-dir injection twice (defense in depth):
`--state-dir` argv monkey-patches the session module's `STATE_DIR/RUNS_DIR/
CACHE_PATH/PROGRESS_PATH`, and `EVAL_AGENT_STATE_DIR` env covers older bundles.

## Verdict summary shape and caching tiers

Summary written to DB rows (parity across channels):
`{overall, name_ok, type_ok, role_ok, reasoning, suggested_fix?, model,
judged_at, cache_key, session_id, evaluator}` — `overall` ∈
pass/full · partial · fail · abstain (frontend buckets full→pass; pill adds
`unknown`).

Three cache tiers, coarsest to finest:
1. **eval-agent state-dir cache** (`cache/verdict_cache.jsonl`, per run+channel,
   keyed by the agent's own prompt hash) — skips Gemini inside the subprocess.
2. **Postgres `inference_cache` kind `ai_verdict`** (durable, 90-day, keyed by the
   channel's `*_verdict_query_summary` including `judge_model`) with **Redis L1**
   (7-day hot window) automatically in front — skips the subprocess entirely.
   HMO schema keys also include `description`, `property_kind`, and `range_uri`
   (R17) so prompt fixes invalidate stale verdicts without manual cache purge.
3. **The DB row itself** (`ai_verdict` column/payload) — what tables and pills read.

`override_cache=true` skips cache *reads* at both web and subprocess tiers
(`--no-cache`) but every fresh verdict is still written back to all tiers.

For Wikidata Studio, `wikidata_verdict_cache` canonicalises build `records`
and worker `record_ids` into the same MARC-aware key before persistence, cache
reads, and stale-verdict sanitisation. Verification never assigns a missing item to the first run record: new builder rows carry `records`, while legacy rows may recover only P3959 reference IDs; rows with no source association remain ungrounded rather than borrowing unrelated evidence.

## Frontend

All five modals share `AgentFlowDiagram` (live step/stats visualisation) and
`VerdictsTable`. SSE modals consume events via `fetch` + `res.body.getReader()`
(POST body carries action/scope, so `EventSource` can't be used). Job-backed
surfaces (`useVerifyJob`, incl. `ItemUploadPanel` pre/post-upload verify)
hydrate the same table from `run_jobs.progress.session_snapshot` while the job
runs. Modals auto-load the last session on open via the sessions endpoints.
`useApprovalStore` fast-polls (2 s) while a verify modal is open and emits
`mhm.entities.refreshed` so inline `AiVerdictPill`s update without a reload.


Wikidata item verdict payloads include `authority_evidence`, `work_candidate_evidence`, and `local_reference_targets` alongside labels, statements, validation issues, and the exact MARC slice. `wikidata_verdict_query_summary` also fingerprints statement/property labels, qualifiers, and references. Both verify and merged-read paths attach local targets before computing `w71_v1` / `records_marc_v5`, so changed evidence invalidates stale cache entries without hiding a matching persisted verdict. Diagnostic exports expose the evidence as compact JSON for offline triage.
