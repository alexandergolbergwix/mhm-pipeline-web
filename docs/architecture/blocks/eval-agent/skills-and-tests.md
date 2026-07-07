# Eval-Agent AI Verification System — Skills & tests

> Up: [Eval-Agent AI Verification System](README.md)

## Skill: add a new verify channel
1. Pick a channel dir name (`<thing>-verify-sessions`) and an action registry
   module (copy `hmo_schema_actions.py`); register evaluator id(s).
2. In the vendored `eval-agent/`: add `eval_agent/evaluators/<name>.py`, a
   rubric `config/rubrics/<name>.md`, and teach the ingest layer your fixture
   filename. Sync to the desktop repo if the evaluator is shared.
3. Write the stream module (copy `hmo_schema_verify.py`): fixture writer,
   `*_verdict_query_summary` (include `judge_model`!), cached-verdict event
   shaper, cache write-through, and — if a DB row owns the verdict — a
   `_persist_*` using a fresh `session_scope()`.
4. Router: `GET …/ai-verify/actions`, `POST …/ai-verify/start-stream`
   (setup inside `session_scope()`, per R9), `GET …/sessions`,
   `GET …/sessions/{sid}`. For background support add a `JOB_KIND_*`, a branch
   in `verify_job.py::_open_verify_stream`, and a `VERIFY_JOB_CHANNELS` entry.
5. Frontend: new modal reusing `AgentFlowDiagram` + `VerdictsTable`, API client
   copying the `fetch`+`getReader` SSE pattern.
6. Tests: extend `test_agent_runner_sessions.py`-style coverage + a router test.

## Skill: debug a failed verify session
1. Get `run_id` + `session_id` (SSE response header `X-Session-Id`, or job
   `params.session_id`).
2. Read `…/<channel>/<run_id>/sessions/<sid>/trace.jsonl` — look for
   `runner.error` (carries the subprocess stderr tail) or `runner.warning`
   (eval-agent missing / uncached skipped). "0 verdicts" usually means: NER
   threshold not overridden, empty fixture, or the subprocess died pre-`[STEP]`.
3. Inspect `sessions/<sid>/pipeline-output/*.json` — is the fixture non-empty
   and shaped as the evaluator expects?
4. Check `<channel>/<run_id>/runs/<latest>/results.jsonl` — verdicts written but
   not streamed points at `read_run_verdicts` fallback logs ("state_dir fix not
   active").
5. Reproduce manually:
   `cd eval-agent && GEMINI_API_KEY=… .venv/bin/python -m eval_agent.cli run
   --pipeline-output <fixture-dir> --evaluators <ev> --state-dir /tmp/dbg --no-self-verify`.
6. On Heroku remember the dyno-locality trap: the GET may be served by a dyno
   that never ran the job — check `run_jobs.result.session_snapshot` first.

## Skill: replay a session
`GET …/ai-verify/sessions` lists sessions (newest first, from `session.start`/
`session.end` trace meta); `GET …/sessions/{sid}` returns
`{session_id, run_id, events, verdicts}` — verdicts deduped last-write-wins by
`_verdict_storage_key`. The modals auto-load the latest session on open. Disk
missing? The job-snapshot fallback (R5) serves it for job-backed channels.

## Skill: inspect / bust the verdict cache
- Postgres: `SELECT * FROM inference_cache WHERE kind='ai_verdict' AND
  input_fingerprint='<cache_key>'` — the fingerprint is `canonical_hash` of the
  channel's query summary (`ner_verdict_input_fingerprint` computes it for NER).
- Redis L1: key `ic:ai_verdict:<hash>`, 7-day TTL.
- eval-agent tier: `<channel>/<run_id>/cache/verdict_cache.jsonl`.
- To force fresh judgements tick "override cache" in the modal
  (`override_cache: true` → skips reads, still writes). Stale-row hygiene:
  `sanitise_stale_ai_verdict` already hides verdicts whose `cache_key` no longer
  matches the (possibly edited) entity content.

## Tests pinning this block

- `backend/tests/test_agent_runner_sessions.py` — session dir layouts (new +
  legacy), listing, trace replay, verdict dedupe.
- `backend/tests/test_agent_runner_subprocess_timeout.py` — 180 s idle-kill,
  stderr surfacing, cancellation.
- `backend/tests/test_verify_session_store.py` — disk vs `session_snapshot`
  precedence.
- `backend/tests/test_extraction_verify_router.py`,
  `test_extraction_verify_suggested_fix.py` — NER channel routes, threshold
  sentinel, verdict persistence + suggested_fix.
- `backend/tests/test_ai_verify_unverifiable.py` — synthetic no-authority-id
  abstain.
- `backend/tests/test_hmo_schema_verify.py` — schema channel: uncached-only
  fixture, cache write-through.
- `backend/tests/test_verify_job_hmo.py` — `hmo_item_verify` job-backed
  dispatch: unknown action, empty scope, end-to-end wiring, cache behaviour.
- `frontend/e2e/` verification specs (mocked SSE via `page.route()`).
