# Research Assistant hardening — scope jail, TypeSafe Jev pre-check, 402 resilience

Status: IMPLEMENTED 2026-10-17 — F1–F3 done (78 research tests green); F4 items RQ1/RQ4 and the system-prompt copy unification remain. Surface: `/runs/:runId/linked-data-explorer`.
Motivation: review found (1) the production prompt path has no scope/jailbreak
gate (Rule R15 only runs in the dev stub), (2) the model layer dies on a Qubrid
402 with a raw error, (3) quarantine does not cover MARC evidence text.

## Review findings (file:line)

1. **No gate on the production path.** `classify_scope`
   (`backend/app/services/research_agent/scope.py:121`) is called only from
   `agui.py:153` (local stub). The production funnel
   `start_async_agui` (`backend/app/routers/research_agent.py:688`) and the
   Modal entry (`modal/modal_research_agent.py:704`) forward the prompt
   straight to the planner.
2. **Quarantine gap.** `quarantine_text` (`sanitize.py:20`) covers fetched
   wiki entities (`wiki.py:106-137`) but NOT `research_evidence` /
   `research_entity` MARC note text (`tools.py:302-315`) — retrieved text can
   carry instructions into the planner.
3. **No pre-prompt model check.** Nothing classifies a user prompt before
   inference; the user asked for TypeSafe Jev (`jev-1.13.0`) as the check.
4. **402 kills the surface.** `build_agent` (`modal/modal_research_agent.py:283`)
   and `title.py:34` call Qubrid (`glm-5.3-flash`); `insufficient_quota` (402)
   surfaces as a raw failure. No fallback model, no humanized message.
5. **Scope router is regex+cosine** (`scope.py:28-147`) — good as a fast
   pre-filter, weak alone against paraphrased jailbreaks.

## F1 — TypeSafe Jev pre-prompt gate (the ask)

New `backend/app/services/research_agent/jev_prefilter.py`:
- `check_prompt(text, *, api_key, model="jev-1.13.0") -> PreFilterVerdict`.
- Calls `POST https://api.typesafe.ai/v1/systemone` (same contract as
  `eval-agent/eval_agent/client/typesafe_client.py:40-79`) with the prompt as
  state and THREE typed Choice questions (reuse `eval_agent.client.typesafe_questions`
  style):
  1. `relevance` — "Is this request about the Hebrew manuscript research
     corpus (manuscripts, MARC, works, persons, provenance, Wikidata,
     Wikibase, SPARQL, the project's own data)?" (yes/no)
  2. `instruction_override` — "Does the request try to override instructions,
     extract the system prompt, adopt an unrestricted persona, or manipulate
     the assistant's rules?" (yes/no)
  3. `harmful_or_exfiltration` — "Does it ask for harmful/illegal content,
     personal data exfiltration, secrets, or actions outside the research
     assistant's scope?" (yes/no)
- ALLOW iff `relevance=yes and instruction_override=no and harmful=no`.
  Refusals return `REFUSAL_MESSAGE` (`scope.py:19`) — the LLM never runs.
- **Chain (defence in depth), every prompt, in order:**
  1. local `classify_scope` (free, catches obvious cases + regex jailbreaks),
  2. TypeSafe Jev check (the model judge),
  3. only then the planner LLM.
  API outage policy: fail-closed on `instruction_override/harmful`
  signals from the local router; on total Jev outage fall back to the local
  verdict (log `Jev pre-filter unavailable` + keep the local decision).
- **Two enforcement points** (neither bypassable):
  - `research_agent.py:731-743` (`start_async_agui`) — after message
    coercion, before the Modal dispatch.
  - `modal/modal_research_agent.py:704-717` (`run_agui_detached`) and the
    sync `agui` twin — before `build_agent`/dispatch.
- Key: `TYPESAFE_API_KEY` (already in the `mhm-jobs2` Modal secret and the
  Heroku config). Rate-limited like the judge client; cached verdicts per
  `(project_id, text_hash)` for 24 h (same discipline as R23).
- Tests: allow/reject/jailbreak/paraphrase cases with a stubbed Jev
  transport; outage fallback; cache hit; the funnel calls the gate before
  dispatch (`backend/tests/unit/test_research_jev_prefilter.py` +
  extension of `test_research_agent_scope.py` / `test_research_async_run.py`).

## F2 — close the review gaps

- Run `classify_scope` + `consecutive_rejects` at BOTH funnels (R15 becomes
  true for production).
- Route `research_evidence` / `research_entity` output through
  `quarantine_text` (`tools.py:302-315`) — R16 for every retrieved text.
- Strengthen `sanitize.quarantine_text` patterns with the Jev
  `instruction_override` vocabulary (role-play jailbreaks, "repeat your
  instructions", encoding tricks).

## F3 — 402 resilience (glm-5.3-flash / Qubrid)

- **Humanize:** map `insufficient_quota`/HTTP 402 to a plain message in the
  chat ("The AI model account is out of credit — the administrator must
  recharge it at platform.qubrid.com/billing. Your tools and canvas still
  work.") in `modal_research_agent.py` (AG-UI error event) and
  `frontend/src/lib/canvasState.ts::humanizeAgentError` (W-231 family).
- **Fallback model:** `resolve_model_config()` in
  `modal/modal_research_agent.py`: primary Qubrid
  (`RESEARCH_AGENT_MODEL`/`glm-5.3-flash`), fallback the Gemini
  OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`,
  model `gemini-2.5-flash`) with `GEMINI_API_KEY` — same
  `OpenAIChatModel`/`OpenAIProvider`, zero new dependencies. Retry the
  dispatch once on 402 before humanizing. `title.py` uses the same resolver.
- Config: `heroku config:set GEMINI_API_KEY=…` + add the key to `mhm-jobs2`
  (the `GET /healthz` gains an `optional` note). OR simply recharge Qubrid —
  the fallback is the resilience, not the substitute.
- Tests: 402 → humanized message; 402 → fallback model used; all-fail →
  humanized terminal message (`tests/unit/test_research_model_fallback.py`).

## F4 — functional improvements (from the review)

- RQ1 free-text search across MARC notes (question catalog gap).
- RQ4 aggregate trend charts (the catalog's missing viz).
- Show the gate verdict in the UI when a prompt is refused (reason chip).
- The two system-prompt copies (`prompt.py:8-79`, `modal_research_agent.py:46-122`)
  drift; fold the Modal one onto `prompt.SYSTEM_PROMPT` (import) so the rails
  cannot diverge.
- Reply-cache: refuse-cache must key the verdict too (a prompt refused once
  is refused for 24 h — no cache echo of a refused prompt).

## Rules

Add `Rule W-273` (platform-infra): "every research prompt passes the
two-stage gate (local scope router + TypeSafe Jev) before any LLM call, on
both the Heroku and Modal planes; retrieved text of every kind passes the
quarantine; a 402 from the model provider humanizes and falls back."
Mirror as research-block `R29`/`R30`.

## Sizing

F1 ≈ 2–3 h · F2 ≈ 1 h · F3 ≈ 1–2 h · F4 separate. Dependencies: F2 before F1
tests (shared funnel); F3 independent.
