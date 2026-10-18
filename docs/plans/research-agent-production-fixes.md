# Research Agent — production-grade remote-agent fixes

Status: IMPLEMENTED 2026-10-17 — P0–P3 applied (56 research tests green). Provider policy: QUBRID ONLY (the Gemini fallback was removed). Trigger: live failure screenshot —
"how many entities weve added to wikidata" → empty canvas + "The agent run
stopped responding before finishing.", duplicated user rows.

## Confirmed failure chain (2026-10-17, Modal logs)

1. `run_agui_detached` dies at `from app.services.research_agent.jev_prefilter
   import …` → `ModuleNotFoundError: No module named 'app'`
   (`modal_research_agent.py:731`). The research image does not mount
   `/root/backend` the way `modal_jobs.py` does — the gate + model-fallback
   imports crash every run since the last deploy.
2. The crash produces one `RUN_ERROR`, but the SSE bridge
   (`async_run.py:163/202`) fabricates its own
   "The agent run stopped responding before finishing." — the real error is
   masked and the stream just ends.
3. UI shows duplicated `YOU` rows (optimistic message + reloaded history are
   not keyed by message id).
4. The question is a pure fact/count question — it needs a DB count, not an
   LLM; the design routes everything through the planner, so any model
   outage kills even trivially answerable questions.
5. Underlying: Qubrid 402 (`glm-5.3-flash`, insufficient_quota) is still
   live, and the Gemini fallback key is not provisioned on the research
   secret — so even after 1–2 fix, the model layer stays dark.

## P0 — hotfix (ship first, ~15 min)

- **P0.1 Image parity:** `modal/modal_research_agent.py` image must carry the
  backend code and set `PYTHONPATH` exactly like `modal/modal_jobs.py`
  (mount `backend/` + `eval-agent/`, env `PYTHONPATH=/root/backend`).
  Also import-safe guard: the gate import falls back to the local router if
  `app.*` is unavailable, so a packaging slip degrades instead of killing
  the run.
- **P0.2 Real error surfacing:** the bridge must relay the run's OWN
  terminal event (`RUN_ERROR` with its message/code) and only synthesize
  "stopped responding" when the stream truly ended with NO terminal event —
  and even then with a distinct code (`stream_ended`), not a look-alike.
- **P0.3 Duplicate rows:** key transcript rows by AG-UI message id;
  optimistic rows merge with the replayed history (no double render).

## P1 — remote-agent contract (the "production grade" core)

- **Terminal-event contract.** Every run emits EXACTLY one terminal event:
  `RUN_FINISHED` or `RUN_ERROR{code, message, retryable}` with codes
  (`model_quota_exhausted`, `model_unavailable`, `gate_refused`,
  `tool_failure`, `deadline_exceeded`, `stream_ended`). Nothing may end a
  stream silently. The UI maps codes → actions (retry / recharge / fallback).
- **Run journal (observability).** One row per run id, stages with
  timestamps + error code: gate → dispatch → model-call (provider, model,
  latency, HTTP status) → tool-call (name, latency) → relay → SSE. Stored in
  Postgres (`research_agent_runs`); surfaced in a `GET /runs/{id}/agent-debug`
  for the curator. "Stopped responding" becomes a diagnosis, not a guess.
- **Deadline + keepalives.** Run deadline 180 s (configurable); SSE
  keepalive ping every 15 s while the run is alive so the browser never
  sees a silent stall; on deadline → `RUN_ERROR{code: deadline_exceeded}`.
- **Model layer: circuit breaker + ladder.** `model_fallback` gains a
  breaker: on 402 open the Qubrid circuit for 10 min (all traffic →
  fallback), on 5xx backoff-exponential; config probe in `/health`
  (`models: {qubrid: up|down|no_key, gemini: …}`). Provision
  `GEMINI_API_KEY` on the research secret + Heroku config (or recharge
  Qubrid) — without one of them the assistant cannot answer LLM questions.
- **Idempotent dispatch.** `run_agui_detached.spawn` from async context →
  `.spawn.aio` (the logged `AsyncUsageWarning`); duplicate submits with the
  same `runId` dedupe (the double YOU rows may also be double dispatch).

## P2 — deterministic-first answering (the real fix for this question)

- **Question router before the LLM.** A cheap classifier (the same Jev gate
  output + lexical patterns) recognises fact/count/status questions
  ("how many entities added to wikidata", "how many manuscripts", "what is
  on the canvas", "which QIDs were updated") and answers them from the
  deterministic tools (`wikidata_uploaded_items` DB count, corpus summary,
  canvas digest) — **zero LLM calls**. The LLM handles synthesis only.
- Benefits: exact numbers (the LLM cannot count reliably), answers survive
  every model outage, and the 402 stops being user-visible for the common
  question class.
- Ship as `research_agent/answer_router.py` + a tool-first instruction in
  both system prompts; tests: every catalog count question answers with a
  number while the model layer is stubbed to fail.

## P3 — hardening & acceptance

- E2E: canned runs must terminate with a terminal event ≤ deadline (happy,
  gate-refused, model-402, tool-failure); the debug endpoint returns the
  journal; chaos test (kill model mid-stream) must end in
  `RUN_ERROR{model_unavailable}`, never silence.
- Alerting: 402 counter + breaker-open → admin notice (the recharge link).
- Prompt-copy unification (F4 leftover) + RQ1 free-text MARC search.

## Order & sizing

P0 (15 min, unblocks the surface) → P1 (≈1 day) → P2 (≈1 day) → P3 (≈0.5 day).
P0.3 and the 402 provisioning are independent of the rest.
