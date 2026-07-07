# Eval-Agent AI Verification System — Key files

> Up: [Eval-Agent AI Verification System](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/agent_runner.py` | Core runner: `locate_eval_agent`, `resolve_verify_state_dir/-session_dir`, `spawn_eval_agent_run`, `sse_stream`, trace persistence + session listing/reading (both layouts) |
| `backend/app/pipeline/verify_job.py` | Background-job wrapper (`run_verify_job`) for the four job-backed channels (authority/NER/Wikidata/HMO item); embeds `session_snapshot` in `run_jobs.result` |
| `backend/app/pipeline/verify_session_store.py` | `load_verify_session`: disk trace OR job-row `session_snapshot` fallback (Heroku multi-dyno) |
| `backend/app/pipeline/ner_verdict_cache.py` | NER `ai_verdict` cache keys, content fingerprints, `sanitise_stale_ai_verdict` |
| `backend/app/pipeline/ai_verifier.py` | `GEMINI_MODEL`, `unwrap_user_gemini_key`, legacy single-match Gemini/heuristic verdict |
| `backend/app/routers/ai_verify.py` | Authority channel: `/runs/{id}/ai-verify/*` + `_persist_ai_verdicts_to_matches` |
| `backend/app/routers/extraction_verify.py` | NER channel: `/runs/{id}/extraction/ai-verify/*` + `_persist_ai_verdicts_to_entities` |
| `backend/app/routers/wikidata_studio.py` | Wikidata channel: `/{id}/wikidata-studio/ai-verify/*`, `_wikidata_verify_event_stream` (~line 1677) |
| `backend/app/pipeline/hmo_item_verify.py` | HMO item channel stream + `HmoStudioItemOverride.ai_verdict` persistence |
| `backend/app/pipeline/hmo_schema_verify.py` | HMO schema channel stream (cache-only persistence, no DB row) |
| `backend/app/pipeline/agent_actions.py` | Prefab action registry (authority); siblings: `extraction_actions.py`, `wikidata_actions.py`, `hmo_item_actions.py`, `hmo_schema_actions.py` |
| `eval-agent/eval_agent/cli.py` | Vendored CLI: `run` subcommand, `--state-dir` monkey-patches session module paths |
| `eval-agent/config/rubrics/*.md` | Per-evaluator judging rubrics (authority, person_ner, contents_ner, provenance_ner, genre_classifier, wikidata_item(+autofix), hmo_wikibase_item(+autofix), hmo_wikibase_schema) |
| `eval-agent/config/schemas/verdict.v2.json` | Verdict JSON Schema validated by `eval-agent verify` |
| `frontend/src/components/AiVerificationModal.tsx`, `AgentFlowDiagram.tsx`, `VerdictsTable.tsx` | Shared modal chrome, live flow diagram, verdict table (evaluator-agnostic) |
| `frontend/src/components/extraction/NerVerificationModal.tsx`, `wikidata/WikidataVerificationModal.tsx`, `hmo/HmoItemVerificationModal.tsx`, `hmo/HmoSchemaVerificationModal.tsx` | Per-channel modals |
| `frontend/src/api/aiVerify.ts` (+ `nerVerify.ts`, `wikidataVerify.ts`, `hmoItemVerify.ts`, `hmoSchemaVerify.ts`) | SSE clients: `fetch` + `ReadableStream` (POST body needed, so no `EventSource`) |
