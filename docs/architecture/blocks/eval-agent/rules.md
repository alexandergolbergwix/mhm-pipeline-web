# Eval-Agent AI Verification System — Rules

> Up: [Eval-Agent AI Verification System](README.md)

- **R1 — Subprocess-only trust boundary.** The FastAPI backend MUST NEVER import
  `eval_agent.*`; all communication is argv/env in, stdout line protocol +
  `results.jsonl` out. *Why:* keeps the judge's dependency tree, prompts, and
  failure modes out of the web process (Rules W-14/W-18).
- **R2 — Curators never type prompts.** Every session starts from a prefab
  `AgentAction` in a server-side registry; adding a prompt is a Python diff.
  *Why:* no free-text prompt injection surface from the browser.
- **R3 — State lives under `resolve_verify_state_dir(channel, run_id)`; sessions
  one level deeper under `sessions/<session_id>/`.** NEVER write a session into
  the legacy direct-child layout — that path is read-only fallback. *Why:* the
  verdict cache + `runs/` must sit at the per-run root so sessions warm-hit each
  other; legacy dirs predate that fix.
- **R4 — On Heroku, verify state MUST resolve to a writable dir**
  (`EVAL_AGENT_STATE_DIR` or `/tmp/mhm-eval-agent-state` when `DYNO` is set) —
  the slug filesystem is read-only (Rule W-33). *Why:* otherwise every fixture
  write and trace append 500s.
- **R5 — `/tmp` state is per-dyno and ephemeral; Postgres is the durable tier.**
  Background verify jobs MUST embed `session_snapshot` in `run_jobs.progress`
  (while running) and `run_jobs.result` (on finish), and session GETs MUST go
  through `load_verify_session`. *Why:* multi-dyno routing means the serving
  dyno usually never saw the trace file; `useVerifyJob` hydrates from the job row.
- **R6 — Every fresh verdict is write-through.** Persist to the owning DB row
  (where one exists) AND to `inference_cache` kind `ai_verdict` in a **fresh
  `session_scope()`** — the request session is closed by the time the
  finally-block runs. *Why:* durability + cross-user warm hits; reusing the
  request session raises on a closed connection.
- **R7 — Cache keys MUST include the judge model** (and any live-data
  fingerprint for autofix evaluators). *Why:* a verdict cached under an older
  model must never be served after an upgrade.
- **R8 — The pre-cache check runs BEFORE `locate_eval_agent()`.** A fully-cached
  scope must succeed on a server with no eval-agent bundle; missing bundle +
  uncached rows yields `runner.warning` + `outcome: "partial"`, never a 500.
  *Why:* Heroku dynos legitimately run cache-only.
- **R9 — SSE streaming endpoints MUST NOT hold `Depends(get_session)`.** Do
  setup in a short-lived `session_scope()` before constructing the
  `StreamingResponse`. *Why:* a minutes-long (or hung) stream pins one Postgres
  connection per session — the 2026-07-04 pool-exhaustion outage.
- **R10 — The subprocess is bounded.** Total stdout silence for 180 s kills it;
  consumer cancellation terminates it; stderr is drained concurrently and its
  tail surfaces in `runner.error`. NEVER spawn it without these guards. *Why:*
  hung Gemini calls otherwise leak processes, money, and DB connections.
- **R11 — NER "judge everything" threshold is a negative sentinel (`-1.0`),
  never `0.0`.** *Why:* eval-agent computes `float(args.threshold or default)` —
  `0.0` is falsy and silently restores the 0.85 default, dropping selections.
- **R12 — Secrets flow server-side only.** Provider API keys are resolved in
  `run_job_params` (Gemini: per-user encrypted store with env `GEMINI_API_KEY`
  fallback; Qubrid: server `QUBRID_API_KEY` only) and injected into the
  subprocess env; they MUST never appear in argv, SSE payloads, or the trace.
  *Why:* argv is visible in `ps`; traces are replayable artefacts.
- **R13 — AI verdicts are advisory.** They surface as pills; the curator always
  confirms. NEVER auto-approve from a verdict (auto-approve rules may *gate* on
  `require_ai_pass`/`respect_ai_fail`, which is still a curator-authored rule).
  *Why:* project-wide human-in-the-loop invariant.
- **R14 — Verdicts MUST stream during the subprocess, not only at the end.**
  `eval-agent` emits `[TRACE] {"type":"agent.verdict",…}` after each judged
  candidate; verify `finally` blocks skip re-yielding keys already streamed.
  Large-scope curator modals MUST use `useVerifyJob` (not direct `start-stream`
  SSE). *Why:* 2026-07-08 — 1967-item HMO autofix showed flow progress but
  VERDICTS (0) for the entire run (Rule W-44).
- **R15 — HMO item verify MUST correlate MARC by embedded control number.**
  `hmo_wikibase_items.control_number()` extracts the 8+ digit parent record id
  from `source_uri` / `local_id` (e.g. `…#Acquisition_<cn>_01`), not only `/MS_`
  paths. *Why:* 672/966 AI autofix fails on run `48ba6c13` were “no MARC
  context” because the join always returned `{}` (Rule W-45).
- **R16 — Tier-1 judge models are registry-driven.** Canonical list in
  `eval-agent/config/tier1_models.yaml` (backend reads via `locate_eval_agent()`).
  Every verify surface sends `tier_model`; `run_job_params` validates per-provider
  credentials. Non-Gemini models force linear judging (`supports_agentic: false`).
  *Why:* Qubrid Kimi has no Gemini tool-loop; cache keys already include
  `judge_model` (R7) (Rule W-46).
- **R17 — HMO schema verify prompts MUST include full ontology context.** The
  `hmo_wikibase_schema` evaluator passes `description`, `aliases`, `property_kind`,
  and `range_uri` into the judge prompt; `filter_schema_entries` enriches rows from
  `ontology_schema_reader.schema_entry_metadata_by_uri()`. Cache keys include
  `description` + OWL metadata so stale “missing description” verdicts invalidate
  after prompt fixes. *Why:* 2026-07-08 schema export — ~105 partials falsely
  claimed missing descriptions the fixture already carried (Rule W-47).
