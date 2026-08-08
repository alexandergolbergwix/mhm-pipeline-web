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
  Qubrid OpenAI-compat entries today: `moonshotai/Kimi-K2.5`,
  `deepseek-ai/DeepSeek-V4-Flash` (shared `QUBRID_API_KEY`).
  *Why:* Qubrid models have no Gemini tool-loop; cache keys already include
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


- **R25 — Wikidata verdict prompts carry authority and local-target evidence (Rule W-65).** The evaluator payload/cache/export includes compact authority evidence and resolved `__LOCAL:` targets; the rubric accepts coherent year precision 9 and clean Hebrew labels. *Why:* missing non-MARC authority context and unresolved internal references caused systematic partial/fail verdicts.


- **R26 — Wikidata item prompts MUST include the source fields needed to judge semantic claims (Rules W-67/W-68).** The MARC slice includes contents, work mentions, genres, catalog fields, and related works; item payloads preserve authority and `work_candidate_evidence` (source field/text, acceptance reason, folio, sequence). The rubric treats P5008 as administrative and P7535 as archival-only. Cache schema/version changes MUST invalidate verdicts generated from an older evidence contract. *Why:* the judge cannot distinguish an unsupported work, institution-as-person, or archival misuse without the source evidence.
- **R27 — Prompt evidence and verdict fingerprints are one contract (Rule W-71).** Wikidata work evidence, statement labels/qualifiers/references, authority evidence, local targets, and the exact MARC slice MUST reach the prompt and cache summary together; verify and read paths use the same local-target enrichment before stale sanitisation. *Why:* otherwise valid verdicts disappear and stale partials survive evidence fixes.
- **R28 — Wikidata + HMO item judges MUST receive WikiProject Manuscripts skill context (Rule W-104 / W-124 / W-171 / W-172).** Durable pack at `eval-agent/config/skills/wikidata_manuscripts/`; `skill_context_for(channel, entity_type, claim_pids)` injects always-rules, entity slices, claim triggers, and (HMO) the HMO→Wikidata projection checklist via `Evaluator.skill_context` → `render_prompt`. Never dump full wiki HTML. Cache salts `w172_v1` (prior `w171_v1` / `w124_v1`). Mode-β hygiene: absent claims ≠ defects; trust `value_label`; honour quantity **`unit` field** (never invent missing unit qualifier); facsimile semantic_type; do not invent disambiguation without evidence. *Why:* builders already followed WPM; judges did not; export-33 false-partialled P1104 when eval-agent `compact_statements` dropped `unit`.*
- **R29 — Wikidata AI verify MUST receive all evidence channels (Rule W-124).** Before writing the fixture, attach `verify_evidence` (MARC + VIAF + Mazal + existing Wikidata + HMO Wikibase) via `wikidata_verify_evidence.py`; canonicalise control numbers on verify fetch; merge multi-CN MARC in the eval-agent Wikidata path. Rubric forbids overall-fail solely for empty MARC when another pack supports the item; P2888/P973 to browseable project Item:Q URLs are intentional bridges. *Why:* export (12) failed ~73% with “No MARC context” while authority/HMO evidence was present but invisible to the judge.
- **R30 — Incomplete verify sessions MUST report `outcome=partial` and persist TRACE verdicts (Rule W-126).** `resolve_verify_session_outcome` returns `complete` only when cache+fresh cover the scope; missing `results.jsonl`, non-zero exit, or `runner.error` are partial. Streamed `agent.verdict` payloads merge into override/cache write-through when the checkpoint is absent. Judge clients emit `[STEP]` before retry sleeps; `spawn_eval_agent_run` kills the child on GeneratorExit. *Why:* job 341a976e marked 54/313 `complete` with `fresh_verdicts=0` and a false “eval-agent error” UI string.
- **R31 — Large-scope verify MUST stay alive under dyno pressure (Rules W-127 / W-135).** OpenAI-compat spawn defaults to `--parallel 2` (`EVAL_AGENT_OPENAI_COMPAT_PARALLEL`, clamp 1–4); judge HTTP waits emit mid-call `[STEP]` via `StepHeartbeat`; the judge loop appends `results.jsonl` per verdict; missing `runner.exit` synthesizes a `runner_error`; verify progress snapshots are throttled (see job-service R20). *Why:* parallel=1 on Qubrid capped throughput at ~1 verdict/5s; parallel>1 without lean heaps OOM'd the Basic dyno.
- **R32 — Verify job wire payloads MUST stay slim (Rule W-128).** Mid-run progress has no `session_snapshot`; terminal snapshots drop TRACE events and compact verdict evidence; `serialise_job` re-slims. *Why:* job ecfdcf29 R14 + H12 left the modal stuck on RUNNING / VERDICTS (0) after a partial finish.
- **R33 — Interrupted verify MUST leave Continuable cache hits (Rule W-130).** Wikidata/HMO streams persist each fresh `agent.verdict` to overrides + inference cache immediately; `fail_stale_jobs` / verify fail paths stamp `result.resumable`. Continue starts a new job with `override_cache=false` so judged items warm-hit. *Why:* dyno OOM at 61/313 left only “Cancel and start again”.
- **R34 — Verify collected events and fixtures MUST stay compact (Rule W-131).** `verify_job` stores compact `agent.verdict` rows; Wikidata fixtures scope MARC + evaluator fields only; cached verdict events omit full Studio item blobs. *Why:* fat TRACE payloads + full item candidates R14'd mid-verify on Basic.
- **R35 — MARC slices MUST fall back to raw collapsed tags; evidenced claims are supported (Rule W-137).** `marc_extract.project()` fills any missing slice name from `RAW_TAG_FALLBACK` (`008`, `300$a`, `540$a`, `852$j`, …) — a byte-mirror of the backend map — and the `wikidata_item` prompt/rubric receive `verify_evidence.claim_sources` (PID → source-field text) plus `value_labels`. A PID listed with evidence must not be reported as unsourced; a sparse catalog record is not a defect. *Why:* collapsed-key runs showed the judge four MARC keys, so P571/P1104/P217/P6216 read as unsupported on every manuscript.
- **R36 — Claim provenance is channel-aware; absent claims are not defects (Rule W-138).** `verify_evidence.claim_sources[PID].channels` names the channel behind each claim (`marc.<slice>`, `authority.*`, `hmo_wikibase`, `work_candidate_evidence`) or marks it `structural` (P31/P3959). The rubric forbids reporting a channel-backed or structural claim as unsourced, requires identifier-backed authority rows before accepting person dates, and treats `P1574 → Q234460` + `P1932` as correct modelling for an unidentified text. *Why:* 246 items carried a P2888 and 122 a P214 with no provenance row, so nearly every person read as unsupported.
