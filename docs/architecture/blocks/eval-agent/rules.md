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
- **R18 — HMO item verify uses `control_numbers` + HMO-specific grounding (Rule W-48).**
  Built items carry `control_numbers: list[str]` from RDF graph BFS at item
  build; ingest `enrich_control_numbers()` propagates across `deferred_links`;
  `session.py` tries every CN when loading MARC. The `hmo_wikibase_item`
  evaluator passes `entity_type`, structural vs manuscript-scoped grounding, and
  the rewritten rubric (`full`/`partial`/`fail`) — HMO `class_qid` is not
  Wikidata. *Why:* export (4) still had 792 items with empty MARC and 1283
  generic descriptions after W-45 URI-regex alone.
- **R19 — Multi-CN MARC merge for shared corpus entities (Rule W-50).**
  When `control_numbers` lists more than one manuscript, `session.py` MUST
  pass `marc_extract.merge_records()` (union of authors/contents/subjects) to
  the HMO item evaluator; `primary_control_number()` picks the URI-matched CN.
  Rubric: CU English labels and short Expression titles are `name_ok=yes`;
  empty `shacl_issues` MUST NOT yield `role_ok=no`. *Why:* export (4) false
  MARC misses for shared authors and ~891 CU label partials from judge
  calibration.
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
- **R19 — HMO item rubric treats event/transmission entities as system-labeled
  (Rule W-52).** `hmo_wikibase_item.md` rules 3d/3e + the evaluator's
  `SYSTEM-LABELED EVENT` grounding state: `E12_Production`, `E52_Time-Span`,
  `F27_Work_Creation`, `TransmissionWitness`, `TextTradition` get `name_ok=yes`
  when the English system label carries the MS/period AND the description is
  substantive; `E74_Group` orgs are judged as organizations, not Hebrew
  persons. *Why:* run-`48ba6c13` fixup loop — the judge downgraded intentional
  `Production of MS …` labels absent a rubric carve-out.
- **R20 — HMO item rubric: honest-negative production, name order, vocabulary
  terms (Rule W-53).** `hmo_wikibase_item.md` rule 3d gains an empty-production
  addendum (a description stating production place/date is *not recorded* is
  substantive → `name_ok=yes`; only the bare label-repeating template is
  partial); rules 3f/3g accept MARC `Surname, Given` order, subject-heading
  persons, generic-MS-titles carrying the shelfmark, and controlled-vocabulary
  terms. The evaluator's `SYSTEM-LABELED EVENT` grounding mirrors the negative
  carve-out and `Paleographical_Unit` is now a structural type. *Why:* export
  (5) on run `48ba6c13` — 155 partial / 4 fail from thin labels + person typing.
- **R21 — Canonical control-number MARC join (Rule W-54).**
  `marc_extract.canonical_control_number()` strips surrounding quotes/whitespace;
  `index_by_id` keys on it and `session.execute` canonicalises every `rid` /
  `cns` / `primary` before `marc_index` lookups (NER, authority, Wikidata, HMO
  item paths). *Why:* Stage-1 persists `_control_number` as `"990…"` (quoted)
  while item control numbers are clean digits, so the join silently missed and
  the judge saw **empty MARC** → conservative `partial` on short-title
  Expressions / given-name Persons. Fixing the join key restores MARC grounding
  run-wide. Mirrored web-side in `marc_verify_context.py` (cache-key parity).

- **R22 — Background verify workers MUST require the user-scoped `_api_key`
  only when the selected tier-1 provider is Gemini.** Non-Gemini tiers, such
  as Qubrid Kimi, use their server-side `api_key_env` and must reach the
  runner after `run_job_params` validates that provider. *Why:* applying the
  Gemini guard to Kimi made an already configured Qubrid job fail before its
  first event.


- **R23 — Wikidata verify grounding is source-record exact (Rule W-63).** The builder persists `records` on every manuscript, person, and work item; shared entities use the sorted union. The worker may recover IDs from legacy P3959 reference snaks, but MUST NEVER default an item to the first run record. *Why:* that fallback paired 294 unrelated candidates with one MARC row and produced systematic false failures.


- **R24 — Verify progress is idempotent (Rule W-64).** Job workers count distinct candidate local IDs from `agent.verdict`; they never add replayed disk/cache verdicts or treat `agent.stats.judged` as an absolute total. *Why:* a 294-item Wikidata job rendered 395/294 during a replayed result stream.
