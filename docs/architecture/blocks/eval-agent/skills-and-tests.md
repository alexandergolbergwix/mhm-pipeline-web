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
5. Frontend: new modal reusing `AgentFlowDiagram` + `VerdictsTable` + `Tier1ModelSelect`,
   API client copying the `fetch`+`getReader` SSE pattern (or `useVerifyJob` for
   large scopes). Pass `tier_model` in job/SSE params.
6. Tests: extend `test_agent_runner_sessions.py`-style coverage + a router test.

## Skill: add a tier-1 judge model
1. Add an entry to `eval-agent/config/tier1_models.yaml` (`provider`, `api_key_env`,
   `base_url` for OpenAI-compat, `supports_agentic`).
2. If `provider: openai_compat`, implement or extend `OpenAICompatJudge`; if Gemini,
   `GeminiJudge` already handles it. `session.py::_build_judge` routes by provider.
3. Document the env var in `docs/architecture/blocks/deployment/env-vars.md`; set on
   Heroku with `heroku config:set`.
4. Tests: `eval-agent/tests/test_judge_models.py` + provider client unit test;
   `backend/tests/test_run_job_params_tier_model.py` for credential validation.

## Skill: tune HMO schema AI verify after ontology/datatype changes
1. Confirm `ontology_schema_reader` inference for the property
   (`read_hmo_schema()` → `datatype`, `property_kind`, `range_uri`).
2. Update `eval-agent/config/rubrics/hmo_wikibase_schema.md` when the judge
   should accept ontology-faithful typing (CIDOC object props → `wikibase-item`,
   folio designations → `string`, etc.) — not Wikidata clone semantics.
3. Ensure `hmo_wikibase_schema.py` `build_prompt()` still passes every field
   the rubric references (`description` is mandatory — R17).
4. Re-run verify with **override cache** after deploy; export JSON and diff
   verdict counts. Stale rows keyed without `description` miss automatically
   once `schema_verdict_query_summary` includes it.

## Skill: tune HMO item AI verify after build/rubric changes (Rule W-48)
1. Confirm the run was **RDF-rebuilt** and **HMO items re-built (skip cache)**
   so fixtures carry `control_numbers` and non-generic `descriptions`.
2. Check `pipeline-output/hmo_wikibase_items.json` for a sample Person row —
   `control_numbers` should be non-empty even when `source_uri` has no digits.
3. Re-run verify with **override cache**; compare export verdict counts to the
   prior JSON. "No MARC context" and generic-description partials should drop.
4. If one shared Person spans multiple MSS, the evaluator uses the first CN —
   partial on homonyms may remain until label-hygiene work lands (Rule W-45).

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
   that never ran the job — check `run_jobs.progress.session_snapshot` (while
   running) or `run_jobs.result.session_snapshot` (after finish) first.

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

## Skill: analyze partial/fail Wikidata CSV rows with Codex

Run `backend/scripts/analyze_wikidata_verdicts.py` after downloading the
Wikidata Studio CSV or JSON export. It keeps only `partial`/`fail` rows, groups them by verdict signature and validation codes, and sends the count plus one minimal evidence sample per group to `codex exec --sandbox read-only`. `--max-examples` and `--max-clusters` are available when deeper evidence is needed; it never edits the repository. Use `--no-codex` to inspect the clustered payload locally; an export with no verdicts exits without spending model tokens.

```bash
python backend/scripts/analyze_wikidata_verdicts.py \
  ~/Downloads/run-<run-id>-wikidata-studio-items.csv \
  --report /tmp/wikidata-verdict-fixes.md
```

## Tests pinning this block

- `backend/tests/test_agent_runner_sessions.py` — session dir layouts (new +
  legacy), listing, trace replay, verdict dedupe.
- `backend/tests/test_agent_runner_subprocess_timeout.py` — 180 s idle-kill,
  stderr surfacing, cancellation.
- `backend/tests/test_verify_session_store.py` — disk vs `session_snapshot`
  precedence.
- `backend/tests/unit/test_verify_job_progress.py` — partial `session_snapshot`
  in `progress`.
- `backend/tests/test_extraction_verify_router.py`,
  `test_extraction_verify_suggested_fix.py` — NER channel routes, threshold
  sentinel, verdict persistence + suggested_fix.
- `backend/tests/test_ai_verify_unverifiable.py` — synthetic no-authority-id
  abstain.
- `backend/tests/test_hmo_schema_verify.py` — schema channel: uncached-only
  fixture, cache write-through.
- `backend/tests/unit/test_hmo_schema_verify.py` — fixture OWL enrichment,
  cache-key fields (`description`, `property_kind`, `range_uri`).
- `backend/tests/unit/test_ontology_schema_reader.py` — datatype inference
  (`hmo_source_uri` → `url`, `book_name` → `monolingualtext`, OWL metadata).
- `eval-agent/tests/test_hmo_wikibase_schema.py` — description + aliases in
  evaluator prompt (Rule W-47).
- `backend/tests/test_verify_job_hmo.py` — `hmo_item_verify` job-backed
  dispatch: unknown action, empty scope, end-to-end wiring, cache behaviour.
- `backend/tests/test_run_job_params_wikidata_verify.py` — fast Studio enqueue,
  worker-side empty-scope errors, and provider-aware Kimi credentials.
- `backend/tests/test_analyze_wikidata_verdicts.py` — partial/fail CSV/JSON filtering and compact prompt context.
- `eval-agent/tests/test_hmo_wikibase_items.py` — HMO `control_number()` from
  embedded URI ids (Rule W-45); `enrich_control_numbers()` via deferred links
  (Rule W-48); `SYSTEM-LABELED EVENT` grounding for Production / TextTradition
  (Rule W-52).
- `eval-agent/tests/test_judge_models.py`, `test_openai_compat_judge.py` —
  tier-1 registry + Qubrid OpenAI-compat judge (Rule W-46).
- `backend/tests/test_judge_models_router.py`,
  `test_run_job_params_tier_model.py` — `GET /api/judge-models`, credential gates.
- `backend/tests/unit/test_agent_runner_subprocess_timeout.py` —
  `[TRACE] agent.verdict` line parsing during subprocess read.
- `frontend/e2e/` verification specs (mocked SSE via `page.route()`).


- The Wikidata evaluator fixture carries `authority_evidence`, `work_candidate_evidence`, and resolved `local_reference_targets`; its rubric accepts source-backed natural-order names, work authors, year precision 9, clean Hebrew-only labels, and valid internal `__LOCAL:` links.
- The Wikidata CSV diagnostic export includes authority/work/local-target evidence JSON; use these fields and the statement `value_label`s when clustering partial/fail rows.
