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
   `GeminiJudge` already handles it; if `provider: typesafe`, `TypesafeJudge`
   already handles it (tuned question sets + `jev_gates.py` run in the session —
   R44). `session.py::_build_judge_for_model` routes by provider.
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

## Skill: bake off a new judge against the tier-1 judge

Run `backend/scripts/typesafe_bakeoff.py` to measure an alternative judge
(currently TypeSafe Jev, `jev-1.13.0`) against a tier-1 model on the SAME
locally rebuilt fixture — read-only, no DB/cache/DB writes:

```bash
cd backend && DATABASE_URL=… .venv/bin/python -m scripts.typesafe_bakeoff \
  --channel ner|hmo|wikidata --evaluator person_ner|hmo_wikibase_item|wikidata_item \
  --limit 25 [--tier1-reuse]
```

- Jev `state` = the byte-identical tier-1 prompt (`build_prompt`) with only the
  trailing "Return only the JSON verdict." line swapped; each rubric axis
  becomes a typed Choice question and `overall` is derived from the worst
  check state (never asked of the model).
- Every row carries a `checks` list in the rule-verify `RuleResult` shape
  (`rule_id`, `state` pass/partial/fail/not_applicable, `field`, `message`,
  `evidence`) so curators see exactly which checks failed or partially
  failed — same visual language as `RuleVerificationPanel`. The report JSON
  adds a per-check `check_summary`; an HTML sibling
  (`bakeoff_checks_*.html`) renders check chips + per-entity cards.
- Studio channels additionally ask atomic quality questions the tier-1 judge
  only does implicitly: `name_quality` (system-label/controlled-vocabulary
  acceptance) and `text_quality` Noul (generation artifacts → `name_ok`
  downgrade, rubric rule 5).
- A blocking `no` below `ROLE_CONF_GATE` (0.5) confidence is downgraded to
  `partial` inside the check list (message names the gate) — uncertainty
  routes to review, never to a hard fail. `overall` is computed from the
  final check states, so the gate is baked into the verdict.
- Tier-1 judging is slow (linear, minutes); `--tier1-reuse` reloads the most
  complete prior `eval-state/runs/*/results.jsonl` instead of re-judging.
- Gold-set certification (Phase 1 of "trust Jev"): export a labeling sheet
  with `--export-gold PATH --silver` (silver pre-fills `overall` from
  persisted curator approvals: approved→full, rejected→fail; no API calls),
  fill `label.overall` (full/partial/fail, optional axes), then score with
  `--gold PATH`. Gold mode skips tier-1 entirely and reports accuracy,
  **unsafe-approve count** (Jev `full` where gold is partial/fail — must be
  0 for certification), safe-miss count, and the confusion matrix. Baselines
  and per-axis agreement in gold mode come from the gold labels. The sheet
  rows carry a `summary` block (labels/descriptions for Studio; text/role/
  type for NER) so labels can be filled offline.
- `--determinism` runs the Jev pass twice and reports overall/axis
  stability plus changed keys (Phase 2 gate: ≥ 0.99).
- Jev-vs-Qubrid comparison report: `state/typesafe-bakeoff/jev-vs-qubrid/comparison.html`
  (quality vs gold for both judges, speed, cost with the Qubrid-rate formula,
  decision-design mechanics, production recommendation). Kimi token counts
  live in the eval-agent run `manifest.json` `stats` (not results.jsonl);
  its full-gold scoring needs the multi-hour `scripts/gold_score_tier1.py` run.
- Certification run v1 (2026-09-20/21, 1,238 Claude-labeled gold rows =
  full populations for NER + wikidata, 500 stratified HMO): gates NOT yet
  met — accuracy 0.67–0.94 per channel vs the ≥ 0.98 gate; unsafe-approve
  8/1,238 (0.65%, gate = 0). Tuning loop results: wikidata fail-class
  63/63 after the deterministic ERROR-severity validator gate (never
  confidence-gated); HMO unsafe 11 → 1 after deterministic text-artifact
  checks (per-text paren balance, identity-CN vs description-CN mismatch
  per W-137, quote-collision garble `"[א-ת]{2,}`, truncated folio ranges);
  person unsafe 1 → 0 after the dictation-note (מכתיבת יד) role rule.
  Known gold noise: the truncated-folio artifact class is labeled
  inconsistently across labeling batches (needs reconciliation).
  Production policy until gates pass: Jev primary, tier-1 confirms
  anything that is not a high-confidence pass.
- 2026-09-20 head-to-head (run `48ba6c13`, 40 items/channel, Jev vs Kimi
  K2.5 on the identical fixture): HMO (exportable items) overall agreement
  **0.975** with all axes ≥ 0.975; wikidata name_ok/type_ok **1.0** and
  per-claim statement checks P31/P1476 40/40 pass — every disagreement is a
  conservative `full→partial` review route, zero false fails in any run;
  person_ner 0.85 (borderline `type_ok` partials). Speed: Jev 3.6–4.7 s for
  40 items vs 392–397 s for the tier-1 linear pass (~90x). Cost: Jev
  $0.21/1k NER, $0.34/1k HMO, $0.63/1k wikidata ($0.042/Mtok input,
  $42/Btok console-verified; the full 1,238-row certification cost $0.42) The
  claim-check framing must name the real evidence channels (statement
  references, `work_candidate_evidence`, MARC slice, authority packs) — a
  `claim_sources`-only framing mis-flagged every supported P31/P1476 as
  unsupported.

## Tests pinning this block

- `backend/tests/test_agent_runner_sessions.py` — session dir layouts (new +
  legacy), listing, trace replay, verdict dedupe.
- `backend/tests/test_agent_runner_subprocess_timeout.py` — 180 s idle-kill,
  stderr surfacing, cancellation.
- `backend/tests/test_verify_session_store.py` — disk vs `session_snapshot`
  precedence.
- `backend/tests/unit/test_verify_job_progress.py` — partial `session_snapshot`
  in `progress`.
- `backend/tests/unit/test_verify_outcome.py` — incomplete scope → `partial`,
  TRACE/checkpoint merge (Rule W-126), synthesize missing `runner.exit`
  (Rule W-127).
- `backend/tests/unit/test_verify_job_progress_throttle.py` — throttled live
  `session_snapshot` (Rule W-127 / job-service R20).
- `eval-agent/tests/test_step_heartbeat.py` — mid-HTTP `[STEP]` keepalive.
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
- `eval-agent/tests/test_wikidata_test_live_ready.py` — test.wikidata.org live-readiness evaluator: snapshot pack, no-copy-test-ids prompt, in-batch `__LOCAL:` is W-192; clash-cleared identifierless persons are not live CREATE (W-195).
- `eval-agent/tests/test_hmo_wikibase_items.py` — HMO `control_number()` from
  embedded URI ids (Rule W-45); `enrich_control_numbers()` via deferred links
  (Rule W-48); `SYSTEM-LABELED EVENT` grounding for Production / TextTradition
  (Rule W-52).
- `eval-agent/tests/test_judge_models.py`, `test_openai_compat_judge.py` —
  tier-1 registry + Qubrid OpenAI-compat judge; Kimi `thinking` is a JSON
  object (Rule W-46).
- `eval-agent/tests/test_typesafe_client.py` — TypesafeJudge: state rewrite
  byte parity, tuned question sets per evaluator (+ the wikidata `p31_ok` /
  `duplicate_risk` contract questions) + schema fallback, universal overall
  table, missing-axis row error, 4xx-no-retry / 429-retry transport
  (Jev-primary rollout step 8).
- `eval-agent/tests/test_jev_gates.py` — deterministic gates ported from the
  bake-off harness: artifact downgrade, ERROR-severity validator gate,
  `ROLE_CONF_GATE`, claim/name_quality/match_kind overall effects, the
  wikidata duplicate gate (probe `candidates_found` → fail; update/adopted/
  absent pass; `not_run` never moves an axis), missing-P31 gate,
  schema-clean gated verdicts, session wiring (gates run for the typesafe
  provider only).
- `eval-agent/tests/test_jev_escalation.py` — escalation policy: high-conf
  full stays with Jev; partial / low-conf full → fallback verdict with the
  deciding `judge_id`; policy off / non-typesafe primary never escalate;
  cache-hit non-full rows re-check the fallback.
- `backend/tests/unit/test_typesafe_pass_through.py` — typesafe registry
  entry, credential resolution, `TYPESAFE_API_KEY` in the spawned subprocess
  env (rollout step 6).
- `eval-agent/tests/test_judge_failure_verdicts.py` and
  `eval-agent/tests/test_gated_retry.py` — provider and parse failures become
  uncached `abstain` rows with `provider_error` status, and the fallback judge
  receives exhausted primary failures (Rule W-207 / W-210 / W-211).
- `backend/tests/test_judge_models_router.py`,
  `test_run_job_params_tier_model.py` — `GET /api/judge-models`, credential gates.
- `backend/tests/unit/test_agent_runner_subprocess_timeout.py` —
  `[TRACE] agent.verdict` line parsing during subprocess read.
- `frontend/e2e/` verification specs (mocked SSE via `page.route()`).


- The Wikidata evaluator fixture carries `authority_evidence`, `work_candidate_evidence`, and resolved `local_reference_targets`; its rubric accepts source-backed natural-order names, work authors, year precision 9, clean Hebrew-only labels, and valid internal `__LOCAL:` links.
- The Wikidata CSV diagnostic export includes authority/work/local-target evidence JSON; use these fields and the statement `value_label`s when clustering partial/fail rows.

## Skill: refresh WikiProject Manuscripts judge context (Rules W-104 / W-124)

1. Re-fetch
   [Data Model](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model)
   (+ hub / Tasks if property tables changed).
2. Update `eval-agent/config/skills/wikidata_manuscripts/skill.json` slices /
   claim triggers — keep prompts compact; update `SOURCES.md` scrape date.
3. Bump `skill.json` `version` and both Studio verdict salts
   (`WIKIDATA_VERDICT_SCHEMA`, `HMO_ITEM_VERDICT_SCHEMA`).
4. Keep `wikidata_verify_evidence.py` packs in sync with any new durable
   evidence channel the builder adds (MARC / VIAF / Mazal / Wikidata / HMO).
5. Extend `eval-agent/tests/test_wikidata_manuscripts_skill.py` and
   `backend/tests/unit/test_wikidata_verify_evidence.py`.
6. Align `docs/wikidata-manuscripts-data-model.md` + mapper if builders change.

## Tests (W-104 / W-124)

- `eval-agent/tests/test_wikidata_manuscripts_skill.py` — pack load, entity
  slices, claim triggers, prompt injection for Wikidata + HMO evaluators,
  multi-channel evidence blocks in the Wikidata prompt.
- `backend/tests/unit/test_wikidata_verify_evidence.py` — evidence pack
  partitioning + MARC attach.
- `backend/tests/unit/test_wikidata_verify_scope_cache.py` — quoted CN join
  on verify fetch.
