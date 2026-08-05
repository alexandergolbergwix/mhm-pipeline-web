# AI verification (verify streams, judges, verdict caches)

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

Verify session layout (upstream Rule W-14): sessions live under
`<state_dir>/<run_id>/sessions/<session_id>/` so each session has its own
audit trail. `agent_runner.py::_resolve_session_dir(run_id, session_id)`
accepts BOTH that layout and the legacy direct-child layout so older
sessions still load — never *write* a session into the legacy layout, that
path is read-only.

### Rule W-17 — NER verdicts persist to ExtractionApproval.ai_verdict

Mirror of Rule W-13 (authority verdicts). When the
`NerVerificationModal`'s SSE stream finishes, the backend's
`extraction_verify.py::_persist_ai_verdicts_to_entities` opens a fresh
`session_scope` (the request session is closed by then) and joins
`candidate._entity_id` → `ExtractionApproval.id`, writing the verdict
summary into the JSONB `ai_verdict` column AND setting
`ai_verdict_at = now()`.

Summary shape (parity with `AuthorityMatch.payload.ai_verdict`):
`overall`, `name_ok`, `type_ok`, `role_ok`, `reasoning`, `model`,
`judged_at`, `cache_key`, `session_id`, `evaluator`.

The frontend's `useApprovalStore` polls `/entities` and emits
`mhm.entities.refreshed` whenever a verdict lands, so the inline
pills on the EntityTable refresh without the user reloading the page.

### Rule W-18 — Stage-2 verify state_dir per-RUN (matches Rule W-14)

`extraction-verify-sessions/<run_id>/` is the shared per-run state
dir; per-session artefacts live one level deeper under
`sessions/<session_id>/`. The eval-agent's verdict cache lives at the
state_dir root so opening the modal again warm-hits prior Gemini
judgements. The per-session subdir holds only the filtered fixture
(marc_extracted.json + ner_results.json) and the SSE trace audit log.

Same trust boundary as Rule W-15: the eval-agent is a subprocess
only; no Python imports across the FastAPI ↔ eval-agent boundary.

### Rule W-33 — Verify state on Heroku is writable under `/tmp` (added 2026-06-17)

On Heroku dynos the slug filesystem is read-only. All three verify
channels (`ai-verify-sessions`, `extraction-verify-sessions`,
`wikidata-verify-sessions`) MUST persist session traces + eval-agent
`runs/` artefacts via `resolve_verify_state_dir()` in
`agent_runner.py`, which defaults to `EVAL_AGENT_STATE_DIR` or
`/tmp/mhm-eval-agent-state` when `DYNO` is set.

`scripts/start.sh` exports both `EVAL_AGENT_ROOT` (bundled
`eval-agent/`) and `EVAL_AGENT_STATE_DIR`. `scripts/release.sh`
fails fast if `locate_eval_agent()` cannot find `eval_agent/cli.py`.

Postgres `inference_cache` (kind `ai_verdict`) remains the durable
cross-dyno verdict tier; `/tmp` state is ephemeral per dyno lifetime
but sufficient for in-session replay and eval-agent subprocess I/O.
Completed background verify jobs also embed ``session_snapshot`` in
``run_jobs.result`` so session GET handlers survive multi-dyno routing.
While a verify job is still running, ``verify_job.py`` also writes a
partial ``session_snapshot`` into ``run_jobs.progress`` on every streamed
event so ``useVerifyJob`` can hydrate ``VerdictsTable`` live without
reading the worker dyno's ``/tmp`` trace.

### Rule W-44 — Large-scope AI verify MUST stream verdicts live via job + TRACE (added 2026-07-08)

Incident: run `48ba6c13` — **Autofix with AI (1967 items)** showed an active
`AgentFlowDiagram` (`[STATS]` lines updating judged count) but
`VerdictsTable` stayed at **VERDICTS (0)** / "Waiting for verdicts…" for the
entire multi-hour run.

Root cause, two parts:

1. **Subprocess verdict timing.** `eval-agent` wrote `results.jsonl` only in
   `checkpoint()` at session end. `spawn_eval_agent_run` forwarded
   `[STEP]`/`[STATS]` during the loop but not `agent.verdict`; verify streams
   re-read `results.jsonl` in a `finally` block and only then emitted verdicts.
   For 1967 uncached items at 30 rpm autofix that meant ~65 minutes with a live
   UI count of zero even while Gemini was judging.
2. **HMO item modal used direct SSE.** `HmoItemVerificationModal` called
   `POST …/ai-verify/start-stream` via `useHmoItemVerifySession` instead of the
   job-backed path (`useVerifyJob` + `run_jobs.progress.session_snapshot`) that
   NER verify and `ItemUploadPanel` pre/post-upload already use. Long-lived SSE
   on Heroku is fragile; without incremental `agent.verdict` events the job
   snapshot also had nothing to hydrate mid-run.

Invariants now enforced:

- **`eval-agent` emits `[TRACE] {"type":"agent.verdict",…}` after each judged
  candidate** (`eval_agent/orchestration/session.py::_emit_verdict_trace`).
  `agent_runner._read_subprocess_stream` maps these to `AgentEvent` type
  `agent.verdict` during the subprocess loop.
- **Verify stream `finally` blocks MUST NOT re-yield verdicts already streamed**
  during the subprocess (track streamed `_local_id` / `_match_id` keys; still
  persist from `results.jsonl` for durability).
- **Large-scope curator verify modals MUST use `useVerifyJob`**, not a direct
  `start-stream` hook. `HmoItemVerificationModal` now mirrors
  `NerVerificationModal`: `RunJobs.start(…, kind="hmo_item_verify")`, poll every
  2 s, hydrate from `fetchVerifySessionWithJobFallback` +
  `progress.session_snapshot`. The `start-stream` route remains for replay/tests;
  it is not the primary UI path for 1000+ item scopes.

Tests: `backend/tests/unit/test_agent_runner_subprocess_timeout.py`
(`test_read_subprocess_stream_parses_trace_agent_verdict`),
`backend/tests/test_verify_job_hmo.py`.

---

### Rule W-46 — Tier-1 judge model registry + Qubrid Kimi path (added 2026-07-08)

Curators can pick the tier-1 judge per verify run from a server registry
instead of always defaulting to `gemini-3.5-flash`:

- **Registry:** `eval-agent/config/tier1_models.yaml` — today
  `gemini-3.5-flash` (Gemini, agentic-capable) plus Qubrid OpenAI-compat
  linear judges `moonshotai/Kimi-K2.5` and
  `deepseek-ai/DeepSeek-V4-Flash`
  ([Qubrid model card](https://platform.qubrid.com/model/deepseek-v4-flash)).
  Backend mirrors via `app/pipeline/judge_models.py` (no Python import
  across the subprocess boundary).
- **Eval-agent:** `OpenAICompatJudge` in
  `eval_agent/client/openai_compat_client.py`; `session.py::_build_judge`
  routes by provider. Non-agentic models force `mode=linear` in
  `SessionConfig.from_args`.
- **Credentials:** Gemini — user Settings key or `GEMINI_API_KEY`;
  Qubrid — server `QUBRID_API_KEY` only (injected into subprocess env,
  never argv). `prepare_job_params` fails fast with a clear 400 when the
  chosen model's key is missing.
- **API/UI:** `GET /api/judge-models`; `Tier1ModelSelect` on every verify
  modal + upload pre/post-verify checkboxes. Job params carry `tier_model`.
- **Cache:** unchanged — `judge_model` already part of inference-cache
  query summaries (Rule W-25 / eval-agent R7).

Tests: `eval-agent/tests/test_judge_models.py`,
`test_openai_compat_judge.py`, `backend/tests/test_judge_models_router.py`,
`test_run_job_params_tier_model.py`.

---

### Rule W-47 — HMO schema AI verify must show ontology context to the judge (added 2026-07-08)

Audit of the 2026-07-08 schema verdict export (`387` entries: `232`
partial / `36` fail): **~105 partials** falsely claimed “missing
description” even though every row had a non-empty `description` in the
bootstrap JSON. Root cause: `eval-agent/eval_agent/evaluators/
hmo_wikibase_schema.py` never passed `description` (or OWL metadata)
into `build_prompt()` — the rubric told the judge to score `name_ok`
from label **and** description, but only the label was visible.

Invariants now enforced:

- **Evaluator prompt** includes `description`, `aliases`, and — for
  properties — `OWL kind` + `rdfs:range` + `parent_uri` when present.
- **Fixture enrichment** — `hmo_schema_verify.filter_schema_entries`
  merges `schema_entry_metadata_by_uri()` from
  `ontology_schema_reader.py` so skipped/cached bootstrap rows still carry
  OWL context without re-running bootstrap.
- **Cache key** — `schema_verdict_query_summary` includes `description`,
  `property_kind`, and `range_uri` so pre-fix verdicts do not warm-hit
  after deploy.
- **Rubric** (`hmo_wikibase_schema.md`) documents CIDOC object-property
  `wikibase-item` typing, folio **designation** strings vs folio
  **counts**, `holding_institution` vs `has_holding_institution`, `url`
  for `owl:sameAs`, and `quantity` (not `globe-coordinate`) for
  `geo:lat`/`geo:long` — matching the item exporter.
- **Datatype inference** — `hmo_source_uri` → `url` (`xsd:anyURI` in
  TTL + local-name override); `book_name` → `monolingualtext`; thirty
  CIDOC/LRMoo/HMO classes gained `rdfs:comment` (zero fallback-class
  descriptions remain).

Tests: `eval-agent/tests/test_hmo_wikibase_schema.py`,
`backend/tests/unit/test_ontology_schema_reader.py`,
`backend/tests/unit/test_hmo_schema_verify.py`.

**Curator ops:** re-run HMO schema AI verify with a fresh session (or
`skip_cache`) after deploy — old `ai_verdict` cache rows keyed without
`description` will miss and re-judge correctly.

---

### Rule W-48 — HMO item AI verify needs manuscript scope + substantive descriptions (added 2026-07-09)

Second audit of run `48ba6c13` export (4) after W-45/W-47 fixes — **690
fail / 857 partial** on 1911 items (down from 966 fails, still not
curator-ready):

1. **792 items still had no MARC join** — mostly `QDraft_Person_*` rows
   whose `source_uri` carries no 8+ digit control number. URI-regex alone
   (W-45) cannot reach derived persons/works linked only through the RDF
   graph. **Fix (build):** `hmo_exporter._control_numbers_for_node` BFS-walks
   **incoming** RDF edges to every manuscript URI, stamps `control_numbers`
   on `WikibaseEntityDraft` / `ResolvedWikibaseEntity`, and persists them in
   `HmoStudioItemCache`. **Fix (verify):**
   `hmo_wikibase_items.enrich_control_numbers()` propagates across
   `deferred_links`; `session.py` tries every CN in the list when loading
   MARC.
2. **1283 generic Wikibase descriptions** — `… in the Hebrew Manuscripts
   Ontology (HMO)` fallback when a node lacks `rdfs:comment`. Codicological
   units were fixed in W-45; Work/Expression/Person/Manuscript/Production/
   epistemology nodes were not. **Fix:** `graph_builder._stamp_wikibase_comment`
   (and the existing CU helper) attach English `rdfs:comment` at RDF build;
   `hmo_exporter._descriptions_for_node` prefers those over the fallback.
3. **Wrong judge framing** — the item rubric treated HMO items like raw NER
   spans (`grounded=None`, Wikidata-centric `class_qid` confusion). **Fix:**
   rewritten `hmo_wikibase_item.md` + evaluator passes `entity_type`,
   `control_numbers`, structural vs manuscript-scoped grounding, and
   `full`/`partial`/`fail` overalls.

**Curator ops after deploy:** RDF rebuild → HMO **Rebuild (skip cache)** →
re-run item AI verify with **override cache** (rubric + build fields changed).
Reupload only when live wiki labels/descriptions should change.

Tests: `backend/tests/unit/test_hmo_exporter_control_numbers.py`,
`test_graph_builder_codicological_labels.py`,
`eval-agent/tests/test_hmo_wikibase_items.py`.

---

### Rule W-50 — HMO verify label hygiene + multi-CN MARC merge (added 2026-07-09)

Export (4) on run `48ba6c13` showed ~1111 `name_ok=partial` rows driven by
label design and judge calibration, not bad RDF:

1. **505 / Expression labels** — `parse_contents_entry()` in
   `converter/rdf/rdf_helpers.py` splits `N) folio : title` 505 rows at
   ingest; `graph_builder._add_content_work` / `_add_expression` emit short
   Hebrew titles only (never `(in MS {cn})` in labels; scope stays in
   `rdfs:comment`). `clean_marc_label` strips `(in MS …)` suffixes and
   optional enumeration prefixes; `hmo_exporter._truncate` clips at word
   boundaries.
2. **Multi-CN MARC for verify** — shared persons across manuscripts MUST
   merge MARC from every `control_numbers` entry:
   `marc_extract.merge_records()` / `project_many()`; `session.py` passes
   the union to `hmo_wikibase_item`; `primary_control_number()` picks the
   CN matching `source_uri` / `local_id`.
3. **Vocabulary-node descriptions** — genre/subject/material/script nodes get
   `rdfs:comment` at RDF build (not generic exporter fallback).
4. **SHACL in verify** — fixtures from `fetch_merged_hmo_items` carry real
   `shacl_issues`; evaluator `blocking_shacl` short-circuits; rubric forbids
   `role_ok=no` when `shacl_issues` is empty.

Upload SHACL gate (`hmo_item_shacl_gate.py`) unchanged — fail-closed.

Tests: `test_rdf_helpers.py`, `test_graph_builder_codicological_labels.py`,
`eval-agent/tests/test_marc_extract_merge.py`, `test_hmo_wikibase_items.py`.

---

### Rule W-51 — AI verdict caches are content-addressed everywhere (added 2026-07-09)

Every ``kind=ai_verdict`` surface (NER extraction, authority matches,
Wikidata Studio items, HMO Wikibase items, HMO schema bootstrap) MUST
use the same two-tier contract:

1. **Inference cache lookup** — ``canonical_hash(query_summary)`` where
   ``query_summary`` includes every curator-visible input (entity text,
   match payload, item labels/claims/SHACL, merged MARC slice, judge
   model, evaluator id, and a schema salt like ``w50_v1``).
2. **Stored row ``cache_key``** — the same content fingerprint, never
   the eval-agent's prompt-hash. ``sanitise_stale_*`` on read paths
   drops verdicts whose ``cache_key`` no longer matches the current
   input so a rebuild or MARC edit invalidates pills without
   ``override_cache``.

``override_cache`` / ``--no-cache`` is only for forcing a re-judge when
the input is unchanged (rubric tweak, model swap). Changing the data is
always sufficient invalidation — mirrors Rule W-26 fingerprint-keyed
build caches (Rule R6: never delete manually).

Modules: ``ner_verdict_cache``, ``authority_verdict_cache``,
``wikidata_verdict_cache``, ``hmo_item_verdict_cache``,
``hmo_schema_verdict_cache``, shared ``ai_verdict_cache_common``.

Tests: ``test_*_verdict_cache.py`` per channel.

---

### Rule W-54 — Canonical control-number join for AI verify (added 2026-07-10)

Deep investigation of the residual ``partial`` HMO item verdicts that survived
Rule W-53 (measured locally via
[`.codex/skills/local-measure-verify`](.codex/skills/local-measure-verify/SKILL.md))
found the dominant cause was **not** thin data — it was a silent MARC-join
failure. Stage-1 persists ``run_records.marc["_control_number"]`` **with
literal surrounding quote characters** (``"990…"``), but Studio item /
candidate control numbers are the clean digit string. Every verify indexer
keyed on the raw quoted value, so the join missed and the judge received an
**empty MARC context** — then conservatively returned ``name_ok=partial``
("description merely repeats the label") for exactly the derived entities
(short-title ``F2_Expression``, given-name-only ``E21_Person``) that most need
manuscript corroboration. This is the same "no MARC context" class Rules W-45 /
W-48 chased via URI-regex + BFS control-number propagation; the join **key**
was quoted the whole time.

Invariant: **canonicalise the control number at every join boundary** — strip
surrounding quotes + whitespace on both the index key and every lookup.

- ``eval_agent/ingest/marc_extract.py`` — new ``canonical_control_number()``;
  ``index_by_id`` keys on it. ``orchestration/session.py`` canonicalises every
  ``rid`` / ``cns`` / ``primary`` before ``marc_index`` lookups (NER, authority,
  Wikidata item, HMO item paths) and the ``_ner_index`` keys.
- ``app/pipeline/marc_verify_context.py`` — mirror ``canonical_control_number()``
  in ``index_marc_records`` + ``marc_context_for_item`` (cache-key + fixup
  parity).

Also generalised the Rule W-53 production-description grounding: the
manuscript's title + shelfmark are woven into **every** ``E12_Production``
description (not only the fully-empty case), so a date-only description reads
``Production of manuscript {cn} ('{title}', shelfmark {sh}): {date}.`` instead
of a bare date that repeats the label.

This restores real title/authors/subjects grounding to the judge **run-wide**,
not just for the measured scope — any verify channel keyed by control number
benefits.

Tests: ``eval-agent/tests/test_marc_extract_merge.py``
(``index_by_id_canonicalises_quoted_control_number``,
``canonical_control_number``), ``backend/tests/unit/test_marc_verify_context.py``
(``marc_context_joins_quoted_control_number``),
``test_graph_builder_codicological_labels.py``.

Mirror caveat (Rule W-43 residual): ``graph_builder.py`` /
``marc_verify_context.py`` edits are web-local; the vendored ``eval-agent/``
copy is web-owned (see [[vendored-eval-agent-copy]]). Do **not** run
``sync_converter_to_web.sh``.

---

### Rule W-58 — Wikidata AI verdict cache keys canonicalize build and verify record identities (added 2026-07-11)

Wikidata Studio verification persisted valid `WikidataItemOverride.ai_verdict`
rows, but the review table still showed `--`. The worker fingerprints its
fixture with `record_ids`; cached build rows expose the equivalent association
as `records`. The old read model did not recognize `records` when recomputing
the fingerprint. It also used a different generic MARC attachment path, so
its MARC slice could differ from the worker’s. Stale-verdict sanitisation then
correctly rejected the mismatched key — but incorrectly hid every just-finished
verdict from the curator.

The canonical `wikidata_verdict_cache` path now resolves both fields and
attaches the same MARC slice in every path: direct verify, `wikidata_verify`
run jobs, inference-cache lookup, durable override persistence, and the merged
Studio read model. New summaries carry a `records_marc_v2` marker; unmarked
pre-fix summaries are accepted only when their legacy worker fingerprint still
matches the unedited item, then surfaced with the corrected key. A real-run
regression test covers both persistence and the transition path. Any new
Wikidata verdict reader or writer MUST use this canonical record-ID/MARC-context
helper, rather than the generic attachment helper or a local reconstruction.

---

### Rule W-60 — Verify workers use provider-aware tier-1 credentials (added 2026-07-11)

`run_job_params` correctly validates Qubrid Kimi with the server-side
`QUBRID_API_KEY`, but the shared `run_verify_job` then rejected every job whose
stored `_api_key` was empty as “missing Gemini API key”. That user-scoped field
is intentionally empty for non-Gemini providers. Selecting Kimi K2.5 therefore
failed before the worker could emit `session.start`, even when Qubrid was
configured.

The worker now resolves the selected tier model and requires `_api_key` only
when its provider is Gemini. Non-Gemini providers retain their server-side
environment credentials and continue into the eval-agent runner. New verify
providers MUST follow this split: request-time credential validation uses the
model registry; worker-time guards must not impose Gemini-specific secrets on
other providers. Test: `test_run_job_params_wikidata_verify.py`.

---

### Rule W-62 — Wikidata diagnostic exports MUST preserve verdict and prompt evidence (added 2026-07-11)

The first 294-item Wikidata Studio JSON export had `ai_verdict: null` for every
row, and the old CSV reduced any populated verdict to only `overall`, one
reasoning string, and model. That export could not tell us which generated
labels, descriptions, statements, validation issues, or MARC evidence caused a
partial/fail judgment, so it could not drive a safe builder fix.

The Wikidata Studio CSV now includes the evaluator-relevant item fields,
record IDs, linked MARC context, validation issues, upload state, flattened
rubric fields, and a complete `ai_verdict_json` column. The export MUST retain
that complete verdict JSON and prompt context; adding a new rubric field requires
adding it to the JSON source (and any useful flattened column) so the `analyze_wikidata_verdicts.py` Codex analysis workflow can group recurring builder defects. Test:
`test_wikidata_items_export_import.py`.

---

### Rule W-63 — Wikidata verification MUST use the item source records, never an arbitrary run record (added 2026-07-11)

A 294-item Wikidata Studio verification completed without a worker crash, yet its judge repeatedly compared people and works to the first MARC record in the run. The generated item dataclass did not persist source control numbers for person or work rows. The verify loader then treated that missing metadata as a reason to use `records[:1]`, silently attaching unrelated title, author, and contributor evidence. The evaluator correctly rejected those false pairings, while the UI presented them as generic evaluation errors.

`WikidataItem` now carries review-only `records` metadata. Manuscripts retain their own MARC 001; deduplicated person and work rows retain the sorted union of every source record. The build fingerprint includes a schema marker so old cache rows become stale. Verification uses that metadata, with a P3959-reference recovery only for legacy rows, and MUST leave an item ungrounded rather than borrow the first run record. Every future builder item type or verifier adapter MUST preserve and consume this exact source-record association. Tests: `test_wikidata_studio_works.py`, `test_wikidata_verdict_cache.py`.

---

### Rule W-71 — Verdict evidence, cache keys, and static QIDs MUST share one verified contract (added 2026-07-14)

The third 180-item verification export showed 80 partials and one failure in
the modal, while the exported current-item rows showed only 47 partials and 34
missing verdicts. Thirty-three rows contained `__LOCAL:` statements: verification
attached `local_reference_targets`, but the merged read model did not, so it
recomputed a different fingerprint and hid valid persisted verdicts. Work-source
evidence was present in build artifacts but absent from the evaluator prompt,
statement labels/qualifiers/references were absent from cache keys, and a live
audit found static genre/subject mappings whose QIDs named unrelated entities.
The canonical helper now enriches both verify and read paths, preserving durable
local-target evidence while merging statement-derived targets; prompt-relevant
evidence participates in `w71_v1`/`records_marc_v5` fingerprints; work evidence
reaches the rubric; and every emitted static QID has a verified label. Ambiguous
crosswalk entries fail closed and broad `Jews` headings do not become P921.


### Rule W-79 — Verification context MUST distinguish semantic subtypes (added 2026-07-16)

The seventh Wikidata Studio export showed that a printed facsimile was correctly
projected with P31=Q571 but still judged as a manuscript because the stable
entity type lacked a semantic refinement. It also exposed an unverified Israel
Museum holder and under-specified `__LOCAL` author targets. Studio items now
carry a `printed_facsimile` semantic subtype, the verified Israel Museum QID
(Q46815), and richer local-target context; incomplete one-token English
author labels are omitted rather than presented as misleading names. Tests:
`test_wikidata_phase1_projection.py`, `test_wikidata_verdict_cache.py`, and
`eval-agent/tests/test_wikidata_item.py`.


### Rule W-80 — Diagnostic display labels MUST be complete (added 2026-07-16)

The post-deploy run still judged semantically correct items partial because
P195/P3959 display labels were null, `__LOCAL` claims lacked `value_label`, and
a catalog provenance phrase was promoted into a public description. The export
now supplies static labels for emitted properties and verified institutions,
backfills local statement value labels from target items, gates personal
correspondence genres on explicit evidence, and suppresses catalog-only holder
phrases. Tests: `test_wikidata_phase1_projection.py` and
`test_wikidata_verdict_cache.py`.

### Rule W-104 — Studio AI verify MUST inject WikiProject Manuscripts skill context (added 2026-07-25)

Wikidata Studio and HMO Wikibase item/autofix judges previously saw only a
condensed rubric + candidate payload. They did **not** carry the community
[WikiProject Manuscripts](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts)
/[Data Model](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model)
contract that builders already follow (`docs/wikidata-manuscripts-data-model.md`),
so verdicts could miss P50-on-manuscript, folio/P7416 misuse, or
project-QID-as-Wikidata mistakes that block a clean **HMO → public Wikidata**
projection.

Invariant:

1. Durable skill pack at
   `eval-agent/config/skills/wikidata_manuscripts/skill.json` (sources in
   `SOURCES.md`) — curated slices, never full wiki HTML in the prompt.
2. `eval_agent.skills.wikidata_manuscripts.skill_context_for` selects
   always-rules + entity slice + claim-triggered checks + (for HMO) the
   HMO→Wikidata projection checklist.
3. `Evaluator.render_prompt` injects `skill_context()`; Wikidata and HMO
   item evaluators (autofix inherits) override it.
4. Verdict cache salts `WIKIDATA_VERDICT_SCHEMA` / `HMO_ITEM_VERDICT_SCHEMA`
   bump to `w104_v1` so old pills miss after deploy (Rule W-51).

Tests: `eval-agent/tests/test_wikidata_manuscripts_skill.py`.

### Rule W-115 — Wikidata AI verify MUST use the same Studio source as the review table (added 2026-07-26)

After W-114, Verify with AI still failed with **no Wikidata Studio items in
scope** while the modal header showed ~1608 items. The modern Studio UI
defaults to **canonical** HMO projection and passes those `local_id`s as
`item_ids`, but `_fetch_wikidata_verify_items` hard-coded `source="legacy"`.
Legacy and canonical builds use different local IDs, so the intersection was
empty and the worker aborted before any judge call.

Invariant:

1. Wikidata verify jobs / start-stream accept `source` (`legacy`|`canonical`,
   default `canonical`) and `approved_only`, matching the Studio toggles.
2. The worker loads the Studio cache via `execute_studio_build(..., source=…)`.
3. Frontend verify modals / upload pre-verify pass the active projection
   `source` and `approvedOnly` with `item_ids`.

Tests: `backend/tests/test_run_job_params_wikidata_verify.py`.

### Rule W-116 — Wikidata AI verify MUST NOT SPARQL-reconcile the Studio corpus (added 2026-07-26)

A 1608-item Verify job stayed on **QUEUED/running** with no verdicts while
`_fetch_wikidata_verify_items` → `execute_studio_build` rebuilt the canonical
Studio cache with ``reconcile=True``. Each item hit WDQS (P3959/P214); the
endpoint returned **429 / read timeouts**, so the job never reached the judge
and the tray looked stuck.

Invariant:

1. Verify scope loads the existing Studio cache for the active
   ``source`` / ``approved_only`` (fallback to the sibling approved_only row
   when the exact mode is empty).
2. Cache miss rebuilds with ``reconcile=False`` — never live WDQS on the
   verify path.
3. The worker writes ``phase=preparing`` / “Loading Studio scope…” before
   materialising scope so the tray is not frozen on bare QUEUED.

Tests: `backend/tests/unit/test_wikidata_verify_scope_cache.py`.

### Rule W-124 — Wikidata AI verify MUST receive all evidence channels + WPM Data Model (added 2026-07-27)

Export `(12)` on run `48ba6c13` scored ~182/251 `fail` under DeepSeek largely
because the judge reported **“No MARC context”** while Studio items still
carried authority / HMO Wikibase evidence — and because quoted DB control
numbers wiped `record_ids` before the fixture was written. The WPM Data Model
skill pack (Rule W-104) was present but thin relative to the live
[Data Model](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model)
property tables, and P2888/P973 to the project Wikibase were treated as
suspicious rather than intentional bridges.

Invariant:

1. **Canonical MARC join on verify fetch.** `_fetch_wikidata_verify_items`
   canonicalises run + item control numbers before membership filtering;
   fixture MARC rows store a clean `_control_number`. Eval-agent Wikidata
   paths **merge all** `record_ids` (parity with HMO item verify), not only
   the first CN.
2. **`verify_evidence` pack on every item** (`wikidata_verify_evidence.py`):
   MARC slice, VIAF, Mazal/NLI, existing Wikidata, HMO Wikibase
   (`hmo_wikibase_id` + browseable Item:Q + P2888/P973 bridges), plus work /
   local-target evidence. Attached before the eval-agent fixture write;
   `attach_wikidata_marc_context` also ensures the pack exists.
3. **Evaluator prompt** surfaces each channel as first-class; rubric forbids
   overall-fail solely for empty MARC when another pack supports the item.
4. **WPM skill** (`wikidata_manuscripts` `w124_v1`) expands material /
   creation / content / housing rules from the Data Model page and states
   that browseable `mhm-hmo.wikibase.cloud/wiki/Item:Q…` on P2888/P973 is an
   intentional bridge.
5. **Cache salts** `WIKIDATA_VERDICT_SCHEMA` / `HMO_ITEM_VERDICT_SCHEMA` →
   `w124_v1` (`records_marc_v6`) so old pills miss after deploy (Rule W-51).

**Curator ops after deploy:** re-run Wikidata Studio **Verify with AI**
(override cache optional — salt bump already invalidates). Rebuild Studio
only if item fields themselves changed.

Tests: `test_wikidata_verify_evidence.py`, `test_wikidata_verify_scope_cache.py`
(quoted-CN join), `eval-agent/tests/test_wikidata_manuscripts_skill.py`.

### Rule W-126 — Incomplete AI verify MUST report partial and keep TRACE verdicts (added 2026-07-27)

Incident: Wikidata verify job `341a976e` (DeepSeek V4 Flash, scope 313)
streamed **54** TRACE verdicts then stopped without writing
`results.jsonl`. The UI showed
`Verified 54 of 313 — some candidates could not be judged (eval-agent error)`
while the job row was `succeeded` with `outcome=complete` and
`fresh_verdicts=0` — so overrides/cache never received the 54 judgements,
and the message blamed a typed eval-agent error that never occurred.

Invariant:

1. **`session.end.outcome=partial`** when judged (cache + fresh) `< scope`,
   subprocess exit ≠ 0, or `runner.error` — never `complete` solely because
   `locate_eval_agent()` succeeded (`verify_outcome.resolve_verify_session_outcome`).
2. **Persist TRACE verdicts** when the checkpoint is missing: merge streamed
   `agent.verdict` payloads with `results.jsonl` (disk wins) before override +
   inference-cache write-through (Wikidata + HMO item streams).
3. **Verify workers drain to `session.end`** after `runner.error` (do not
   fail-closed mid-stream and drop the finally block's partial framing).
4. **Judge retry sleeps emit `[STEP]` keepalives** (capped ≤90 s) so the
   180 s idle-kill does not fire during silent 429 backoff.
5. **`spawn_eval_agent_run` kills the child on GeneratorExit/aclose**, not
   only on `CancelledError` — otherwise a closed parent leaves a judging
   orphan while the job already looks finished.
6. Curator UI copy must describe an early stop / incomplete scope, not a
   generic “eval-agent error”, and may surface `runner_error` when present.

Tests: `test_verify_outcome.py`, `frontend/tests/unit/useVerifyJob.spec.ts`.

### Rule W-127 — Large-scope verify MUST stay alive under dyno pressure (added 2026-07-27)

Incident: Wikidata verify job `06b44db0` (DeepSeek V4 Flash, scope 313)
streamed **52** TRACE verdicts in ~7 min then stopped with
`outcome=partial`, no `runner.exit` / `runner.error`, and no
`results.jsonl`. W-126 persistence kept the 52 pills, but the remaining
~261 were never judged. Same shape as `341a976e` (54/313). Root causes
on a 512 MB web dyno: OpenAI-compat `parallel>1` stampeding hung HTTP,
megabyte `session_snapshot` written on every progress tick starving the
event loop / filling the stdout pipe, and silent mid-call HTTP waits
with no `[STEP]` keepalive.

Invariant:

1. **Throttle live verify progress snapshots** — attach
   `session_snapshot` about every 5 s / every 10 verdicts / on terminal
   (`verify_job._progress_with_snapshot`). Mid-run snapshots keep
   verdicts only; drop bulky TRACE noise. Terminal `result` still
   carries the full snapshot.
2. **OpenAI-compat spawn forces `--parallel 1`** (and SessionConfig
   defaults parallel=1 when unset for that provider).
3. **Mid-HTTP `[STEP]` heartbeats** via `StepHeartbeat` around judge
   `urlopen` (Gemini + OpenAI-compat) so the 180 s idle-kill does not
   fire during a single long call.
4. **Incremental `results.jsonl`** — append each verdict during the
   judge loop, not only at `checkpoint()`, so a kill leaves a
   recoverable disk trail.
5. **Synthesize `runner_error`** when spawn ends without `runner.exit`
   (`synthesize_missing_runner_error`) so the curator modal explains a
   silent early stop instead of a blank failure.
6. **Abandoned spawn kills the child** without requiring a clean exit
   event (GeneratorExit / aclose) — continues W-126 kill hygiene.

Tests: `test_verify_job_progress_throttle.py`, `test_verify_outcome.py`
(synthesize / missing exit), `eval-agent/tests/test_step_heartbeat.py`.

### Rule W-128 — Verify job polls MUST stay light on the web dyno (added 2026-07-27)

Incident: after W-127, Wikidata verify job `ecfdcf29` (DeepSeek, scope 313)
climbed to **53/313** (52 cache hits + 1 fresh) then the **512 MB** web dyno
hit **R14 (Memory quota exceeded)** and job polls **H12**'d. The modal
stayed on RUNNING with **VERDICTS (0)** while the tray still showed 53/313.
The job had already finished `outcome=partial` with a clear
`runner_error`; the UI never hydrated because mid-run/terminal
`session_snapshot` payloads (~0.5–1.8 MB with full TRACE + evidence)
starved the event loop and the browser poll.

Invariant:

1. **Mid-run progress is counters only** — no `session_snapshot` in
   `run_jobs.progress` while judging (Rule W-127 throttle was not enough).
2. **Throttle progress DB writes** (~2 s) except framing events
   (`session.start` / `session.end` / `runner.*`).
3. **Collect only framing + verdict events** into the worker's in-memory
   list (drop STEP/STATS TRACE noise).
4. **Terminal `result.session_snapshot` is slim** — compact verdicts
   (label / overall / truncated reasoning), **empty `events`**. Full
   evidence remains on disk TRACE, overrides, and inference cache.
5. **`serialise_job` re-slims** any legacy fat snapshot before the wire.
6. Curator UI may show an empty VerdictsTable mid-run while the counter
   advances; on terminal success it MUST hydrate from the slim job
   snapshot / session GET.

**Curator ops:** Close and reopen the verify modal (or refresh) if the
UI looks stuck after a partial finish. Prefer Gemini/Kimi for large
override-cache scopes on the current dyno size — DeepSeek + full
evidence still OOMs around the first uncached batch after warm hits.

Tests: `test_verify_job_progress.py`, `test_verify_job_progress_throttle.py`.

### Rule W-130 — Interrupted AI verify MUST be Continuable from cached verdicts (added 2026-07-27)

Incident: Wikidata verify job `5bd42818` reached **61/313** then the Basic
dyno OOM'd (R14→R15→H10). After restart, `fail_stale_jobs` marked the row
`failed` with *"Cancel and start again"* — no Continue path, and any
verdicts not yet written in the stream `finally` were lost.

Invariant:

1. **Incremental persist** — Wikidata / HMO item verify streams write each
   fresh `agent.verdict` to overrides + inference cache immediately (not
   only in `finally`), so a kill mid-run still leaves durable cache hits.
2. **Resumable terminal result** — `fail_stale_jobs`, verify-job exception /
   cancel paths, and partial `session.end` stamp
   `result.resumable` / `judged` / `total` / `remaining` (via
   `verify_resume.py`). Stale verify errors tell the curator to Continue.
3. **Continue UI** — `useVerifyJob` exposes `resumeOffer` +
   `continueFromPause()` (same scope params, `override_cache=false`, new
   `session_id`). Wikidata / HMO / NER verify modals show
   **Continue verification (N/M done)**; cache hits skip already-judged
   items. Session GET may hydrate from a failed job's slim snapshot.
4. Dyno restart still re-spawns active rows (`recover_interrupted_jobs`);
   with incremental cache the respawn is effectively a warm continue.

Pre-deploy docs gate note (W-130 follow-up): list endpoints omit
``session_snapshot``; ``GET …/jobs/{id}`` may include the slim snapshot.
``useVerifyJob`` probes resume via ``?kind=&limit=5``, never the full
run job history — listing every verify row with embedded verdicts R14'd
the Basic dyno when the curator opened the modal after Studio's
``page_size=500`` payload.

### Rule W-131 — Studio list payloads and verify heaps MUST stay Basic-dyno-safe (added 2026-07-27)

Incident: opening Wikidata Studio with ~300 items while a verify job ran
collided three heaps on one 512 MB web dyno — full ``page_size=500`` list
responses (statements + QS + evidence), fat TRACE/cached-verdict candidates,
and mid-verify ``fetchAllStudioItems`` reloads — producing R14/R15 crashes
before the first uncached DeepSeek batch finished.

Invariant:

1. **``list_view=true`` on ``GET /wikidata-studio``** — drops ``statements``
   (adds ``statement_count``), evidence blobs, and corpus ``quickstatements``;
   slims ``ai_verdict`` to overall/reasoning/model/judged_at +
   ``has_suggested_fixes``. Full item via
   ``GET …/wikidata-studio/items/{local_id}`` (single-row merge).
2. **No mid-verify corpus reload** — verify modals refresh the review table
   only on terminal ``onComplete`` / Apply-fix, not on throttled progress
   ticks (Wikidata + HMO item verify).
3. **Lean verify heap** — cached verdict events carry compact candidates only;
   ``verify_job`` stores compact ``agent.verdict`` rows in
   ``collected_events``; fixtures use compact JSON, scoped MARC CNs, and
   evaluator-needed item fields (``verify_evidence`` without duplicate
   ``marc`` blob); incremental override/cache writes batch every ~10 verdicts
   or ≤2 s (always flush on cancel/error/finally) — Continue still warm-hits
   (W-130).

Tests: ``test_wikidata_studio_list_view.py``, ``test_wikidata_item_views.py``.


### Rule W-132 — Wikidata verify MUST scope MARC and release in-memory Studio payloads (added 2026-07-28)

W-131 improved list/verify payloads but production still interrupted at **131/313**
(R14 → H12 job polls → H10). The worker still held the **entire run MARC corpus**
(~2k JSONB rows), full Studio items for all 313 scopes, duplicate verdict dicts
in ``streamed_fresh_verdicts`` / ``collected_events``, and fat
``GET …/jobs/{id}`` snapshots on every 2 s poll.

Invariant:

1. **Scoped MARC** — ``_fetch_wikidata_verify_items`` loads run control numbers
   lightly, then ``load_run_marc_records_scoped`` keeps only CNs referenced by
   the verify scope (quoted DB keys canonicalised).
2. **Release heap after fixture write** — ``release_wikidata_verify_heap`` slims
   ``items_by_id`` to persist-only fields (≤40 compact statements,
   ``_marc_context`` retained) and clears scoped ``marc_records`` once the
   eval-agent fixture is on disk.
3. **No duplicate verdict lists** — the Wikidata stream tracks
   ``streamed_fresh_verdict_keys`` only; ``finally`` merges from
   ``results.jsonl`` (incremental persist already wrote overrides/cache).
4. **Job worker framing-only** — ``verify_job`` no longer appends every
   ``agent.verdict`` to ``collected_events``; terminal snapshots hydrate from
   session trace / ``results.jsonl`` via ``read_verify_session``.
5. **Light job GET polls** — ``GET …/jobs/{id}`` defaults
   ``include_session_snapshot=false``; the verify modal fetches the slim
   snapshot once on terminal; poll interval 5 s.

Tests: ``test_wikidata_verify_heap.py``, ``test_wikidata_verify_scope_cache.py``,
``test_verify_job_progress.py``.


### Rule W-133 — Wikidata verify persist MUST NOT block the eval-agent stdout reader (added 2026-07-28)

Post-W-132 deploy, production verify dropped to **~1 entity/minute** (vs
1–5 per 5–10 s before). W-130 incremental persist ``await``'d Postgres on
every ``agent.verdict`` in the stream hot path; the worker stopped draining
subprocess stdout → pipe backpressure stalled the judge. Concurrently, the UI
session GET on every progress tick re-read the full ``trace.jsonl`` and
scanned job history on the same 512 MB dyno.

Invariant:

1. **`WikidataVerdictPersistBatch.enqueue()`** schedules flushes in a
   background task — never ``await`` persist in the eval-agent event loop.
2. **Trace append** in the Wikidata stream uses ``asyncio.to_thread`` for
   ``persist_session_event``.
3. **Session GET** reads disk in a thread pool; skips the job-table scan when
   disk already has verdicts; returns compact verdicts only (no TRACE events).
4. **UI** throttles mid-run session reload to ≤1 per 8 s; job counter poll
   stays at 2 s.

**Curator ops:** cancel a crawl-speed run and restart after deploy — a stalled
pipe does not self-heal mid-job.

Tests: ``test_wikidata_persist_batch.py``.


### Rule W-134 — Interrupted verify jobs MUST auto-resume on the backend (added 2026-07-28)

Verify already runs as ``run_jobs`` workers (not in the browser). After W-130
the curator still had to click **Continue** when a dyno restart, OOM, or stale
heartbeat left a partial run — even though verdicts were already in Postgres/
Redis cache.

Invariant:

1. **Stale verify rows re-queue** — ``fail_stale_jobs`` calls
   ``apply_verify_job_auto_resume``: ``status=queued``, new ``session_id``,
   ``override_cache=false``, progress keeps ``processed``/``total``, then
   ``spawn_job``. User-cancelled rows become ``cancelled``; zero-judged rows
   still ``failed``.
2. **Startup** — ``recover_interrupted_jobs`` (running/queued rows) then
   ``recover_resumable_verify_jobs`` (failed + ``resumable`` in the last 24 h).
3. **Worker behaviour unchanged** — a resumed job warm-hits inference cache for
   already-judged items; only the remainder is sent to the judge.
4. Manual **Continue** in the UI remains as a fallback when auto-resume did not
   run (e.g. nothing judged yet).

Tests: ``test_run_job_recovery.py``, ``test_verify_resume.py``.

### Rule W-135 — Verify judge throughput MUST use safe parallelism (added 2026-07-28)

After W-133 restored the stdout reader, Qubrid/OpenAI-compat judges were still
capped at ``parallel=1`` (~1 verdict / 5 s — API latency bound). Lean verify
heaps (W-131/W-133) allow **2** concurrent judge workers by default.

Invariant:

1. ``spawn_eval_agent_run`` passes ``--parallel`` from
   ``EVAL_AGENT_OPENAI_COMPAT_PARALLEL`` (default ``2``, clamp 1–4; set ``1`` to
   revert W-127 behaviour). Optional ``EVAL_AGENT_GEMINI_PARALLEL`` (1–6)
   overrides Gemini when set.
2. **Non-blocking trace** — ``emit_session_event`` fire-and-forgets
   ``runner.step`` / ``agent.stats`` so disk I/O does not stall subprocess
   stdout between verdicts.
3. HMO item verify schedules incremental Postgres persist as background tasks
   (Wikidata already uses ``WikidataVerdictPersistBatch``).

Tests: ``test_spawn_parallel.py``.


### Rule W-136 — Verdict fingerprints MUST be invariant under verify-heap slimming (added 2026-07-29)

Incident: a 313-item Wikidata Studio verify finished with 194 pass / 99 partial
/ 20 fail visible in the modal, while every **AI verdict** cell in the review
table showed ``—``. The verdicts were persisted correctly; the read path could
not reproduce their ``cache_key``, so stale-verdict sanitisation (Rule W-51)
dropped all 313.

Two independent key mismatches:

1. **Slim vs full item.** Rule W-132's ``release_wikidata_verify_heap`` replaces
   scope items with ``slim_item_for_verdict_persist`` output *before* the verdict
   is persisted — compacted statements (no qualifiers/references, capped at 40)
   and ``verify_evidence`` minus ``marc``. The fingerprint hashed the full shape,
   so a freshly written ``cache_key`` was unreproducible by *any* reader.
2. **Read path had no evidence packs.** ``fetch_merged_wikidata_items`` never
   attached ``verify_evidence`` (Rule W-124) and attached
   ``local_reference_targets`` only *after* sanitisation.

Invariant:

1. **One projection *per question*.** ``fingerprint_statements`` /
   ``fingerprint_verify_evidence`` in ``wikidata_verdict_cache.py`` are the only
   statement/evidence projections used for **keys and persist slims**. Any field a
   fingerprint reads MUST survive slimming.

   **Amended 2026-08-05 (Rule W-156):** the FIXTURE is a different projection.
   Building it with ``fingerprint_verify_evidence`` stripped ``duplicate_check`` and
   ``llm_proposals`` from every prompt while the rubric asked about both, so they
   rendered as ``{}`` on all 343 items of run ``48ba6c13`` and 28 of 29 partial
   verdicts hedged about a probe that had answered. Use
   ``judge_evidence_projection`` for the fixture.
2. **Read paths enrich first.** ``attach_local_reference_targets`` +
   ``enrich_items_with_verify_evidence`` run over the whole merged corpus before
   any verdict is compared; ``fetch_merged_wikidata_item`` delegates to the
   corpus merge so drawer and table agree.
3. **Derived evidence never invalidates alone.** ``sanitise_stale_wikidata_verdict``
   also accepts the evidence-free fingerprint (subset scopes resolve fewer
   ``__LOCAL`` targets) and rewrites ``cache_key`` to the current value.

Tests: ``tests/unit/test_wikidata_verdict_cache.py``
(``test_slimmed_persist_item_reproduces_the_full_item_fingerprint``,
``test_verdict_survives_when_evidence_pack_is_absent``),
``test_wikidata_verdict_persistence.py``.

### Rule W-156 — The judge's evidence projection MUST NOT be the fingerprint projection (added 2026-08-05)

Incident: export (23) of run `48ba6c13` came back 204 full / 27 pass / **29
partial** / 6 fail, and 28 of the 29 partial verdicts spent their reasoning
hedging about a duplicate check — "the duplicate check was not run", "the result
was empty", "duplication cannot be ruled out". The probe had in fact answered
`absent` for 293 of the 343 items.

The judge had never seen it. `compact_wikidata_verify_fixture_item` built the
fixture with `fingerprint_verify_evidence`, which strips
`wikidata_existing.duplicate_check` and `llm_proposals` — correctly, because
Rule W-136 forbids keying a verdict on state the read path cannot reproduce. But
`wikidata_item.py` renders both channels as first-class prompt blocks and the
rubric instructs the judge on both, so they rendered as `{}` on every item and the
rubric's "unknown ⇒ do not conclude the item is new" branch fired 343 times.

Rule W-136's "one projection" bullet was taken one step too far. **What the judge
reads and what keys the verdict are different questions.**

Invariant:

1. **Two named projections.** `judge_evidence_projection` builds the fixture and
   keeps every channel the rubric names; it drops only `marc`, which travels
   separately in `marc_extracted.json`. `fingerprint_verify_evidence` is
   unchanged and remains the only projection used for keys and persist slims.
2. **A channel the rubric asks about MUST be in the judge projection.** Adding a
   prompt block to an evaluator without adding the channel here is the same bug.
3. **Changing the judge projection is a schema bump** (Rule W-51): the judge's
   input changed, so every prior verdict must miss. This one is `w156_v1`.

### Rule W-157 — A verdict judged without a conclusive duplicate answer MUST be re-judged once one exists (added 2026-08-05)

Verdicts written on 2026-08-02 and 08-03, while the probe was still reporting
`skipped` / `not_run`, were reused verbatim after the probe later answered for 314
items. Nothing anywhere re-judged on a duplicate-status transition.

The obvious fix — put `duplicate_check` in the fingerprint — is the one Rule W-136
forbids: the review table cannot reproduce a live probe, so every verdict would
read as stale and the AI-verdict column would empty out again.

Invariant:

1. **Record the class, do not key on it.** Every persisted verdict carries
   `duplicate_status` (the raw status) and `duplicate_class` — `probed-conclusive`
   when the status is `absent` / `candidates_found` / `already_linked`, `unknown`
   otherwise. The class is derived from the persisted answer, so the read path can
   reproduce it; the raw payload still never enters a fingerprint.
2. **Re-judge at scope partitioning.** `cached_verdict_needs_duplicate_rejudge`
   sends a cache hit back to the judge when its stored class is `unknown` and the
   item is now `probed-conclusive`. A verdict with no class at all was judged
   before this rule, with the probe stripped from its fixture, so `unknown` is the
   truthful reading.
3. **It MUST terminate by construction.** After the re-judge the stored class is
   conclusive, so the item is no longer eligible; an item whose probe is still
   inconclusive was never eligible.
4. **`sanitise_stale_wikidata_verdict` is NOT touched.** No new accept branch, no
   fingerprint change — Rules W-136 / W-148 / W-149 / W-150 / W-151 / W-152 cannot
   regress. Read-path visibility comes from `annotate_duplicate_rejudge`, a pure
   annotator that adds `needs_rejudge` and never drops a verdict. It runs on the
   export and the single-row drawer, deliberately **not** on the `list_view` table
   path (Rule W-131's payload budget).

### Rule W-158 — A judge failure MUST NOT persist as a substantive verdict (added 2026-08-05)

Incident: one manuscript in run `48ba6c13` was stored as `overall="fail",
name_ok="no", type_ok="no", role_ok="n/a", reasoning=""`. That is exactly the
eval-agent `Verdict` dataclass default set. A transport failure, a parse failure
and budget exhaustion were all indistinguishable from a reasoned rejection.

Three things had to be wrong at once. `parse_verdict(None)` returned the defaults
unchanged; the envelope-level `error` that explained it ("no verdict (judge
failure)") was never read on the persist path; and `_compact_verdict_for_job`
dropped a falsy `reasoning` while keeping the axes, so the UI rendered a
reasoned-looking fail with no reason.

Invariant:

1. **`verification_failed`, not `fail`.** A judge that did not answer reports
   `overall="verification_failed"` with `unknown` axes and a reasoning that says
   it is not an assessment. A substantive `overall` with blank `reasoning` is the
   same thing — reachable because the agentic tool-loop cannot send a
   `responseSchema`.
2. **The failure MUST carry its reason.** The stored-row schema requires a
   non-empty `reasoning` for a substantive `overall` and a non-empty `error` for
   `verification_failed`. `_load_schema` strips those conditionals and the
   harness-only enum values before the schema becomes a `responseSchema`: the
   model is never offered a way to declare its own check failed.
3. **NEVER cache a judge failure** (amends Rule W-51). The `ai_verdict` cache has a
   90-day TTL, so a transport hiccup would warm-hit for three months and the item
   would never be judged again. The override row is still written, so the curator
   sees "check failed" rather than nothing.
4. **A run that produced one is `partial`, not `complete`.** A
   `verification_failed` row carries a stable candidate id so it still advances
   progress (Rule W-64), but the run did not judge its whole scope.
5. **Every `overall` the backend can emit MUST be in the frontend allowlists**
   (Rule W-110.5) — `WikidataItemsPanel`'s streamed-verdict set and
   `VerdictsTable`'s `Overall` union and filter chips. An unlisted value is
   silently dropped.
