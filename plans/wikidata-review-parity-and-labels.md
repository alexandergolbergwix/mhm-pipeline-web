# Plan: Wikidata work-labels + review-surface parity (4 features)

Origin: 2026-06-13 session. The Linked-Data-Explorer/Wikidata AI verification
showed **50/50 work items failing** because work English labels are the bare
`NLI <id>` fallback and some Hebrew labels are raw cataloging notes. The eval
agent is **correct** — it's surfacing real label bugs. Plus two parity asks.

Decisions locked with the user (2026-06-13):
- Transliteration on the web → **run TaatikNet on Modal** (not model-free tiers).
- Build all four features below.

Each feature is independently mergeable behind its own tests. Recommended:
one focused session per feature (context hygiene).

---

## Root-cause evidence (confirmed this session)

- `converter/wikidata/taatiknet_translit.py:82` `_load_taatiknet()` returns
  `None` on the web backend — the 1.1 GB `malper/taatiknet` model is **not
  bundled** (verified: no `*.safetensors` under taatiknet on disk). So
  `transliterate_hebrew_to_latin()` → `None` for every work.
- `converter/wikidata/item_builder.py:2899-2914` `_get_or_create_work`: when
  TaatikNet returns `None`, English label falls back to `f"NLI {ms_control_number}"`.
  That literal string is what the eval agent (correctly) fails.
- Hebrew label bug: `item_builder.py:2882` sets `work.labels["he"] = title`
  where `title` for NER-contents works comes from
  `item_builder.py:1914` `work.get("text")` (a `contents_ner` WORK span). Those
  spans sometimes include folio markers + content descriptions
  (`דף 1א-ב, 45ב-65א … עם ניקוד טעמים ומסורה`) rather than a clean title
  (Rule 47 flagged the "1) Daf e" class as a Stage-2 NER artifact).
- Authority review (`frontend/src/components/authority/AuthorityTable.tsx:42`)
  is already a filterable table but lacks the MARC novelty column; that column
  is computed only in the extraction path
  (`backend/app/routers/extraction.py:360` → `marc_structured_index.classify`).
- Wikidata Studio tables are custom (`StatementTableView`
  `frontend/src/routes/WikidataStudio.tsx:887`) + shared `VerdictsTable.tsx`;
  neither uses the rich `frontend/src/components/extraction/EntityTable.tsx`.

---

## Feature 1 — TaatikNet on Modal (English work labels)

**Modal app** (`modal/modal_app.py`):
1. Add `malper/taatiknet` to the baked weight artefacts list (~line 43) and
   `_bake_weights()` (~line 68) via `transformers` `from_pretrained` snapshot.
2. Vendor `converter/wikidata/taatiknet_translit.py` + `hebrew_translit.py`
   into the image (extend the `add_local_dir` steps ~line 129; currently only
   `ner/` + `converter/authority/` are vendored).
3. In `MhmNer.load()` (~line 177) warm the TaatikNet model once.
4. Add a `/transliterate` POST route in the ASGI app (`web`, ~line 266):
   body `{text: str}` → `{latin: str | null}` calling
   `transliterate_hebrew_to_latin(text)` (strip stress marks; per-word join
   already handled in the vendored module).

**Web backend**:
5. New `app/pipeline/translit_backend.py` (mirror `extraction_backend_modal.py`
   pattern, Rule W-11/W-12): POST to `MODAL_NER_URL/transliterate`, route
   through `inference_cache.cache_lookup_or_call` (kind="translit"), honour
   `MHM_NO_NETWORK`, return `None` on any failure.
6. Wire it as Tier 4 in `converter/wikidata/hebrew_translit.py`
   `english_label_for_hebrew`: when `MODAL_NER_URL` is set, Tier 4 calls the
   Modal client instead of the local `taatiknet_translit` model load. Keep the
   local-model path as the desktop default. **Do not** change Tiers 1/2/3/5.

**Tests**: Modal client unit (mock POST), `english_label_for_hebrew` picks
Modal Tier 4 when env set, falls through to NLI id when Modal returns null.

**Deploy (operator)**: `modal deploy modal/modal_app.py` then
`heroku config:set MODAL_NER_URL=...` (already set for NER — same URL serves
`/transliterate`). Needs Modal auth — cannot be done headless here.

---

## Feature 2 — Hebrew work-label sanitiser

**`converter/wikidata/item_builder.py`**:
- Add `_is_noise_work_title(title) -> bool` + `_clean_work_title(title) -> str`
  near `_split_work_title_author` (~line 834). Detect folio-range/cataloging
  noise: leading `דף`/`folio` + range patterns (`\d+[אבא-ת]?[-–]`),
  embedded `ניקוד|טעמים|מסורה|כתובים` description tails, underscore-joined
  runs. Strip a leading folio range; if what remains is still noise (no real
  title token), **skip creating the work** (return without emitting P1574 for
  that span) rather than emit a garbage label.
- Apply in both NER-contents (`~1914`) and contents-record (`~1860`) paths
  before `_get_or_create_work`.

**Tests**: noise titles skipped/cleaned; clean titles untouched; the exact
screenshot string (`דף 1א-ב, 45ב-65א …`) is rejected.

NOTE: the deeper fix is Stage-2 contents-NER (desktop `ner_post_filters.py`
Rule 41) so WORK spans don't include folio markers — out of scope for the web
item_builder patch but worth a follow-up in the desktop repo.

---

## Feature 3 — Authority MARC novelty column

**Backend**: in the endpoint returning `AuthorityMatch` rows (the run detail /
authority list), compute `marc_index.classify(control_number, entity_text)`
per match using a role→fields map mirrored from
`marc_structured_index._TYPE_TO_FIELDS` (person→contributors/authors,
place→places/related_places, …). Return as `exists_in`. Compute on read — no
migration. Honest semantics: MARC name-field matches → `grounded`; NER-merged
names not in structured MARC → `novel`.

**Frontend** (`AuthorityTable.tsx`): add the `exists_in` column + reuse the
extraction `_EXISTS_BADGE` renderer + a per-column filter (same machinery
already present).

**Tests**: classify mapping for authority roles; column + filter render.

---

## Feature 4 — Wikidata table → extraction-style table

Replace `VerdictsTable` styling (and optionally `StatementTableView`) with the
`EntityTable` pattern: per-column right-click filter popups, sortable headers,
quick-filter chips, AI-verdict pills, consistent glass styling. Extract the
shared table shell from `extraction/EntityTable.tsx` into a reusable component
so verdicts/authority/wikidata all share it (avoids a 3rd divergent table).

**Tests**: Playwright parity spec mirroring `extraction-review.spec.ts`
(Rule 59) for the Wikidata verdicts surface.

---

## Feature 5 — Auto-fix-with-AI in Wikidata Studio (parity with extraction)

Mirror the extraction auto-fix loop for Wikidata items. Extraction reference:
the eval-agent verdict carries `suggested_fix {text, reasoning, source_field,
confidence:"high"}` (`frontend/src/api/extractionApprovals.ts:63`); the table
shows an Auto-fix button only when `confidence === "high"` and the value
differs (`EntityTable.tsx:309-321`); applying patches the entity then
re-verifies that one row (`EntityTable.tsx:271` `applyAutoFix`).

**Key difference**: an extraction fix is a single `text` value; a Wikidata item
is structured (labels.en / labels.he / descriptions / statements). So the fix
must be **targeted**.

**1. Eval-agent (`wikidata_item` evaluator, sibling eval-agent repo — Rule 48
file/subprocess boundary, no Python import):**
- Extend the verdict schema with `suggested_fix` (nullable):
  `{ target: "label.en" | "label.he" | "description.en" | "statement.<PID>",
     value: str, reasoning: str, confidence: "high" }`.
  Emit only when the judge is confident (e.g. the failing item has a
  `NLI <id>` English label and the judge can propose the transliteration, or a
  Hebrew label full of cataloging notes where the real title is recoverable).
  Leave `null` otherwise — never low-confidence guesses (matches
  `text_high_confidence_v1` policy).
- Bump the cache-schema marker (cf. `extraction_verify.py:434`
  `suggested_fix_policy`) so pre-fix cached verdicts aren't served as current.

**2. Backend (`app/routers/wikidata_studio.py` + `ai_verify.py`):**
- Persist `suggested_fix` on the wikidata verdict (already stored in
  `AuthorityMatch.payload["ai_verdict"]` / the wikidata verdict cache by
  `ai_verify.py:451`). Store explicit `null` to distinguish "no fix" from
  "absent" (mirror `extraction_verify.py:505`).
- Apply path: reuse the existing
  `PATCH /runs/{runId}/wikidata-studio/items/{localId}` (writes
  `WikidataItemOverride` — accepts `labels` / `descriptions` /
  `statement_edits` / `add_statements` / `remove_statements`). Map a
  `suggested_fix.target` to the right override field:
  `label.en → labels: {en: value}`, `statement.<PID> → statement_edits`, etc.
  No new write endpoint needed; auto-fix is just a programmatic override PATCH.
- Re-verify one item after apply (analogue of `recheckEntity`): a single-item
  `start-stream` scoped to that `localId`, or a lightweight re-judge call.

**3. Frontend (the Feature-4 shared table):**
- Add a "Fix" column to the Wikidata table; show the Auto-fix button when
  `verdict.suggested_fix?.confidence === "high"` and the proposed value differs
  from the current label/value. Tooltip = `suggested_fix.reasoning`.
- `applyAutoFix` → call the override PATCH with the mapped field → mark the row
  "re-checking…" → re-verify → refetch (copy the extraction coalesced-refetch
  pattern at `EntityTable.tsx:271-298`).
- A guard: never auto-fix a statement whose change would touch the Wikidata
  WRITE path — overrides are review-surface only; the actual upload still goes
  through the moratorium/Rule-38 guards downstream. (Auto-fix edits the local
  override, not live Wikidata.)

**Dependency**: best built AFTER Feature 1 (TaatikNet on Modal) — once real
transliterations exist, the most common suggested_fix (`label.en` from
`NLI <id>` → transliteration) becomes high-value, and many items won't even
need fixing.

**Tests**: eval-agent emits targeted suggested_fix (unit, eval-agent repo);
backend maps target→override field; frontend shows/apply button gated on
high-confidence + value-differs; Playwright apply→re-verify loop.

---

## Cross-cutting

- Respect mhm-pipeline-web Rules W-11..W-15 (three backends, shared inference
  cache, modal/ is a deploy target never imported).
- Respect desktop pipeline safety rules (23/25/38) — none of these touch the
  Wikidata write path; all are label-quality + read-side review UI.
- After Feature 1+2, re-run AI verification on a work-heavy run; the 50/50
  fail rate should drop sharply (only genuinely-bad labels remain).
