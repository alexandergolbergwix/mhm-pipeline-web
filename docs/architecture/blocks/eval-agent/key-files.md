# Eval-Agent AI Verification System — Key files

> Up: [Eval-Agent AI Verification System](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/agent_runner.py` | Core runner: `locate_eval_agent`, `resolve_verify_state_dir/-session_dir`, `spawn_eval_agent_run`, `sse_stream`, trace persistence + session listing/reading (both layouts) |
| `backend/scripts/analyze_wikidata_verdicts.py` | Token-minimal, read-only Codex CLI analysis of only `partial`/`fail` rows from Wikidata CSV/JSON exports |
| `backend/app/pipeline/verify_job.py` | Background-job wrapper (`run_verify_job`) for the four job-backed channels; embeds partial `session_snapshot` in `run_jobs.progress` (live) and full snapshot in `run_jobs.result` (terminal) |
| `backend/app/pipeline/verify_outcome.py` | Honest `complete`/`partial` outcome + TRACE/checkpoint verdict merge (Rule W-126) |
| `backend/app/pipeline/verify_session_store.py` | `load_verify_session`: disk trace OR job-row `session_snapshot` fallback (Heroku multi-dyno) |
| `backend/app/pipeline/ner_verdict_cache.py` | NER `ai_verdict` cache keys, content fingerprints, `sanitise_stale_ai_verdict` |
| `backend/app/pipeline/run_job_params.py` | Validates verify job params; resolves tier-1 credentials before spawn |
| `backend/app/pipeline/judge_models.py` | Reads `eval-agent/config/tier1_models.yaml`; model list + availability |
| `backend/app/pipeline/ai_verifier.py` | `GEMINI_MODEL` (default tier-1), `unwrap_user_gemini_key`, legacy single-match LLM/heuristic verdict |
| `eval-agent/config/tier1_models.yaml` | Registry: `gemini-3.5-flash`, `moonshotai/Kimi-K2.5`, `deepseek-ai/DeepSeek-V4-Flash` (Qubrid OpenAI-compat) |
| `eval-agent/eval_agent/judge_models.py` | Eval-agent-side registry loader |
| `eval-agent/eval_agent/client/openai_compat_client.py` | `OpenAICompatJudge` — `/chat/completions` + JSON object parsing |
| `backend/app/routers/ai_verify.py` | Authority channel: `/runs/{id}/ai-verify/*` + `_persist_ai_verdicts_to_matches` |
| `backend/app/routers/extraction_verify.py` | NER channel: `/runs/{id}/extraction/ai-verify/*` + `_persist_ai_verdicts_to_entities` |
| `backend/app/routers/wikidata_studio.py` | Wikidata channel: `/{id}/wikidata-studio/ai-verify/*`, `_wikidata_verify_event_stream` (~line 1677) |
| `backend/app/pipeline/hmo_item_verify.py` | HMO item channel stream + `HmoStudioItemOverride.ai_verdict` persistence |
| `backend/app/pipeline/hmo_schema_verify.py` | HMO schema channel stream: `filter_schema_entries` enriches OWL metadata, `schema_verdict_query_summary` cache keys (cache-only persistence, no DB row) |
| `backend/converter/wikibase/ontology_schema_reader.py` | Parses `hebrew-manuscripts.ttl` → Wikibase datatypes; `schema_entry_metadata_by_uri()` for verify fixture enrichment (Rule W-47) |
| `eval-agent/eval_agent/ingest/hmo_wikibase_items.py` | Loads item fixture; `control_number()` / `control_numbers()` / `primary_control_number()`; `enrich_control_numbers()` across deferred links (Rule W-48) |
| `eval-agent/eval_agent/ingest/marc_extract.py` | `project()`, `merge_records()`, `project_many()` — multi-CN MARC union (Rule W-50) |
| `eval-agent/eval_agent/ingest/shacl_gate.py` | `blocking_shacl_issues()` for verify prompts (mirrors upload gate) |
| `eval-agent/eval_agent/evaluators/hmo_wikibase_item.py` | HMO item evaluator — entity_type, multi-CN grounding, SHACL block short-circuit (W-48/W-50) |
| `eval-agent/eval_agent/evaluators/hmo_wikibase_schema.py` | Schema bootstrap evaluator — prompt includes description, aliases, OWL kind/range |
| `backend/app/pipeline/agent_actions.py` | Prefab action registry (authority); siblings: `extraction_actions.py`, `wikidata_actions.py`, `hmo_item_actions.py`, `hmo_schema_actions.py` |
| `eval-agent/eval_agent/cli.py` | Vendored CLI: `run` subcommand, `--state-dir` monkey-patches session module paths |
| `eval-agent/eval_agent/orchestration/session.py` | Judging loop; emits `[TRACE] agent.verdict` per candidate for live UI (`_emit_verdict_trace`) |
| `eval-agent/config/rubrics/*.md` | Per-evaluator judging rubrics (authority, person_ner, contents_ner, provenance_ner, genre_classifier, wikidata_item(+autofix), hmo_wikibase_item(+autofix), hmo_wikibase_schema) |
| `eval-agent/config/skills/wikidata_manuscripts/` | WikiProject Manuscripts skill pack (`skill.json` + `SOURCES.md`) for Studio judges (Rules W-104 / W-124) |
| `eval-agent/eval_agent/skills/wikidata_manuscripts.py` | Entity/claim-aware skill context builder injected into Wikidata + HMO item prompts |
| `eval-agent/config/schemas/verdict.v2.json` | Verdict JSON Schema validated by `eval-agent verify` |
| `frontend/src/utils/fetchVerifySession.ts` | `jobVerifySessionSnapshot`, `fetchVerifySessionWithJobFallback` — disk session GET with job-row `progress`/`result` snapshot fallback |
| `frontend/src/hooks/useVerifyJob.ts` | Background verify job hook: poll/attach, hydrate verdicts from `progress.session_snapshot`, partial-outcome messaging |
| `backend/app/routers/judge_models.py` | `GET /api/judge-models` — tier-1 picker for verify modals |
| `frontend/src/components/Tier1ModelSelect.tsx` | Shared tier-1 judge dropdown (`useTier1Model` hook) |
| `frontend/src/components/extraction/NerVerificationModal.tsx`, `wikidata/WikidataVerificationModal.tsx`, `hmo/HmoItemVerificationModal.tsx`, `hmo/HmoSchemaVerificationModal.tsx` | Per-channel modals (NER + HMO items use `useVerifyJob`; schema still SSE) |
| `frontend/src/components/AgentFlowDiagram.tsx`, `VerdictsTable.tsx` | Shared live flow diagram and verdict table (evaluator-agnostic); Authority-specific modal removed |
| `frontend/src/api/aiVerify.ts` (+ `nerVerify.ts`, `wikidataVerify.ts`, `hmoItemVerify.ts`, `hmoSchemaVerify.ts`, `judgeModels.ts`) | SSE session clients + tier-1 model list; job-backed modals hydrate via `fetchVerifySessionWithJobFallback` |
