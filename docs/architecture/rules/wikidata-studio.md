# Wikidata Studio (public projection + write path)

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

### Rule W-26 — Wikidata Studio build result is cached in Postgres (added 2026-06-03)

`backend/app/models/wikidata_studio_cache.py::WikidataStudioCache`
caches the last successful `build_studio` result per `(run_id,
approved_only)` keyed by `input_fingerprint` (SHA-256 over
records + matches + entities + overrides, computed by
`pipeline/wikidata_studio.py::compute_build_fingerprint`).

On every call to `POST /wikidata-studio/build`:
1. Load raw data, compute fingerprint.
2. If `WikidataStudioCache` hit with same fingerprint → return
   cached `result_items`, `quickstatements`, `summary` instantly.
3. On miss, run full `build_items_for_run`, upsert cache row.

Cache is invalidated automatically whenever any input changes
(new approvals, new matches, overrides edited). Never invalidate
manually — changing the input data is sufficient.

Migration: `0015_wikidata_studio_cache`.

### Rule W-26 — Every Wikidata P/Q constant in property_mapping.py must be verified live (added 2026-06-04)

The 2026-06-04 property audit found three silent catastrophes in
`backend/converter/wikidata/property_mapping.py`:

1. **`Q_PALIMPSEST = "Q179808"`** was the **Palme d'Or** (Cannes film
   award). Every palimpsest manuscript was tagged `P31 = Palme d'Or`.
   Correct QID: `Q274076`.
2. **`P_NUMBER_OF_FOLIOS = "P7416"`** — P7416 "folio(s)" is a STRING
   **citation qualifier** ("this statement references folio 15r of source
   X"), not a count property. Folio count must use P1104 (number of pages)
   with unit Q107256474 (leaf). P7416 as a folio-reference qualifier on
   colophon/scribal-intervention statements is correct.
3. **P50 directly on manuscript items** — Property:P50 has an explicit
   Wikidata constraint: *"use exemplar of (P1574) to connect the manuscript
   to the work(s) it contains; never connect directly the manuscript to the
   author(s)"*. The correct chain: `manuscript → P1574 → work → P50 → person`.

**Invariants enforced by the moat layer (`item_validator.py`):**

- `P50_ON_MANUSCRIPT` (error): P50 on any `entity_type="manuscript"` item.
- `P7416_AS_QUANTITY` (error): P7416 with `value_type="quantity"`.
- `P31_WRONG_QID` (error): any P31 value in `_KNOWN_BAD_P31_QIDS`
  (Q179808, Q5 on manuscripts, …).

**Regression tests** (14 cases in `backend/tests/unit/test_item_validator.py`):
`TestP50OnManuscript`, `TestP7416AsQuantity`, `TestP31WrongQid`,
`TestBuilderNeverViolatesNewChecks`.

**When adding or modifying a P/Q constant:**
1. Open `https://www.wikidata.org/wiki/Property:PXXX` (or `/QXXX`).
2. Read the **Statements** panel: confirm the label, data type, and
   any scope-note constraints match your intended use.
3. For `P_*` constants: check the *item of property constraint* notes —
   these are the community's formal "never use this on X" rules.
4. Add a `_KNOWN_BAD_P31_QIDS` entry and a unit test if you're removing
   a wrong constant so it can never silently sneak back in.
5. Never add a constant derived from memory or documentation alone —
   always fetch the live page. Constants can change (merges, redirects).

### Rule W-27 — Wikidata Studio curator controls (added 2026-06-04)

Five new controls were shipped in one session; any future Studio work must
respect these invariants:

**Force-rebuild toggle** (`?force_rebuild=true` on `GET /wikidata-studio`):
Bypasses the `WikidataStudioCache` fingerprint lookup and always runs a
full rebuild. The cache-write at the end still fires so the next normal
GET is served from cache. UI: "Skip cache (force fresh build)" checkbox
next to the Rebuild button.

**Validator badge** (`validation_issues` in build response):
`item_validator.validate_item(item)` is called for every built item inside
`_build_sync`; issues are returned as `[{code, severity, message}]` on each
serialised item. The frontend renders a red/yellow dot in the sidebar and
inline alert chips in the ItemPanel header. This surfaces the moat-layer
checks (P50_ON_MANUSCRIPT, P31_WRONG_QID, etc.) to curators before upload.

**Item-level approval** (`approved: bool | None` on `ItemOverridePayload`):
Stored in `wikidata_item_overrides.payload.approved`. The QS download
(`?approved_only=true`) and upload (`POST /upload` with
`upload_approved_only=true`) filter to approved items only when the flag is
set. UI: `ItemApprovalBadge` (○ / ✓) in sidebar and ItemPanel header.

**Inline statement exclude** (`remove_statements` via PATCH):
Each statement row in the ItemPanel gets a ✗ Exclude / Undo button.
Clicking immediately PATCHes `{remove_statements: [...current, i]}` on the
override and strikes through the row optimistically. No new DB column — this
reuses the existing `remove_statements` list in `ItemOverridePayload`.

**Approved-items-only upload/QS gate**:
"Approved items only" checkbox + live "N of M approved" count badge in the
Studio header. When active, QS download appends `&approved_only=true` and
upload POST includes `upload_approved_only: true`.

DB migration: `0016_item_override_approved` adds `approved BOOLEAN` to
`wikidata_item_overrides` (nullable; null = not reviewed).

### Rule W-30 — Wikidata upload is fail-closed: reconcile-before-create + validator gate IN the write path (added 2026-06-08)

Context: the 2026-06-07 bulk-deletion request (Epìdosis, ~5,948 items)
targets the OTHER April failure mode — mass *duplicate* creation and
non-notable items — distinct from the mass-*merge* that Rule W-4's
four-stage guard prevents. The 2026-06-08 audit found the create path
unguarded: the `/reconcile` endpoint was decorative (it mutated
in-memory items that `/upload` discarded by rebuilding fresh), manuscripts
reconciled by P8189 (which they never carry — they use **P3959**) so they
could never dedup, `reconciler._query` swallowed SPARQL errors as "absent"
→ CREATE, and `validate_item` ran only at build time as a UI badge.

Invariants now enforced in `backend/app/pipeline/wikidata_upload.py`
(NOT in the byte-identical `uploader.py` — Rule W-4):

- **Reconcile happens INSIDE `upload_items` / `_upload_sync`**, not just in
  the `/reconcile` preview. `_prepare_for_upload(items, reconciler)` is the
  single gate every item passes before any write (dry-run and live alike, so
  the dry-run preview is truthful). The `/reconcile` endpoint is PREVIEW
  ONLY and must never be relied on to change upload behaviour.
- **Each creatable entity type has a dedup path.** Manuscripts reconcile by
  P3959 (`reconcile_manuscript_by_identifiers` → P3959 then shelfmark);
  persons go through the conflict-checked `reconcile_person_by_identifiers`
  (cross-identifier verification — the anti-conflation guard from
  2026-04-13); works go through `reconcile_work_by_label_and_author` (rejects
  a candidate whose P50 author differs). The raw first-match path is gone.
- **Fail closed on lookup error.** `reconciler._query` raises
  `ReconciliationUnavailableError` (NOT `[]`) on any network/429/5xx. An item
  whose lookup can't complete is BLOCKED (`status="blocked"`), never created —
  a transient WDQS outage must never be read as "no existing item".
- **`validate_item` is a HARD GATE before write**, not advisory. Any
  ERROR-severity issue (NO_IDENTIFIER, KOVETZ_PLACEHOLDER, P50_ON_MANUSCRIPT,
  P31_WRONG_QID, …) blocks the write regardless of create-vs-update and
  regardless of UI approval state.

Tests pinning the contract: `backend/tests/unit/test_wikidata_upload_guards.py`
(15 cases). Any new external-write path or reconcile change MUST extend it.

**Residual gap (NOT yet closed):** the QuickStatements download
(`/quickstatements.txt`) emits raw CREATE lines with no reconcile/validate
gate — a curator pasting it into the QuickStatements tool bypasses these
guards. The `item_approved_only` filter is the only control there. Close
this (or document the manual-review requirement) before any bulk QS import.

### Rule W-57 — Wikidata Studio HMO-parity surface + write-path ledger (added 2026-07-10)

The Wikidata Studio had fail-closed upload guards (Rule W-30) but lacked the
review surface and durable state the HMO Wikibase Studio accumulated through
Rules W-41…W-56: no upload audit trail, no merged read model, no AI-verdict
pills outside the verify modal, no single-item push, no global QID ledger, and
an ungated QuickStatements export that bypassed every guard.

Invariants now enforced:

- **Merged read model** — `fetch_merged_wikidata_items` joins build cache +
  curator overrides + global QID ledger + latest `wikibase_cloud_writes` row
  (channel `wikidata_upload`) + stale-sanitized `ai_verdict` on the override row.
- **Durable upload audit** — `record_wikibase_write` per live outcome
  (`create`/`update`/`adopt`/`skip`/`failed`/`blocked`); dry-run writes none.
  Reuses `wikibase_cloud_writes`, not a parallel table.
- **AI verdict persistence** — `WikidataItemOverride.ai_verdict`/`ai_verdict_at`
  after verify streams (Rule W-17 pattern); content-addressed cache keys
  (`WIKIDATA_VERDICT_SCHEMA`, currently `w57_v1`).
- **Global QID ledger** — `wikibase_entity_mappings` rows with
  `wikidata:{marc|person|work}:…` keys (`run_id IS NULL`) recorded on CREATE,
  ADOPT, and per-item reconcile; consulted before WDQS on every write path.
  Test-mode keys use the `wikidata-test:` prefix.
- **Adopt semantics** — reconcile match → `adopted`/`would_adopt` outcome +
  ledger write + audit `OPERATION_ADOPT`, not silent UPDATE flip.
- **Single-item push** — `POST …/items/{local_id}/push` applies override-merged
  state, runs full `_prepare_for_upload`, commits DB tx before network (Rule
  W-40). Moratorium/test-mode honesty unchanged.
- **Gated QuickStatements** — `GET …/quickstatements.txt?gated=true` (default)
  runs `_prepare_for_upload`; blocked items excluded with comment header; 503 on
  `ReconciliationUnavailableError`. Escape hatch: `gated=false&ack=raw` (logged).
- **Frontend parity** — `WikidataItemTable` + `WikidataItemDetailDrawer` +
  `WikidataUploadPanel` (pre/post verify confirm gate, Rule W-41/W-44); legacy
  sidebar view preserved as secondary toggle. Bulk item loads use
  `fetchAllStudioItems` (`STUDIO_MAX_PAGE_SIZE=500`) — the API rejects
  `page_size>500` with a 422 that surfaces as an empty table.

Tests: `test_wikidata_item_views.py`, `test_wikidata_verdict_persistence.py`,
`test_wikidata_qid_ledger.py`, `test_wikidata_single_push.py`,
`test_wikidata_export_quality.py`, `test_wikidata_items_export_import.py`,
extended `test_wikidata_upload_guards.py`, `frontend/e2e/wikidata-item-*.spec.ts`.

Migration: `0033_wikidata_ai_verdict`.

---

### Rule W-65 — Wikidata projections MUST expose clean labels and verifier evidence (added 2026-07-11)

Production verdicts showed recurring false failures from catalog IDs embedded in work labels, unknown MARC roles defaulting to authors, and authority-derived person claims judged without the authority match that produced them. The builder now keeps IDs in P3959/source metadata, skips unsupported or non-person roles instead of inventing author claims, records compact authority evidence, and annotates resolvable `__LOCAL:` links. The verifier prompt, cache, and export preserve that evidence; coherent year-level Wikidata dates (`precision=9`) are valid.

---

### Rule W-66 — Studio build joins MUST canonicalise all MARC control-number inputs (added 2026-07-11)

After a Studio rebuild displayed 162 items rather than the prior 294, logs showed every person being skipped for lack of an external identifier. Records had clean control numbers while approved authority matches used the same IDs wrapped in quotes. `build_items_for_run` grouped matches by their raw stored key, so no authority match joined its source record; the builder correctly failed closed, but did so for the entire person corpus.

The Studio boundary now applies `canonical_control_number` to record, authority-match, and NER-entity keys before grouping. Future build inputs MUST use this canonical key at every join boundary; local measurement must mirror the production authority-row shape. Test: `test_wikidata_studio_control_number_join.py`.

### Rule W-67 — Wikidata projections MUST fail closed on unsupported semantic claims (added 2026-07-12)

The latest 228-item verdict export still contained three hard failures: a generic holiday term emitted as a work, a raw MARC 505 title (`Diodati Segre`) promoted to a work despite an unapproved NER/authority match, and the corporate institution Hekhal Shlomo emitted as a human. Partial verdicts also exposed machine-generated English labels, arbitrary P921/P136 enrichment, archival P7535 claims on ordinary manuscript notes, and a wrong Curt Paul Janz QID used as a provenance role.

The builder now requires a known or curator-approved work identity for structured contents, honors NER approval, gates English labels on trusted catalog romanization, skips unresolved corporate authorities, accepts person dates only from exact authority-ID matches, disables network subject lookup and genre inference by default, removes unsupported manuscript claims, and invalidates old verdict-cache keys. The evaluator receives contents/genre/catalog evidence and treats P5008 as administrative and P7535 as archival-only. Future projection changes MUST preserve this fail-closed boundary and add source-backed property mappings before emitting claims. Tests: `test_wikidata_studio_works.py`, `test_item_validator.py`.


### Rule W-68 — Wikidata work projection MUST be source-aware, not authority-only (added 2026-07-12)

Rule W-67 stopped catalogue fragments by requiring every structured work to have a known QID or approved authority row. That overcorrection removed all 94 works from a 228-item run and dropped Wikidata Studio to 131 items, even though most MARC 505 rows were legitimate structural contents evidence. The original false positives came instead from the MARC 500 parser: it matched כולל anywhere in prose, split every comma and Hebrew conjunction-vav, and reused persisted derived candidates after parser fixes.

The build now uses one source-aware candidate boundary. Clean Hebrew MARC 505 titles may create works; MARC 500 titles must come from a trigger anchored at the note start or a manuscript noun and from quoted spans or recognised title heads; rejected NER stays rejected; Latin-only 505 headings require authority or a known QID. Every accepted/rejected decision retains source field/text, folio, sequence, and reason in `work_candidate_evidence`. Rebuilds recompute 500 candidates from raw MARC and delete older 500-derived contents, so code changes do not need a data migration. New works without accepted evidence fail validation.

Projection quality is part of the same invariant: embedded author suffixes are removed from public work labels and used only for exact author linking; work P1476 uses the title script; works never inherit manuscript P407; English labels still require trusted catalog romanization; quoted generic manuscript titles such as `"קובץ."` fall back to the control-number signature. Build fingerprints include MARC JSON content (not only control numbers), and schema/verdict-cache salts must change whenever this evidence shape changes.

A measurement-only rebuild of run `48ba6c13-115c-4763-bff1-c08b9031b518` with this boundary produced 183 reviewable items: 68 manuscripts, 63 externally grounded people, and 52 source-backed works. The excluded set contained catalogue prose/dedications/geography plus the unverified Latin heading `Diodati Segre`. Tests: `test_notes_work_extraction.py`, `test_wikidata_work_candidates.py`, `test_wikidata_studio_works.py`, `test_rdf_build.py`, and `test_wikidata_manuscript_labels.py`.

### Rule W-69 — Work identity, author evidence, and export fields MUST remain complete (added 2026-07-12)

The approved section export for run `48ba6c13-115c-4763-bff1-c08b9031b518`
exposed four independent quality defects: all 52 work rows had no existing QID,
all extracted work authors lacked P50/P2093, 20 English descriptions copied Hebrew
author names, and 8 labels lost internal Hebrew abbreviation marks. The legacy
section export also omitted item-level `approved` and `ai_verdict`, while its
`approved_only` flag only filtered authority/NER inputs.

The web projection now uses only exact, verified work aliases; exact approved
author authority matches produce P50 before the person pass; unresolved extracted
authors are retained as a local P50 target or P2093; English descriptions remain
English; and normalization preserves Hebrew gershayim while cleaning P1476. The
section export stamps explicit item-review fields and complete source/verdict JSON,
and `backend/scripts/check_wikidata_export_quality.py` reports compact,
source-grounded failures. Do not add fuzzy QID guesses or treat `approved_only`
as item approval/AI success. Tests: `test_wikidata_studio_works.py`,
`test_wikidata_export_quality_checker.py`, `test_section_export_router.py`.

### Rule W-70 — Work projection MUST consume enriched content metadata (added 2026-07-14)

The post-W-69 audit showed that the source records already carried contents-NER
`author` values and approved work authority rows already carried Wikidata QIDs,
but `content_projection` read neither field. The result was authorless local work
items and avoidable duplicate work candidates even when an approved work match
existed. The projection now reads `author`/`author_name`/`work_author`, validates
content and approved-match QIDs as `Q\d+`, and emits direct P1574 references for
approved existing works. Unknown titles remain fail-closed and never receive fuzzy
QID guesses. Tests: `test_wikidata_studio_works.py`.

### Rule W-72 — Public Wikidata semantic claims MUST be evidence-gated (added 2026-07-15)

The Phase 1 audit of run `48ba6c13-115c-4763-bff1-c08b9031b518` found
false-positive P921 topics, over-specific P136 genres, historical owners and
censors projected as P127, catalog workflow text projected as P1684, and
external 710 institutions replaced by a default NLI collection. It also found
MARC wrapper cleanup deleting legitimate Hebrew gershayim. The public projection
now protects Hebrew abbreviation marks, filters explicit catalog-note markers,
requires canonical/primary subject evidence, gates unsafe genre/P31 mappings,
keeps non-current roles out of P127, and emits P195 only from a verified current
holder QID (with an evidence-based description fallback). Rejected candidates
remain in source/evidence fields for curator reconciliation. Tests:
`test_wikidata_phase1_projection.py`, `test_marc_650_655_lod.py`, and
`test_wikidata_work_candidates.py`.


### Rule W-73 — Illustrated genre MUST NOT imply illuminated manuscript (added 2026-07-15)

The second Wikidata Studio verdict audit found 44 manuscripts marked partial
because MARC 655 `Illustrated works (Manuscript)` and catalog prose mentioning
illustrations were projected as P136/P31 `Q48498` (illuminated manuscript). The
projection now separates genre support from instance typing: that MARC label is
not statically mapped to Q48498, free-text notes and `has_decoration` are not
sufficient, and P31=Q48498 requires an authority-stamped QID or structured
confirmed decoration evidence. Rejected genre evidence remains available for
review. Tests: `test_wikidata_phase1_projection.py`.


### Rule W-75 — P195 MUST NOT default to the NLI collection (added 2026-07-15)

The fourth Wikidata Studio export contained 43 partial manuscripts with
`P195=Q188915` even though no verified current-holder collection evidence was
present. The manuscript projection now emits P195 only when a current holder has
a verified organization QID; absence of an external holder no longer implies
the National Library of Israel collection. The description may still name the
source institution. Tests: `test_wikidata_phase1_projection.py`.


### Rule W-76 — Hebrew gershayim MUST NOT trigger quote-noise warnings (added 2026-07-15)

The fourth Wikidata Studio export retained five partial work items because
legitimate internal Hebrew abbreviation marks such as `פע"ח` were counted as
unbalanced ISBD wrapper quotes. The validator now removes quote characters
between Hebrew letters before checking wrapper balance, while still flagging
surrounding and doubled wrapper noise. Test: `test_item_validator.py`.

The seventeenth export showed the same defect had reappeared in the offline
gate: `check_wikidata_export_quality.py` flagged twelve clean work titles
(`פרוש רש"י`, `תשב"ץ`, `תרגום רס"ג לתורה`) as
`title_claim_has_quote_wrappers` because it tested for any `"`. Every surface
that judges quote noise — validator and offline gate alike — MUST flag only
surrounding wrappers and the doubled-quote MARC escape. Test:
`test_wikidata_export_quality_checker.py::test_gershayim_in_a_title_claim_is_not_quote_noise`.


### Rule W-77 — Explicit catalog semantics MUST survive projection (added 2026-07-15)

The fifth Wikidata Studio export showed that conservative Phase 1 guards had
become too lossy: canonical NLI current-holder evidence was omitted, exact
Masorah subjects were not projected, MARC-100 author/title records had no work
chain, and a printed facsimile was typed as a manuscript. The projection now
uses verified canonical mappings, a source-backed manuscript→P1574→work author
fallback, explicit facsimile detection, and a verified Masorah P921 mapping.
Ambiguous provenance and free-text signals remain excluded. Tests:
`test_wikidata_phase1_projection.py`.


### Rule W-78 — Exported authority and work identity MUST stay precise (added 2026-07-15)

The sixth Wikidata Studio verdict export retained placeholder `Unknown Library`
descriptions, incomplete Latin/inverted authority names, and an existing
canonical Mishneh Torah QID on a partial-books work. The projection now omits
known placeholder institutions, preserves authority forms as aliases, and
requires exact work-title matches before using hardcoded canonical QIDs. Tests:
`test_wikidata_phase1_projection.py` and `test_wikidata_studio_works.py`.


### Rule W-82 — Manuscript labels MUST reflect the physical holder (added 2026-07-16)

A Ktiv/NLI catalog record does not imply that the manuscript is physically held
by NLI. When MARC identifies a current owner, the English shelfmark label and
P195 description must not claim Jerusalem/NLI ownership. Shelfmarks and P217
values must pass the shared MARC label normalizer so catalog quote wrappers
cannot reach Wikidata.

### Rule W-98 — Wikidata projection MUST follow the WikiProject Manuscripts data model fail-closed (added 2026-07-24)

Manuscript items are physical carriers. Contained texts attach only via
`P1574` (exemplar of); **P50 never appears on a manuscript** (including
anonymous `somevalue`). Folio counts use `P1104` + leaf unit, never
`P7416` as quantity. Preferred `P31` values are `Q87167` /
`Q48498` / `Q274076` / `Q30103158` / `Q33308141`; discouraged classes such as
`Q213924` (codex) are errors. Catalog control number `P3959` is required
(ERROR). `P17`/`P131` are not inferred from holder/catalog alone. Content
links carry catalog title form as `P1932`; thin unidentified titles may use
`Q234460` (*text*) rather than a fuzzy work QID. Annotator/commissioner roles
map to `P11105`/`P88`; editorial roles stay off the manuscript. The build
export-quality gate runs the full `validate_item` ERROR set before cache
write. Contract: `docs/wikidata-manuscripts-data-model.md`. Tests:
`test_wikidata_wpm_guards.py`, `test_item_validator.py`,
`test_wikidata_export_quality.py`.

### Rule W-99 — Wikidata write path MUST smart-check existence and own-or-accept modify (added 2026-07-24)

Every CREATE/UPDATE (live and dry-run) runs the access map in
`docs/wikidata-data-access.md`: ledger → type-aware SPARQL reconcile →
Action API `wbgetentities` alive check → ownership via first-revision /
token (Rule-38 channels). Defaults: **CREATE** only when no live QID;
**UPDATE** only when the acting Wikidata token created the item. A
foreign existing QID is **skipped/blocked** (never duplicate-CREATED)
unless the curator sets per-entity `accept_foreign_modify` +
`accepted_foreign_qid` matching that QID on `WikidataItemOverride`.
Accept primes an audited Rule-38 bypass for that QID only. Upload jobs
and sync upload load accepts from overrides; the drawer exposes the
checkbox. Tests: `test_wikidata_existence.py`,
`test_wikidata_upload_guards.py`.

### Rule W-100 — Project Wikibase P/Q MUST map to public Wikidata via ontology, never by ID identity (added 2026-07-24)

Local IDs on `mhm-hmo.wikibase.cloud` are a different namespace from
wikidata.org. Canonical HMO→Wikidata projection uses
`converter/wikidata/hmo_wikidata_pq_mapper.py`: ontology local-name/URI
→ public PID/QID allowlist (WPM-aligned: script `P9302`, folios
`P1104`); project PID → public PID only through the schema ledger
ontology URI; item values accept Wikidata URIs / class URIs / explicit
`wikidata_property` claims — never a bare project QID. Manuscripts
still forbid P50. Ontology `owl:equivalentProperty` lines match the
runtime map. Tests: `test_hmo_wikidata_pq_mapper.py`,
`test_hmo_canonical_wikidata.py`.

### Rule W-103 — Wikidata upload target is curator-chosen; default dry-run (added 2026-07-25)

The production moratorium is no longer only an env flag. Wikidata Studio
exposes three **upload targets** (job param `upload_target`):

1. **`dry_run`** (default) — moratorium active; reconcile + validator preview only.
2. **`test`** — write to `test.wikidata.org` (bypasses production moratorium).
3. **`live`** — write to `wikidata.org` after an explicit UI confirm; sets
   `allow_live` on `WikidataUploader` (same effect as legacy
   `MORATORIUM_LIFTED=true` for that request).

Env `MORATORIUM_LIFTED` / `WIKIDATA_TEST_MODE` remain as legacy overrides when
callers only pass `dry_run=false`. Single-item push accepts `test|live`
(default `test`). Tests: `test_wikidata_upload_guards.py`
(`test_resolve_upload_mode_*`, `test_ui_live_target_bypasses_env_moratorium`);
`frontend/e2e/wikidata-upload-panel.spec.ts`.

### Rule W-114 — Related works MUST NOT mint evidence-less Wikidata CREATE items (added 2026-07-26)

Wikidata Studio AI verify failed before any judge call: refreshing the Studio
build hit `WORK_WITHOUT_SOURCE_EVIDENCE` on four local works
(`work:bible`, `work:ת_נ"ך`, `work:תיקון_חצות`, `work:הגדה_של_פסח`). The
manuscript `related_works` path called `_get_or_create_work` without
`work_candidate_evidence`, violating Rule W-68's source-aware work boundary
and the hard export quality gate.

Invariant:

1. `related_works` projection runs `assess_work_candidate` and retains rejected
   evidence on the manuscript for review.
2. P1574 to an existing Wikidata work is allowed only with a verified known QID
   (or an explicit curator-approved related-work row). Live-verified additions
   include Bible (`Q1845`), Tanakh (`Q83367`), Haggadah (`Q623354`), and
   Tikkun Chatzot (`Q2740944`).
3. Local CREATE work items from related works require accepted evidence stamped
   on the work item — never a bare title → `__LOCAL:work:…` mint.

Tests: `backend/tests/unit/test_wikidata_studio_works.py`
(`test_related_works_known_qid_links_without_local_work`,
`test_related_works_curator_approved_stamps_evidence_on_local_work`,
extended `test_dict_related_works_title`).

### Rule W-117 — Wikidata Studio emits only WPM public items; summarized HMO nodes roll up (added 2026-07-26)

A 1608-item canonical verify export showed 93% `fail` because HMO ontology
classes (`Codicological_Unit`, `PhilologicalView`, `EvidenceChain`, …) were
judged as Wikidata items. Canonical Studio already gated `direct_wikidata_item`
rows to `manuscript` / `person` / `work`, but `summarized_in_wikidata`
classes were skipped entirely — manuscripts only carried claims already on the
`F4` node, not Production/CU/Expression facts stored on related HMO entities.

Invariant:

1. **Public item types only:** Studio `entity_type` MUST be
   `manuscript` | `person` | `work`. Verify scope skips any other
   `entity_type` (`PUBLIC_WIKIDATA_ENTITY_TYPES`).
2. **Rollup, not separate items:** HMO classes with
   `projection_status=summarized_in_wikidata` fold allowlisted claims onto the
   parent manuscript (or work for `F27_Work_Creation`) when they share a MARC
   control number. Mapping runs through `hmo_wikidata_pq_mapper` restricted to
   each strategy's `wikidata_properties`.
3. **Wikibase bridge:** every manuscript with a live HMO QID carries `P2888` +
   `P973` to the project wiki item URL.
4. **Never leak project QIDs** as statement values; never emit `P50` on
   manuscripts.
5. Build summary exposes `rolled_up_entities` and `summarized_hmo_nodes`.
   Fingerprint salt: `hmo-wikidata-v4`.

Tests: `backend/tests/unit/test_hmo_canonical_wikidata.py`,
`backend/tests/unit/test_wikidata_verify_scope_cache.py`.

### Rule W-118 — Wikidata Studio read paths MUST filter HMO ontology rows from stale cache (added 2026-07-26)

Rule W-117 stopped **new** canonical builds from emitting HMO-class items, but a
1608-item verify export still showed 92.9% `fail` because **stale Postgres cache
rows** (pre-W-117) and the **merged read model** continued to surface
`Codicological_Unit`, `PhilologicalView`, `E21_Person`, etc. to verify, export,
and the review table. Verify scope filtered non-public types; export and
`fetch_merged_wikidata_items` did not.

Invariant:

1. **Single read gate:** `filter_public_wikidata_items` /
   `is_public_wikidata_studio_item` in `hmo_canonical_wikidata.py` — only
   `manuscript` | `person` | `work` reach verify, export, cached-verdicts, and
   the merged UI read model. HMO class names (`E21_Person`, `F4_…`, …) are
   never public even when they look “person-like”.
2. **Stale canonical cache rejection:** `studio_cache_has_non_public_items` —
   `execute_studio_build` refuses a canonical cache hit and rebuilds when any
   cached row has a non-public `entity_type`. Passive GET marks `cache_stale`
   so curators know to **Rebuild (skip cache)**.
3. **MARC join normalization:** the filter stamps `record_ids` from `records` /
   P3959 references so export and verify share the same control-number slice.

Tests: `test_wikidata_item_views.py`,
`test_wikidata_items_export_import.py`, `test_hmo_canonical_wikidata.py`
(filter/stale-shape cases).

### Rule W-119 — Wikidata Studio build jobs MUST NOT WDQS-reconcile the corpus (added 2026-07-27)

Incident: force-rebuild on run `48ba6c13` with canonical source left the job at
**0/1** for tens of minutes while the web dyno hammered `query.wikidata.org`
(`reconcile=True` inside `execute_studio_build`). Job polls hit **H12** (30 s
router timeout); asyncpg raised *cannot switch to state 12* when polls
overlapped the build's open session during blocking SPARQL.

Invariant:

1. **`wikidata_studio_build` jobs always pass `reconcile=False`** to
   `execute_studio_build` — same policy as verify scope materialisation
   (Rule W-116). Live WDQS reconcile belongs on upload, gated QuickStatements,
   and the `/reconcile` preview endpoint only.
2. **Canonical CPU build runs in `run_in_threadpool`** so validation /
   rollup assembly does not block the asyncio event loop on Heroku's single
   web dyno.

Tests: `backend/tests/unit/test_wikidata_studio_build_job.py`.

### Rule W-120 — Canonical Wikidata labels and work evidence MUST match legacy hygiene (added 2026-07-27)

Export (11) on run `48ba6c13` after W-117–W-119 still had **237/258** blocking
validation errors: Hebrew in `en` labels, MARC-inverted person names, Latin in
`he`, and every CREATE work missing `work_candidate_evidence`. The canonical
adapter had been copying live HMO labels verbatim and never stamping W-68
evidence — legacy `WikidataItemBuilder` already fixed these.

Invariant:

1. **`_wikidata_labels_and_aliases`** in `hmo_canonical_wikidata.py` applies
   legacy rules: manuscripts get shelfmark-based `en` (`Jerusalem, NLI, …`);
   persons use `_to_natural_name_order` with inverted form kept as alias;
   works route Hebrew→`he` and Latin→`en` only.
2. **CREATE works** must stamp accepted `work_candidate_evidence` via
   `assess_work_candidate` joined from MARC 505/500/related_works or approved
   authority; unevidenced CREATE works are dropped (existing QID may remain).
3. Fingerprint salt **`hmo-wikidata-v5`** so pre-hygiene Studio caches miss.

Tests: `backend/tests/unit/test_hmo_canonical_wikidata.py` (label + evidence cases).

### Rule W-121 — Canonical CREATE works MUST recover MARC 245 / known-QID evidence (added 2026-07-27)

After W-120, Studio dropped from ~258 → ~190 items because every CREATE work
lacked `work_candidate_evidence`. HMO always mints a main `F1_Work` from MARC
**245**, but the evidence join only looked at 505/500/related_works — so the
~68 main works were fail-closed away even though they are legitimate.

Invariant:

1. `assess_work_candidate` accepts `245` / `100/245` (reasons `marc_245_title` /
   `marc_title_author`) under the same quality filters as 505.
2. `_work_candidate_evidence_for` joins HMO work titles (labels, aliases,
   `has_title`, stripped `(MS …)` suffix) to prepared MARC title/contents/
   mentions/related_works and to sanitized authority name keys.
3. Exact `known_work_qid_for_title` hits set `existing_qid` + accepted evidence.
4. Placeholders, descriptive notes, Latin-without-authority, and unevidenced
   CREATE works stay dropped. Fingerprint salt: `hmo-wikidata-v6`.

Tests: `test_hmo_canonical_wikidata.py`, `test_wikidata_work_candidates.py`.

### Rule W-122 — Wikidata→Wikibase bridges MUST be browseable Item:Q URLs (added 2026-07-27)

Curators clicking Wikidata P2888/P973 (or Studio “Open on HMO Wikibase”) hit
dead links for two independent reasons:

1. **Ontology IRI as P2888.** Live HMO `hmo_source_uri` / exact-match claims
   carry `https://w3id.org/mhm/ontology#MS_<cn>`. The PQ mapper forwarded that
   string as public P2888. Clicking it follows w3id → GitHub raw, which for
   LFS-tracked TTLs returns a **Git LFS pointer**, not Turtle and not a
   Wikibase page. Export (11) had 21 manuscripts with a dual P2888
   (ontology IRI + Item:Q).
2. **Dead `/wiki/MS_<cn>` slug.** The planned Phase-3 redirect pages were
   never created on `mhm-hmo.wikibase.cloud` (404). `hmo_wikibase_page_url`
   must not invent that URL.

Invariant:

1. P2888/P973 emit only `https://mhm-hmo.wikibase.cloud/wiki/Item:Q…`
   (`is_browseable_hmo_wikibase_url` / `resolve_hmo_bridge_url`).
2. Ontology IRIs and MS_ slugs are rewritten to Item:Q when
   `project_item_qid` / `wikibase_id` is known, otherwise dropped.
3. Bridge attach strips non-browseable P2888/P973 before adding the Item:Q
   pair. No QID → no bridge (fail closed).
4. Fingerprint salt `hmo-wikidata-v8`. w3id ontology redirect must target
   `media.githubusercontent.com/media/…` (LFS blob), not
   `raw.githubusercontent.com` (pointer) — update
   `pipeline/docs/w3id/htaccess` and re-publish to perma-id/w3id.org.
5. Catalog workflow placeholders such as ``רשומה זמנית`` never become
   P1684; P1684 is manuscript-only (legacy Rule W-72 filter on the
   canonical mapper).

Tests: `test_property_mapping_hmo_links.py`, `test_item_builder_hmo_links.py`,
`test_hmo_wikidata_pq_mapper.py`, `test_hmo_canonical_wikidata.py`.

### Rule W-125 — Canonical Wikidata Studio MUST merge full MARC/authority enrichment (added 2026-07-27)

PhD proposal RQ2/RQ4 require research-grade public items (production, place,
language, works, agents, housing, themes, provenance). Export `(12)` on the
canonical Studio path emitted only identity shells
(`P3959`/`P31`/`P2888`/`P973` on manuscripts; `P214`/`P31`/`P2888` on persons;
`P1476`/`P31`/`P2888` on works) because HMO claim→QID mapping fail-closed on
project QIDs and did not re-run the legacy MARC builder.

Invariant:

1. **Canonical build merges legacy.** `execute_studio_build(source=canonical)`
   runs `build_items_for_run` (MARC + approved authority + NER) and
   `merge_legacy_into_canonical` keeps HMO `local_id` / bridges /
   `existing_qid` while unioning research claims (P571, P1071, P407, P1574,
   P217, P195, P11603, P127, P186, P1104, P9302, P136/P921, P953/P6108,
   work P50/P2093, person P8189/dates, …).
2. **Provenance events** (`record["provenance_events"]`) feed
   `_add_provenance_claims` (owners/dates + P7153 significant places).
3. **Person P106** occupation from role; **P1680** subtitle from MARC 245$b.
4. Fingerprint salt `hmo-wikidata-v9` includes the MARC/authority enrichment
   fingerprint so cache invalidates when either HMO or MARC inputs change.

**Curator ops:** Wikidata Studio **Rebuild (skip cache)** on canonical source,
then re-verify.

Tests: `test_wikidata_canonical_enrichment.py`,
`test_hmo_canonical_wikidata.py` (merge + fingerprint).

### Rule W-137 — Verify evidence must be complete and manuscript identity must be its own (added 2026-07-29)

Audit of the 86 judged items in export (13) of run `48ba6c13` (22 full /
43 partial / 21 fail — every non-full item a manuscript, all 19 judged
persons full) traced the verdicts to four build/evidence defects, not to
judge strictness. Each was confirmed against the run's actual
`run_records.marc` in Postgres.

**1. The judge saw almost no MARC.** `_marc_context` / `verify_evidence.marc`
carried exactly `title`, `authors`, `contributors`, `subjects` on all 67
manuscripts. This run (like every TSV/collapsed-key ingest) stores raw
`NNN$x` keys — `300$a`, `852$j`, `540$a`, `008`, `500$a` — plus only five
normalised keys, and `project_marc_slice` asked for normalised names only.
The judge then correctly reported "unsupported by MARC" for exactly the
claims whose source field it was never shown: P571 ×29, P1104 ×12,
P217 ×10, P6216 ×8, P1574, P1684, P1476, P1071.

- `marc_verify_context.RAW_TAG_FALLBACK` + `raw_tag_slice()` fill any slice
  name the normalised pass could not supply; normalised values always win, so
  a modern run is byte-identical to before. Mirrored in
  `eval-agent/eval_agent/ingest/marc_extract.py` so fixture and cache key agree.
- `wikidata_verify_evidence.build_claim_sources()` adds **per-claim
  provenance** (`verify_evidence.claim_sources`): PID → the source-field text
  that supports it, plus `supported: bool`. `CLAIM_SOURCE_SLICES` is the map.
- `build_statement_value_labels()` adds `verify_evidence.value_labels` —
  PID/QID/`__LOCAL:` glosses from the static desktop dictionary and the item's
  own local targets. No WDQS on the verify path (Rule W-116).

**2. Manuscript identity was contaminated across records (the only defect
that could reach public Wikidata).** `_index_manuscripts` /
`_match_manuscript` joined the legacy MARC item on *any* linked control
number, so several canonical manuscripts matched the same legacy item:
three items shipped as `The British Library, F 8298` with `P217 = F 7956`,
7 items had a label shelfmark contradicting their own P217, and 14 carried
2–5 `records`.

- `marc_verify_context.primary_control_number_for(...)` /
  `hmo_canonical_wikidata.identity_control_number(...)` — identity is the CN
  embedded in the entity's own `source_uri` / `local_id`; propagated CNs
  (Rule W-48) are context only.
- Manuscript `records`, labels, descriptions, shelfmark and the legacy join
  all use that CN (`identity_records_for`, `_marc_record_for_entity`,
  `_merged_records`, `_with_scoped_records`). Persons/works still span
  records (Rule W-63).
- One `P3959` per manuscript — its own. The per-CN loop emitted 4–5.
- **Hard export-quality gates** (`wikidata_export_quality_gate`):
  `MANUSCRIPT_SHARED_IDENTITY`, `LABEL_SHELFMARK_MISMATCH`,
  `MANUSCRIPT_MULTIPLE_CATALOG_IDS`, `MANUSCRIPT_MULTIPLE_SHELFMARKS`.
  Identity defects are build bugs, never curator decisions.

**3. Duplicate claims.** `_statement_key` included `value_type`, so the same
fact emitted as `string` and `external-id` survived twice.
`dedupe_statements()` keeps one statement per `(property, value)`, preferring
the instance with references/qualifiers, and runs on every path (merged,
canonical-only, legacy-only).

**4. Descriptions were catalog notes.** 17 English descriptions contained the
literal `MARC 336 content type: · MARC 337 media type: …` (RDA terms written
as `rdfs:comment` at `graph_builder.py:702`), 9 Hebrew descriptions were raw
MARC 500 notes, 26 carried the `Subjects include …` clause (26/26 non-full),
and 45 work descriptions had Hebrew in the `en` slot.

- Manuscripts: the **generated** form wins — `<language> manuscript, <date>,
  <script>, <material>, <holder>` (the form that passed) — plus a generated
  Hebrew counterpart (`_hebrew_manuscript_description`). Stored descriptions
  are rejected when `_is_catalog_note_description` matches.
- `_description_language_slot` routes Hebrew→`he` / Latin→`en` and drops a
  mixed-script `en` description so the generated form takes over.
- `_build_manuscript_description` no longer appends `Subjects include …`; a
  description disambiguates, it does not summarise.

**Also:** every serialised statement now carries `property` as well as
`property_id` — all 1847 exported statements had `property: null`, which
violates Rule W-62 and made this audit far harder than it should have been.

**Rubric alignment** (`eval-agent/config/rubrics/wikidata_item.md`): a PID in
`claim_sources` with evidence *is* supported; `value_labels` gloss item
values; generated descriptions are the house style, not "thin"; a sparse
catalog record is not a defect (never demand P195/P571/P217 that no channel
supplies); duplicate catalog ids / a second manuscript's shelfmark are real
failures.

**Cache invalidation:** `WIKIDATA_VERDICT_SCHEMA` → `w137_v1`, canonical
Studio fingerprint salt → `hmo-wikidata-v10`. Old verdicts miss automatically
(Rule W-51) — a rebuild + re-verify is sufficient, no `override_cache`.

**Curator ops after deploy:** Wikidata Studio **Rebuild (skip cache)** on the
canonical source, then **Verify with AI**. Do not re-upload the 14 formerly
multi-record manuscripts until their QID ledger rows are checked — their
identity changed, and a corrected item must not re-point an existing QID
(Rule W-99).

**Measuring it without a judge run:**
`backend/scripts/check_wikidata_export_quality.py <export.json>` now reports
`manuscript_shared_identity`, `manuscript_multiple_catalog_ids`,
`manuscript_multiple_source_records`, `label_shelfmark_mismatch`,
`description_is_catalog_note`, and `export_missing_property_ids`. Baseline on
export (13): 68 / 14 / 4 / 27 respectively.

Tests: `backend/tests/unit/test_wikidata_evidence_and_identity.py` (19),
`test_wikidata_description_hygiene.py` (14),
`eval-agent/tests/test_marc_extract_merge.py` (raw-tag projection).

**Follow-up (readiness fingerprint stability).** Auditing the red tests in this
area surfaced a real fragility in `hmo_canonical.canonical_entity_fingerprint`:
an absent container and an empty one hashed differently, so fingerprinting a raw
read-back snapshot and re-fingerprinting it after `normalize_live_entity`
disagreed — the canonical readiness gate (Rule W-94) then reported a stale
fingerprint for an unchanged item. Scalars/mappings/sequences are now coerced to
their empty form before hashing. Test:
`test_hmo_canonical_readiness_contract.py::test_fingerprint_is_insensitive_to_absent_versus_empty_containers`.

### Rule W-138 — MARC values reach the handlers unwrapped; every claim names its channel (added 2026-07-29)

Export (14) — the first run with persons and works in the judged pool (308
judged, up from 86) — scored 186 full/pass, 89 partial, 33 fail. Every W-137
defect class was 0, and **121 of the 122 non-full items** traced to seven
further defects, each confirmed against the run's `run_records.marc`.

**1. MARC quote wrappers reached the desktop field handlers.**
`prepare_record_for_pipeline` unwrapped *control* fields only, so `041$a`
arrived as `'"heb"'`. The 041 handler chunks language codes in three-character
slices → `['"he', 'b"']`, no valid code, **no `P407` on 48 manuscripts** (plus
20 that dropped a secondary language, because `_split_multi` split `"heb|lad"`
into `"heb` + `lad"`). The same wrappers degraded extent, shelfmark and the
RDA 33x terms (`content_types: ['']` is what produced the `MARC 336 content
type:` description text W-137 had to filter).
`_unwrap_marc_value` now strips balanced surrounding quotes per value **before**
multi-value splitting, preserving internal gershayim (`שד"ל`). Third occurrence
of this family — see W-54, W-87, W-111.

**2. Dimensions were misparsed, not merely missing.** `300$c: "9.5X14.5 cm"`
yielded `height_mm: 50` — the single-value `cm` branch matched `"5 cm"` out of
`14.5 cm` because decimals were unhandled. `_parse_dimensions` now handles
decimal/comma pairs with cm+mm units and refuses to start mid-number.

**3. Claims outside MARC had no provenance row at all.** W-137's
`claim_sources` only mapped MARC-backed PIDs, so `P2888` (246 items), `P214`
(122), `P973` (63), `P106`/`P8189`/`P1559` (25 each) and `P569`/`P570` reached
the judge with nothing to check — hence "unsupported" on nearly every person.
`build_claim_sources` is now channel-aware: `marc.<slice>`,
`authority.{viaf,mazal,authority_dates,authority_role,authority_names}`,
`hmo_wikibase` (including identifier claims whose authority row lives on the
live wiki item, gated at HMO creation per Rule W-95), `work_candidate_evidence`
/ `local_reference_targets`, and `structural: true` for `P31`/`P3959`, which
follow from the entity type. **Every emitted PID must resolve to a channel.**

**4. Person dates came from a fuzzy name join.**
`_authority_match_for_entity` fell back to `matches_by_name[label.casefold()]`,
so a scribe in a 1642 manuscript was described `(1786-1874)` — a homonym's
lifespan (26 items). Anything asserting biographical fact now passes
`identifier_only=True`; a name-keyed match may still supply the *role*, never
the dates (Rule W-67's exact-ID requirement, now enforced on the canonical
description path too).

**5. Works merged on a title key.** `_work_keys` indexed a work by every
`P1476` value, so items carrying several title forms merged unrelated works —
the Carpentras siddur inherited `סדר אליהו זוטא` (58 items had 2–3 titles).
Identity is now the QID, the local id, or the item's own label;
`canonical_work_titles` keeps the single title form that matches the work's own
label/aliases, and `WORK_MULTIPLE_TITLES` blocks the build.

**6. 82 works shipped the bare fallback description.**
`_work_description_with_attestation` adds century, an identifier-backed author
and the attesting manuscript's shelfmark — a description disambiguates.

**7. 38 `__LOCAL:` targets named items that were never built** (31 manuscripts).
Works dropped by the thin-title (W-98) or evidence (W-68/W-121) gates left the
referring `P1574` pointing at nothing, which pass 2 of the upload would leave as
a literal placeholder. `wikidata_local_refs.resolve_local_references` degrades a
contained-text link to `P1574 → Q234460` + `P1932` catalog title and drops any
other property, reporting counts in the build summary;
`DANGLING_LOCAL_REFERENCE` is a hard gate.

**Stragglers.** `is_too_generic_subject` keeps corpus-wide ethnonyms
(`Jews`, `Judaism`, `כתבי יד`, …) out of `P921` (Rule W-72, enforced at the
resolver). `P195` now resolves two further holders — `Q23308` British Library
and `Q82133` Bodleian Library, **both read live from the Wikidata API on
2026-07-29** per Rule W-26; `The Ben Zvi Institute` resolves to two plausible
entities (a publishing organization and Yad Yitzhak Ben-Zvi) and therefore
**abstains** (Rule W-84's reasoning).

**Cache invalidation:** `WIKIDATA_VERDICT_SCHEMA` → `w138_v1`, Studio salt →
`hmo-wikidata-v11`, `WIKIDATA_STUDIO_BUILD_SCHEMA` → `source-aware-works-v5`.

**Offline gate before any judge run.**
`backend/scripts/check_wikidata_export_quality.py` now reports
`blocking_counts` (must be 0) separately from `informational_counts`
(`work_missing_existing_qid` is expected for CREATE works). Baseline on export
(14): **589 blocking** — `claim_without_provenance_row` 269,
`work_boilerplate_description` 82, `work_multiple_p1476` 58,
`missing_p407_with_language_evidence` 48, `claim_without_evidence` 48,
`dangling_local_reference` 31, `person_dates_without_authority_evidence` 28,
`p407_drops_secondary_language` 20, `dimension_contradicts_extent` 5.

**Curator ops after deploy:** Wikidata Studio **Rebuild (skip cache)** on the
canonical source → run the checker on a fresh export → only when
`blocking_total` is 0, re-run **Verify with AI**.

Mirror caveat (Rule R5 / W-43 residual): `field_handlers.py`,
`marc_subject_resolve.py`, `item_builder.py`, `manuscript_projection.py` and
`property_mapping.py` were hand-edited in the web copy. Do **not** run
`sync_converter_to_web.sh` until the desktop repo carries the same changes.

Tests: `backend/tests/unit/test_wikidata_wave2_projection.py` (39).

**Follow-up on export (15)** — the first rebuild under this rule dropped the
offline gate from **589 → 32 blocking**: `claim_without_provenance_row` (269),
`dangling_local_reference` (31), `missing_p407_with_language_evidence` (48),
`p407_drops_secondary_language` (20) and `work_multiple_p1476` (58) all reached
**0**, and verdicts improved to 223 full/pass of 308 judged (from 186). The 32
residuals were two real defects and two checker bugs:

- **Unmapped PIDs (5).** `P1922` (first line), `P655` (translator), `P9046`
  (commentary by) and `P5816` (condition) had no `CLAIM_SOURCE_SLICES` entry, so
  they arrived `supported: false`. Added. The empty-channel fallback also
  mislabelled them `structural` — it now says `unmapped`, since "structural"
  asserts a claim needs no evidence and that is only true for `P31`/`P3959`.
- **Legacy work descriptions (21).** `_work_description_with_attestation` was
  applied on the canonical path only; `work_projection._get_or_create_work`
  still emitted the bare fallback. Both paths now name the attesting shelfmark.
- **Checker: dimensions.** The projection was correct (`9.5X14.5 ס"מ` → 95 ×
  145 mm); the checker compared millimetre claims against centimetre MARC text
  and flagged all 5. It now compares in both units with a 0.5 mm tolerance.
- **Checker: person dates.** It looked for `birth_year` in
  `work_candidate_evidence` instead of `authority_evidence`, so a properly
  identifier-backed `scribe (1632-1703)` (Mazal + VIAF, both present) was
  flagged. Fixed.

**Follow-up on export (16)** — the gate reached **0 blocking** and persons
improved sharply (full 89 → 126, partial 26 → 3, fail 18 → 6), but **works
regressed** (fail 7 → 19) because two W-138 changes of mine were wrong, and the
gate could not see either:

- **`952$a–d` are not shelfmark data in this corpus.** They hold rights, audit
  and export notes (`"Public domain; Contract"`, a cataloguer's name); the NLI
  shelfmark is **`090$a`** (`"F 3238"`). Mapping 952 into the `shelfmark` slice
  meant the judge saw rights text where it expected a shelfmark, so **all 105**
  works cited an attestation it could not verify. `shelfmark` is now
  `090$a / 099$a / 852$j / 852$h / 852$c`; `952$a–b` moved to `rights`.
- **A work's attestation must be verifiable.** The description now cites the
  **control number** (`attested in NLI record 990001499320205171`), which is in
  the item's own `record_ids` and `P3959` — not a shelfmark, which is not part of
  a work's evidence. This also removes the `attested in MS Ms. Heb. …` double-MS
  (8 items).
- **`P1476` provenance is entity-aware.** A work's title is evidenced by
  `marc.title` only when the work *is* the record's main title (`100/245`); for a
  505/500/RELATED-derived work, citing `marc.title` handed the judge the
  manuscript's *different* title as "evidence" — contradiction, not support.
  Derived works now cite `marc.contents` / `marc.notes` +
  `work_candidate_evidence`.
- **A known-QID work must not be minted as a local CREATE.** `הגדה של פסח` was
  accepted with reason `known_wikidata_work` yet shipped `existing_qid: None`,
  i.e. a duplicate of an item already on Wikidata.
  `_known_qid_from_work_evidence` recovers the QID from the accepting evidence
  row when the item's own (longer ISBD) label no longer matches exactly
  (Rule W-78 / W-114).
- **Title claims are sanitised like labels.** `P1476` shipped
  `"הגדה של פסח :" "מנהג אשכנז."` while the label carried the clean form —
  `_claim_value_for` routes `P1476`/`P1680`/`P1932` through
  `sanitize_work_title` (64 items).

New checker codes so neither class can hide again: `attestation_not_in_evidence`
(a description may only cite what the item's own evidence carries) and
`title_claim_has_quote_wrappers`. Both were **invisible** to the gate that
reported 0 on export (16); re-running the extended checker on that same export
reports **169 blocking** (105 + 64), which is the honest number.

### Rule W-139 — AI verify asks Wikidata whether the item already exists (added 2026-07-30)

The upload path has been fail-closed on duplicates since Rule W-30, but nothing
earlier in the flow said so: an item could be built, judged, approved by a
curator and only *then* discovered to duplicate a live Wikidata item. Verify now
probes for an existing item and hands the answer to the judge as evidence.

Measured on run `48ba6c13` export (16): **12 of 206 probeable CREATE candidates
already exist on Wikidata** — Yom-Tov Lipmann Heller (`Q66439`), Samuel ibn
Naghrillah (`Q467161`), Abraham Firkovich (`Q2390259`), Yihye Bashiri
(`Q22935567`), … and one manuscript already imported as `Q134603946`
("KTIV990001343040205171"). Creating those would have been 12 duplicates.

**Action API, never WDQS.** Rules W-116 / W-119 forbid live SPARQL here — it
produced 429s and read timeouts that hung whole jobs. CirrusSearch answers the
same question over the light Action API:
`action=query&list=search&srsearch=haswbstatement:P8189=987007262418105171`.

- **Identity, not similarity.** Probes are by identifier only — `P3959` for
  manuscripts, `P214`/`P8189`/`P244`/`P227` for persons. Label similarity is
  never treated as duplication: homonymous scribes and works are normal
  (Rules W-37 / W-84).
- **Batched + throttled, because unthrottled probing 429s.** Measured: 50 of 60
  single probes failed. `probe_batch` pipes up to 20 `PID=value` pairs into one
  query (`haswbstatement:P214=A|P8189=B`), each hit is attributed by reading that
  item's claims, requests share a ≥1.1 s interval and honour `Retry-After` and
  `maxlag`. Full 313-item corpus: 206 probes in ~100 s, and answers are cached
  content-addressed (`kind="wikidata.duplicate_probe"`, Rule W-25) so repeats are
  free.
- **Fail closed, never fail silent.** `unavailable` / `skipped` / `not_run` mean
  *unknown*, never "safe to create". The 104 works have no identifier to probe and
  are reported `skipped` with that reason stated — a silent `absent` there would
  be a lie (Rule W-110's no-silent-caps reasoning).
- **Budget + kill switch.** `WIKIDATA_DUPLICATE_PROBE=0` disables;
  `WIKIDATA_DUPLICATE_PROBE_MAX` (400), `_INTERVAL` (1.1 s) and `_TIMEOUT` (8 s)
  bound it. Runs in the verify **worker** during scope preparation, never in a
  request handler (Rule W-59).
- **Rubric.** `candidates_found` on a CREATE item ⇒ `type_ok="no"`,
  `overall="fail"`, naming the QID and matched identifier so the curator links
  instead of creating. An identifier match is an identity match even when labels
  differ. `unavailable`/`skipped` is a caveat in `reasoning`, not a fail.
- **Offline gate.** New checker code `create_would_duplicate_existing_item`,
  which reports the matched QID in the finding row.

**Correction (2026-07-31).** `attach_duplicate_evidence` uses the *batched*
path, which never called `probe_item` and therefore never touched the cache — so
the probe re-spent ~100 s of HTTP on every verify run despite Rule W-51. It now
caches per `(pid, value)` identifier, so a lookup is shared across every item
using it and free on a re-run; **absences are cached too** (that is the common
case) under a 7-day TTL, short enough that a newly created Wikidata item is not
missed for long. Cache reads and writes each take their own short transaction —
the HTTP never runs inside one (Rule W-40).

Works remain unprobeable by identifier — an honest gap, not a solved problem.
Closing it needs a curator-reviewed label+author search, which is a weak signal
and must not auto-block (Rule W-84).

Tests: `backend/tests/unit/test_wikidata_duplicate_probe.py` (13).

### Rule W-140 — Manuscript metadata MUST be recovered from MARC before it is generated (added 2026-07-30)

Export (17) reached **0 blocking** offline findings and 240/280 clean verdicts,
so the residual quality gap was breadth, not correctness. A head-to-head
against `Q134603946` — an NLI manuscript hand-created on Wikidata in 2025 by
editor `Lapon-Kandelshein Essi` — showed the shape of it: **23 statements to
our 15**. They carried extent, material, location, subject, collection and the
NLI link; we carried shelfmark, script, copyright and channel provenance on 13
of 15 claims against 0 references on 22 of their 23.

Measured across our 68 manuscripts, most of that gap was **data we already had
and dropped**, not data needing generation:

| MARC slot | had data | emitted | cause |
|---|---|---|---|
| `856$u` → `P953` | 63 | 1 | collapsed-key ingest never derived `digital_url` |
| `300$a` → `P1104` | 62 | 45 | parser took the first number adjacent to a folio unit |
| `340$a` → `P186` | 23 | 2 | exact-match vocabulary missed materials named in prose |
| `561$a` → owner/place | 37 | 0 | genuinely unstructured Hebrew narrative |

Invariant:

1. **Recover before generating.** A field present in MARC is projected by
   deterministic code. An LLM is only for prose no parser can reach.
2. **The extent parser fails closed and sums honestly.**
   `converter/transformer/extent.py` is the single implementation (the desktop
   `FieldHandlers._parse_extent` delegates to it). It sums every leaf sequence
   (`111, [2] דף` = 113; `3 כרכים (300, 207, 110 דף)` = 617 in 3 volumes),
   reads page and Hebrew-numeral (gematria) units, ignores parenthetical
   foliation notes, and returns **None** for a unit it cannot name (`עמודות`),
   a folio *reference* (`דף 2א-2ב`) or a bare number. A unit word is never read
   as a numeral — `דף` is gematria 84. The resolved unit is stamped as
   `extent_unit` so the projection never re-sniffs the string.
3. **`url`-typed claims must be URLs.** `856$u` arrives quote-wrapped and
   sometimes holds prose, so `P953` is gated on an `http(s)` scheme at the emit
   site, not only at ingest.
4. **Material stays a closed vocabulary.** `materials_in_text` matches
   `MATERIAL_TO_QID` terms as whole words (allowing one Hebrew prefix letter,
   `ופפירוס`) and suppresses negations. 340$a in this corpus is a free-text note
   about the copy — autographs, binding, multiple hands — and
   `בכתיבות אחדות` ("in several hands") is **not** a material.
5. **A generated description may not contradict its own item.** The `he`
   manuscript description takes its language word from the record via
   `_LANG_CODE_TO_HEBREW`; a hardcoded `עברי` had 10 Arabic/Italian manuscripts
   contradicting their English description. Israeli holders render in Hebrew
   from `_HEBREW_INSTITUTION_NAMES`; foreign institutions keep their own name
   rather than have us invent a Hebrew form.
6. **LLM extraction is span-grounded, closed and advisory.**
   `marc_llm_extract.py` sends the 500/541/561/563/583 prose to a tier-1 model
   (DeepSeek V4 Flash on Qubrid by default, `MARC_LLM_EXTRACT_MODEL`) and keeps
   a proposal **only** when its quoted `span` appears verbatim in the record and
   its property is one of the already-audited constants `P127` / `P1071` /
   `P186` — the extractor never introduces a new P/Q (Rule W-26). Material
   values must resolve through `MATERIAL_TO_QID`. Results are content-addressed
   in the inference cache (Rule W-51), bounded by `MARC_LLM_EXTRACT_MAX`, and
   disabled by `MARC_LLM_EXTRACT=0`. The extractor takes a session **factory**
   and never holds a transaction across a model call (Rule W-40). It runs in the
   **build job** as the "mining provenance prose" phase — never on the verify
   scope path, where one call per manuscript hung "Loading Studio scope…" for
   minutes. The corpus cache is read in one bulk query, only misses reach the
   model, and those overlap under `MARC_LLM_EXTRACT_CONCURRENCY` (default 6).
   **The mining phase MUST load the MARC prose itself.** Built Studio items name
   their records as `record_ids`/`records` and carry no MARC — that is attached
   only on the verify path. Export (19) proved the cost of assuming otherwise:
   68 of 68 manuscripts had zero readable prose, the phase was a 0.4 s no-op,
   nothing was persisted, and every item reported `not_run`. The phase scopes
   `load_run_marc_records_scoped` to the manuscripts in the build, projects only
   the three `SOURCE_SLICES`, and pops `_marc_context` before persisting so the
   cache row keeps its Basic-dyno budget (Rule W-131).
   **The prompt MUST name what each property means.** `marc_llm_extract_v1` sent
   bare PIDs; the model then routed languages (`לאדינו`, `ערבית`, `לטיני`) and
   holding libraries into `P1071` (location of creation). `v2` renders
   `SUPPORTED_PROPERTIES` with its descriptions and states that a language,
   script or holding library is never a place of creation. A prompt-semantics
   change bumps `EXTRACT_SCHEMA` so old-semantics cache rows are not served.
7. **Proposals are never claims.** They surface as
   `verify_evidence.llm_proposals` for the curator and the judge, and nothing is
   projected into `statements`. Generation is not an evidence channel
   (Rules W-72 / W-67 / W-138), so the rubric forbids a proposal from changing
   any verdict axis, and `unavailable` / `no_source` describes the extractor,
   never the item.

**Root cause of the empty verdict column (2026-07-31).** The `llm_proposals`
follow-up below was the *second* instance of one defect; the first arrived with
Rule W-139. `verify_evidence.wikidata_existing.duplicate_check` is produced by a
live Wikidata probe that only the **verify** worker runs, so verify hashed
`absent` / `candidates_found` while the review table hashed `not_run` — every
verdict read as stale and the AI-verdict column showed `— —` for all 313 rows.

**Invariant: the verdict fingerprint may contain only state the READ path can
reproduce.** It answers "is this the same item state the judge saw?", so a
channel that exists solely because the verify worker did external I/O cannot be
part of it. `duplicate_check` is excluded via
`_EVIDENCE_SUBKEYS_OUTSIDE_FINGERPRINT` (the subkey only — `existing_qid` and the
authority rows in the same channel still key the verdict, so the exclusion stays
surgical); its own 7-day cache TTL governs freshness instead. Verdicts written
under the old key cannot be recovered by fingerprint arithmetic — the read path
has no probe result to reproduce — so they need one more verify run to be
re-keyed. Tests:
`test_wikidata_verdict_cache.py::TestPathDependentEvidenceNeverKeysAVerdict`.

**Follow-up (verdicts vanished from the table, 2026-07-31).** Adding
`llm_proposals` to the evidence pack silently put it inside the verdict cache key
(`fingerprint_verify_evidence` dropped only `marc`), so the AI-verdict column
went empty for every row: verify hashed `status: not_run` while the read path
hashed the `ok` pack the build had mined. Advisory evidence MUST NOT key a
verdict — the rubric already forbids a proposal from moving any verdict axis, so
including it in the key was wrong by construction. `llm_proposals` now joins
`marc` in `_EVIDENCE_KEYS_OUTSIDE_FINGERPRINT`, and
`sanitise_stale_wikidata_verdict` recognises the pre-fix key shape and rewrites
it forward so verdicts already written are recovered instead of lost. Whenever a
channel is added to the pack, decide explicitly whether it belongs in the key.
Tests: `test_wikidata_verdict_cache.py::TestAdvisoryEvidenceNeverKeysAVerdict`.

Tests: `backend/tests/unit/test_marc_extent_and_digital_access.py` (27),
`test_marc_llm_extract.py` (20).

### Rule W-142 — Projection must not lose what the build already knows (added 2026-07-31)

Export (18) reached 0 blocking findings and 243/280 clean verdicts, and auditing
the residue found four places where the build **had** the right answer and threw
it away. Each is a lost-information bug, not a knowledge gap:

1. **Contained works were replaced by a placeholder.** 42 `P1574` claims
   degraded to generic `Q234460` ("text") — but **32 of them named a work that
   was in the same build** under a different local id. `resolve_local_references`
   now indexes emitted works by every title they answer to (labels plus their
   `P1476` claim) and relinks the `__LOCAL:` target before degrading. Note the
   two title properties are NOT interchangeable: `P1932` ("stated as") carries
   the catalog wording on the referring claim, `P1476` is the work's own title —
   conflating them made the index empty and every relink miss. A relink to the
   referring item itself is refused, so the circular `P1574` the judge flagged
   cannot be created.
2. **The catalogue URL was dropped by the merge.** `P973` legitimately holds
   both the HMO bridge and the catalogue's own record URL (MARC `856$u`), but
   `_CANONICAL_PREFERRED_PIDS` suppressed the legacy statement because that
   *property* was already present — so 0 of 68 manuscripts carried their
   catalogue link. For `_MULTI_VALUE_CANONICAL_PIDS` only an identical **value**
   is a duplicate. `P31` must stay strict: merging legacy types back in
   reintroduces the discouraged classes Rule W-98 forbids.
3. **A printed facsimile stayed typed as a manuscript.** Only the legacy
   projection reads `500$a` `דפוס צלום`, and `P31` is canonical-preferred, so
   `Q87167` overwrote the correct `Q571`. The merge now carries
   `semantic_type="printed_facsimile"` across and swaps the type, and the `he`
   description says `מהדורת פקסימיליה מודפסת` rather than `כתב יד`.
4. **The `he` label hardcoded holder and language.** It emitted
   `כתב יד עברי, ספרייה לאומית, <shelfmark>` verbatim, so an Israel Museum
   manuscript announced the National Library as its holder (Rule W-82). It now
   takes the language from the record and the holder from
   `_HEBREW_INSTITUTION_NAMES`, keeping a foreign institution's own name rather
   than inventing a Hebrew form.
5. **LLM proposals were mined but never persisted.** `_mine_provenance_prose`
   mutated `cached.result_items` *after* `execute_studio_build` had already
   written the cache row, so all 313 items exported `llm_proposals: not_run`
   while the extractor had genuinely run. The build job now writes the enriched
   items back onto the durable cache row.

**A judge verdict is a hypothesis, not a fact.** The same audit had the judge
assert that MARC `tat` should map to `Q4927` rather than our `Q25285`. Live
lookup (Rule W-26): `Q25285` **is** Tatar; `Q4927` is
*"Category:History of Dominica"*. Acting on that verdict would have reproduced
the Palme d'Or incident. Verify any P/Q a verdict recommends before changing a
mapping.

Tests: `backend/tests/unit/test_wikidata_projection_recovery.py` (11).

### Rule W-143 — Holding institutions resolve through one audited table (added 2026-08-02)

`P195` was resolved by a hand-rolled `if`-chain covering four institutions, so 53
of 68 manuscripts carried an inventory number with no holder QID beside it — even
though their labels named a holder, across **22 distinct institutions**.

1. **One table, live-verified.** `converter/wikidata/holding_institutions.py` maps
   name variants → QID with **the English label recorded beside each QID as
   fetched** (Rule W-26). The 2026-08-02 batch resolved 18 institutions via
   `wbsearchentities` + `wbgetentities`.
2. **Abstention is explicit, not an omission.** `ABSTAINED_INSTITUTIONS` names
   each unresolvable holder *and why*: Ben Zvi resolves to two plausible entities
   (`Q3571277` Yad Yitzhak Ben-Zvi vs `Q99770351` Ben Zvi Institute), and the
   Montefiore Library and Lehmann Foundation have no Wikidata item. They emit no
   `P195` and never fall back to NLI (Rule W-75).
3. **Verified evidence beats an external payload.** When the table and an
   authority row's `wikidata_qid` disagree, the table wins — it was audited; the
   payload's provenance is unknown.

The table is not only a label: it is half of the Rule W-144 duplicate key.

### Rule W-144 — `absent` MUST mean every key was probed (added 2026-08-02)

Manuscripts were probed on `P3959` alone. Live check of all 33 Samaritan
manuscripts on Wikidata: **33 of 33 carry no `P3959`**, yet each is identifiable
by holder + shelfmark — `CAJS Rar Ms 75-117` (Penn `Q49117`), `MS. Bodley Or. 699`
(Bodleian `Q82133`). The one probe we ran could not see the imports most likely to
collide with us.

1. **Second key: `P195` + `P217`, as a conjunction.** `haswbstatement` joins with
   `|` as **OR**, so an AND is space-separated and cannot be batched — one request
   per item, inside the same probe budget.
2. **Fail closed on both sides.** No `P195` (an abstained holder) or two
   shelfmarks ⇒ no composite probe. A one-sided lookup is never substituted.
3. **`absent` requires every key to have answered.** A probe that failed or never
   ran downgrades the item to `skipped` with the reason. Reporting `absent` off a
   partial probe is the precise false negative this rule exists to prevent.
4. **Unbatchable keys MUST be bounded.** Identifier probes pack 50 to a request;
   a conjunction or a title cannot be batched at all, so on the reference corpus
   they added ~173 sequential CirrusSearch calls. Search is the expensive
   endpoint: the 2026-08-02 run earned `429 Too Many Requests` and, with four
   polite retries each, left verify on "Loading Studio scope…" for tens of
   minutes. `WIKIDATA_DUPLICATE_PROBE_UNBATCHED_MAX` (default 40) caps them and
   `..._TRIP` (default 3) consecutive failures stops probing for the rest of the
   job. Everything dropped reports `skipped` and is logged — never a silent cap
   (Rule W-110).
5. **The answer MUST be visible.** Export (19) said `duplicate_check: not_run` on
   all 313 items while **207 answers sat in the cache** — `_wikidata_existence`
   lives in the verify process's memory and is outside the verdict fingerprint
   (Rule W-136), so no read path showed it. `attach_cached_duplicate_evidence`
   stamps the cached answer on read paths, **cache-only**: opening an export must
   never become external I/O. No cache entry reads as `not_probed`, never
   `absent`.

**Amended 2026-08-05 (Rules W-159 / W-160).** Composite and title keys ARE
batchable: ``probe_titles_batch`` groups by class and ORs ``inlabel:`` clauses,
``probe_composites_batch`` ORs the selective PID and then enforces the FULL
conjunction client-side against each hit's claims — the batched query is a
deliberate superset and the AND is never substituted by a one-sided lookup. The
unbatchable budget now bounds only the residue (groups that errored), the residue
is interleaved across probe classes by ``order_unbatchable_fairly`` so the cap
cannot land on one class, and a capped key persists a ``deferred`` marker that a
later job retries. The duplicate answer has exactly one writer.

### Rule W-145 — Works MUST be probed for duplicates (added 2026-08-02)

Works had **no duplicate check at all** — `_IDENTIFIER_PIDS_BY_TYPE["work"]` was
empty, and 0 of 105 works carry any identifier. The collisions here are the most
certain in the corpus: the judge already caught us proposing a new item against
`Q623354`, the Passover Haggadah.

1. **Probe `inlabel:"<title>" haswbstatement:P31=<class>`**, one request, only
   when exactly one title and exactly one class are present.
2. **A title is a likeness, not an identity.** Hits carry
   `requires_curator_confirmation` and `matched_on: title~…`. Nothing downstream
   may auto-match, auto-adopt, or suppress a CREATE on this evidence alone.
3. **A manuscript never gets a title probe.** It has real identifiers; a
   likeness must not dilute them.

**Amended 2026-08-05 (Rule W-145 follow-up).** Works are batched by class and
interleaved with the other probe classes, so works are never the class the budget
starves. Before this, ``unbatchable`` was ``sorted(pending)`` and ``"P195+P217"``
sorts before ``"title+P31"`` — every manuscript composite key was issued before any
work title key, and 29 works reported ``not_probed`` with their keys already
computed.

### Rule W-146 — Created items MUST be reachable from one another (added 2026-08-02)

LOD is the point of the project, and the graph was one-directional: 92 internal
edges, **91 of them originating from a manuscript**, with **124 of 140 person
items having nothing pointing at them**.

The cause was not notability — **122 of 140 persons carry `P214` (VIAF)** and are
perfectly citable. `ROLE_TO_PID` simply had **no entry** for `former owner` (49
approved rows on the reference run), `mentioned` (27) or `signatory` (13). Each was
dropped with a log line, and `former owner` was additionally short-circuited
before the lookup.

1. **Every approved person role either maps to a property or is refused for a
   stated reason.** `seller` (the data model forbids modelling auction houses as
   owners) and `censor` remain refused. Silence is not a decision.
2. **A former owner is `P3342` (significant person), not `P127`.** Verified live
   2026-08-02: `P3342` is *"person linked to the item in any possible way"*, which
   is unambiguously true; `P127` is *"owner of the subject"* and, unqualified,
   asserts **current** ownership. The data model does describe `P127` as the
   ownership chain distinguished by `P580`/`P582` — upgrading to it requires date
   evidence from `561$a` prose that we do not yet parse.
3. **Never create an item for the sake of an edge.**
   `person_projection` refuses a person with no external identifier, citing
   **Wikidata:Notability**. Of 21 approved work-author headings, **0 carry a VIAF
   or Mazal id**, so those works keep `P2093` — with no item to link to, the name
   string is exactly what Wikidata's guidance prescribes. `P2093` is a defect
   **only** when a person item for that author does exist.
4. **The canonical context MUST stamp `marc_authority_matches` onto its records,
   in the desktop shape.** It indexed the matches for its own lookups and never
   wrote them back, so `_find_work_author_match` had nothing to read. The shape
   matters: a raw DB row has `entity_text`/`viaf_id`, while the builder reads
   `name`/`viaf_uri` — passing the raw row makes every person fail the notability
   check for want of an identifier that was in fact present.

### Rule W-153 — Wikidata Studio cache rows MUST not preserve identifierless persons (added 2026-08-03)

Run `48ba6c13-115c-4763-bff1-c08b9031b518` displayed an `Aaron` person with
local ID `mazal:987007257211705171` and a `NO_IDENTIFIER` ERROR. The current
legacy builder, given that identifier, emits both P214 and P8189 and validates
cleanly; the row was an older Postgres Studio cache payload, not a fresh
authority projection. The build fingerprint had no code/schema version that
invalidated this pre-notability row, so a normal page load kept it reviewable.

The Studio build schema is now `source-aware-works-v5`. In addition,
`studio_cache_has_stale_validation` rejects any cached person carrying an
ERROR-level `NO_IDENTIFIER`: cache-hit paths treat it as stale, GET exposes
`cache_stale=true`, and the curator must force-rebuild before review or upload.
The cache rule applies to both legacy and canonical source rows; the upload
validator remains the final write-path gate.

### Rule W-154 — Canonical Wikidata Studio merges MUST omit identifierless persons (added 2026-08-03)

The production rebuild of run `48ba6c13-115c-4763-bff1-c08b9031b518` ran
successfully after W-153, but the canonical result still logged
`NO_IDENTIFIER`. The legacy builder correctly skipped many identifierless
persons; canonical enrichment then allowed an identifierless canonical or
unmatched legacy person to survive the final merge and cache write.

`merge_legacy_into_canonical` and the final canonical assembler now apply the
same publishability boundary after matching, overrides, and reconciliation,
before cache persistence: a person must have an existing QID or a non-empty
P214/P8189/P244/P227/P213/P268 statement. Otherwise it is omitted, so the
Studio corpus cannot contain a person that the final validator marks
`NO_IDENTIFIER` (R70).

### Rule W-155 — Canonical Wikidata Studio finalization MUST be post-filter and source-scoped (added 2026-08-04)

The first W-154 rebuild removed the identifierless row but still exposed
non-full AI verdicts caused by claims that were finalized in the wrong order or
with the wrong evidence scope. The canonical assembler now:

1. applies curator overrides before final filtering;
2. omits people whose exact authority match is hard-rejected or chronologically
   impossible for every source record, and preserves those hard-reject flags so
   legacy enrichment cannot restore their dates;
3. removes broad `P921` subjects and `P195` values lacking a verified current
   holder in the manuscript's own MARC record;
4. resolves `__LOCAL:` references only after the public item set is final; and
5. stamps the source MARC control number into accepted work evidence.

This keeps the cached Studio corpus, the verifier evidence, and the eventual
QuickStatements export on the same fail-closed public projection. *Why:* the
production audit found unsupported local edges, broad `Jews` subject claims,
holder mismatches, authority dates from the wrong person, and work evidence
whose source record was not visible to the judge.

### Rule W-159 — The duplicate answer has exactly one writer (added 2026-08-05)

Export (23) reported `verify_evidence.wikidata_existing.duplicate_check.status`
as `not_run` on **all 343** items while 314 answers sat at the top level of the
same rows. `export_wikidata_items` built the evidence pack inside
`fetch_merged_wikidata_items` and only stamped `_wikidata_existence` afterwards,
so `build_verify_evidence_pack` always took its `not_run` fallback and nothing ever
republished it. Two surfaces, two writers, two orders — the third such incident
after export (19).

Invariant:

1. **`stamp_duplicate_check` is the only writer.** It puts the *same object* on
   `item["_wikidata_existence"]` and inside the evidence pack, so the two cannot
   drift and the order cannot matter. Called at the tail of both
   `attach_duplicate_evidence` and `attach_cached_duplicate_evidence`, and after
   every `enrich_items_with_verify_evidence` — rebuilding the pack would otherwise
   discard an answer stamped earlier.
2. **Read the answer through `duplicate_status_for_item`.** It checks the live
   result, then the pack, then `_duplicate_status` — which is all a persist-slim
   item keeps, because `fingerprint_verify_evidence` strips the pack's copy.
3. **The export derives the top-level field from the pack**, never the reverse.

### Rule W-160 — A capped probe MUST be distinguishable from an unattempted one (added 2026-08-05)

29 work items reported `duplicate_check.status = "not_probed"` with the note "no
probe has run for this item yet" — while their probe keys were sitting right there
in the same object, already computed. The keys had been dropped by the unbatchable
budget, and a dropped key left **no cache row at all**, which on the read path is
indistinguishable from a key nothing ever looked at.

Invariant:

1. **A capped key persists a `deferred` cache row** carrying the reason. The
   export reads it as `skipped` with `reason: "capped"` and a note naming the cap,
   never as `not_probed` and never as `absent`.
2. **A deferred row is a MISS for the probe.** `attach_duplicate_evidence` retries
   it, because treating it as an answer would let the 7-day cache TTL freeze the
   cap in place for a week.
3. **The offline checker separates them** — `work_create_probe_capped` (coverage
   gap the next run closes, informational) vs
   `work_create_without_duplicate_probe` (never attempted).

### Rule W-161 — A holder name is either audited or the build fails (added 2026-08-05)

Amends Rules W-75 / W-82 / W-143.

Incident: 11 of 68 manuscripts shipped labelled `Jerusalem, NLI, F …` while MARC
710 named Braginsky, a private collector, or nothing at all. Two faults compounded:
`_holding_institution_name` gated acceptance on a substring keyword list that
rejected "Braginsky Collection" — "collection" lives in
`_PERSON_NAME_QUALIFIER_WORDS`, not `_INSTITUTIONAL_KEYWORDS` — and both label
builders filled the gap with a hardcoded `holding or "Jerusalem, NLI"`.

A bare `None` from `institution_qid` conflated three different facts: verified,
reviewed-and-unlinkable, and never-audited. Three real institutions sat in that
third bucket.

Invariant:

1. **`resolve_holder` returns a status, not a `None`** — `resolved` / `abstained` /
   `unknown` / `placeholder`, each with a reason. `unknown` is a build error;
   `abstained` is a reviewed decision and only informational.
2. **Label precedence, and it NEVER ends at NLI.** Verified table label, else the
   record's own attested 710 string (attestation, not fabrication), else the
   shelfmark alone. The no-shelfmark fallback is
   `Hebrew manuscript, NLI record <cn>` — a catalogue designation, not an
   ownership claim.
3. **One extractor.** `holder_names_from_record` feeds both the label and P195, so
   the name that keys one cannot differ from the name that keys the other.
4. **The gate blocks `UNAUDITED_HOLDER`, `FABRICATED_HOLDER` and
   `HOLDER_QID_UNVERIFIED`.** None of them is a curator decision (Rule W-137);
   each clears by verifying a QID live and adding a table row, or by recording an
   abstention with its reason.
5. `scripts/audit_holding_institutions.py` reports the buckets for a run or an
   export. **`unknown` MUST be empty.**

### Rule W-162 — Every PID a projection can emit has a channel row (added 2026-08-05)

Amends Rules W-137 / W-138.

21 statements reached the judge as `channels: ["unmapped"], supported: false` —
P3342 on 16 items, P1891 on 5. Rule W-146 had added both to `ROLE_TO_PID` and to
the person linker but not to any claim-source table, and the rubric tells the judge
that a claim it cannot trace is unsupported. A missing *mapping* read as missing
*data*, for three months.

Invariant:

1. **Role-derived person links have their own channel.**
   `_PERSON_LINK_CLAIM_PIDS` + `_person_link_evidence` invert `ROLE_TO_PID` and
   quote the relator row that produced the edge — the same rows the linker read.
2. **The map is exhaustive and self-checking.** `projectable_property_ids()` is
   *computed* from the `P_*` constants and `ROLE_TO_PID`, and
   `unmapped_projectable_pids()` MUST be empty. A hand-listed set is what went
   stale.
3. **The exclusions are reviewed data, not a test literal.**
   `QUALIFIER_ONLY_PIDS` / `REFERENCE_ONLY_PIDS` in `property_mapping.py` name the
   PIDs that only ever appear inside a statement. Anything not there earns a
   channel row.
4. **`support_status` names the cause.** `no_channel_mapped` is OUR build defect
   and blocks; `channel_empty` is catalogue sparsity and is informational —
   blocking on it would make a thin but valid record unbuildable. `supported`
   stays for compatibility; read the status.

### Rule W-163 — The export-quality gate runs in the build path, on both sources (added 2026-08-05)

`assert_wikidata_export_quality` had exactly one non-test caller, inside the
**legacy** branch of `execute_studio_build`. The `source == "canonical"` branch
upserted the Studio cache and returned without ever calling it — so every defect in
this rule file's neighbours shipped through an ungated path.

Invariant:

1. **Both branches gate before `_upsert_studio_cache`**, and the canonical build
   requests `return_native=True` so the gate has something to check.
2. **Two severities, and the distinction is load-bearing.** Blocking findings are
   build bugs with no curator override (Rule W-137); informational findings are
   reviewed decisions or sparse records and MUST never block.
3. **The evidence-level checks are pure.** They rebuild the pack in-process from
   the serialised items plus MARC; `_wikidata_existence` and `_llm_proposals` are
   absent at build time, so no verify-time fact is consulted.

### Rule W-164 — A manuscript's label is a designation and its inception is the audited year (added 2026-08-05)

Amends Rules W-80 / W-140 / W-82.

Three defects in what a manuscript asserts about itself:

* **64 of 68** items carried the MARC 245 in `labels.he` — the title of a text the
  manuscript *contains*, not a designation for the physical carrier.
* A P195 on Q1256981 rendered `value_label: null` while the verified label sat in
  `holding_institutions` one module away.
* Two items asserted P571 = **1501** while their own MARC 260 $c read
  `מאה ט"ז-י"ז, לפני תל"ו (1676)`.

Invariant:

1. **With a shelfmark or control number present, `labels.he` is the designation**
   and the Hebrew 245 becomes an alias. A title becomes the label only when there
   is nothing better.
2. **Every projected QID carries a gloss** (Rule W-80). `qid_label` defers to
   `institution_label` rather than duplicating the institution table, and the gate
   blocks `MISSING_VALUE_LABEL`.
3. **`manuscript_production_year` is the only P571 source.** It lives in
   `converter/transformer/production_year.py` so the projection can reach it; a
   second copy under `app/pipeline` is the drift Rule W-142 punishes. A
   colophon-attested year NARROWS a century-precision date and never widens or
   invents one; the century stays as P1319/P1326 and the year is referenced
   through P887.
4. **A century RANGE is parsed before a single century.** `_parse_hebrew_century`
   matches the first century in a range, so `מאה ט"ז-י"ז` yielded 1501–1600 and
   silently discarded half the range.
5. **Corollary — a P/Q constant is verified live or it is not written** (Rule
   W-26). The audit behind point 2 found that **all 14** Talmud tractate QIDs and
   **10 of 13** Bible book QIDs were wrong, and both tables are emitted as P921 on
   public items: Shabbat pointed at a species of arachnid, Bava Batra at the
   central bank of Brazil, Jeremiah at "Tom and Jerry". A wrong QID looks exactly
   like a verified one, which is why the gloss must be recorded beside it — the
   wrong gloss on Q9190 ("Jews" for what is actually Exodus) had also put it in
   `_BROAD_MAIN_SUBJECT_QIDS`, dropping a specific biblical subject in the belief
   it was dropping a generic one.

### Rule W-165 — A work item is attested from exactly one record (added 2026-08-05)

The work counterpart of Rule W-137's manuscript-identity clause.

`QDraft_Work_37` shipped three answers to "which record is this work from?": a
label from the HMO snapshot (`מחזור מנהג אשכנז המערבי`), a description citing
record `990000592310205171` — whose MARC 245 is `גלא עמיקתא`, a different work —
and `work_candidate_evidence` sourced from a third record. Manuscripts have had an
identity anchor since Rule W-137; works had none, so three independent walks of
`entity.control_numbers` each stopped at whichever record they reached first.

Invariant:

1. **`_work_identity_record` is the anchor**: the identity control number when it
   indexes, else the UNIQUE record whose titles strictly match the work's own
   label, else fail closed. The description's attestation clause and the evidence
   walk both read it.
2. **`_titles_match` is strict by default.** Equality after work-title
   sanitisation, or up to an ISBD subtitle break. The old `a in b or b in a` is how
   a bare `מחזור` bound to a 42-character title. `loose` remains for discovery and
   ranking only, floored at 8 characters and 2 tokens, and may never ACCEPT
   evidence.
3. **Every evidence row carries `source_record_id` and `source_scope`, even when
   empty** — "no record backs this row" is a stated fact, not a missing key.
4. **A label-keyed merge may not union across records.** Two works that each name
   records and name disjoint ones do not merge without corroborating evidence;
   recordless works still merge, since refusing would mint a duplicate. The
   evidence union dedupes on `(title, record, field)` — deduping on the title alone
   silently kept only the first of two records attesting it — and drops rows whose
   record is not among the merged item's own.
5. **The gate blocks `WORK_EVIDENCE_RECORD_MISMATCH`** when the description cites a
   record the item neither owns nor evidences.

### Rule W-167(b) — An authority identifier must look like the register it claims (added 2026-08-06)

Run `48ba6c13` published **105** statements of `P214 = 987007…` — an 18-digit NLI
J9U number asserted as a **VIAF ID**, which violates P214's own Wikidata format
constraint. The HMO ontology's `viaf_id` property was taken at face value by
`hmo_wikidata_pq_mapper`. For 48 of those persons P214 was the *only* identifier,
so it was simultaneously what made them publishable under Rules W-153 / W-154 —
dropping it would have deleted the person.

Invariant: `route_authority_identifier` sends the value to the property whose
SHAPE it matches (an 18-digit `987…` is a J9U, a short number is a VIAF cluster),
and refuses a value that fits neither register — an identifier we cannot attribute
is worse than an absent one (Rules W-67 / W-72). The build gate blocks
`IDENTIFIER_WRONG_PROPERTY` and `IDENTIFIER_SHAPE_UNRECOGNISED`.

### Rule W-168 — An item whose identifier already exists on Wikidata is an UPDATE (added 2026-08-06)

Run `48ba6c13`'s rebuild produced 19 fails, and 14 of them said the same thing: the
duplicate probe had found a live Wikidata item carrying this item's own `P8189`,
and the judge — correctly — refused to create a second one and told the curator to
link instead. One manuscript said it about `P3959`. The finding was right and
newly visible (Rule W-156), but leaving the item as a CREATE meant the risk stayed
on the board and a human had to re-key 15 QIDs the probe had already resolved.

Invariant: `adopt_identifier_matched_duplicates` sets `existing_qid` from the
probe, turning the CREATE into an UPDATE. Deliberately narrow, and fail-closed at
every edge:

1. Only `candidates_found`, and only when the item has no `existing_qid` yet.
2. Only IDENTITY keys — an authority/catalog identifier, or a composite whose AND
   was verified client-side (Rule W-144). **A title match is a likeness and stays
   with the curator** (Rule W-145).
3. Only when exactly ONE distinct QID was found. Two is ambiguous, and ambiguity is
   a curator decision (Rule W-37's reasoning). The ambiguity is recorded on the
   item either way.
4. **Adoption does not authorise the write.** The upload path still classifies
   ownership and blocks a foreign item until the curator explicitly accepts that
   QID (Rule W-99). This only stops us *proposing* a duplicate.
5. **Adoption MUST refuse untrusted identity (Rule W-170).** When
   `heading_mismatch` is set, or the probe candidate label conflicts with the item
   label, record `adoption.adopted: false` (and clear a prior bad `existing_qid`).
   Do not blanket-block solely on `wikidata_crosscheck_fail` after identifiers were
   stripped — that left CREATE + live P8189 for the judge to fail under W-139.
6. Applied on the verify path, the export, and the build — the build reads the
   probe cache only, never the network (Rule W-119), so it reflects whatever the
   last verify run learned. Both item shapes are told: the serialised dicts drive
   the curator table, the native items drive validation and upload.

The rubric is amended to match: with `adoption.adopted: true` the duplicate axis is
settled and the judge assesses the claims instead.

### Rule W-169 — Review-table verdict reads MUST replay verify's adoption and live labels (added 2026-08-06)

After verifying only the 19 `unknown` rows on run `48ba6c13`, the modal showed
every item judged and `WikidataItemOverride.ai_verdict` held `full`/`partial`/`fail`
with matching `stable_cache_key`s — but `GET /wikidata-studio?list_view=true`
still rendered 19 unknowns. Two verify-only enrichments were missing from
`fetch_merged_wikidata_items` before stale sanitisation:

1. **Cached QID adoption (Rule W-168).** Verify sets `existing_qid` from the
   probe before fingerprinting. The Studio cache can still be a CREATE until the
   next rebuild, so the table hashed CREATE-shaped rows and dropped the verdicts.
2. **Live value-label glosses (Rule W-80).** Verify stamps `value_label` on
   statement QIDs the static tables cannot gloss. Those labels enter the stable
   fingerprint (only `__LOCAL:` labels are stripped — Rule W-152). List reads
   never glossed, so manuscript keys diverged even when adoption did not apply.

Invariant: before `_sanitise_merged_verdicts`, the merge path MUST
`attach_cached_duplicate_evidence` + `adopt_identifier_matched_duplicates` on the
display rows, mirror adopted QIDs onto the pre-derived stable rows, and
`attach_live_value_labels` on both whenever any stored verdict is present. The
state that keys a verdict is the state the table validates — one item state, not
two. A gloss or adoption failure MUST NOT fail the list read.

Tests: `test_wikidata_item_views.py`
(`test_merged_view_keeps_verdict_after_probe_qid_adoption`,
`test_merged_view_keeps_verdict_when_live_labels_match_verify`).

### Rule W-170 — Non-passing projection defects MUST be fixed fail-closed, not rubric-weakened (added 2026-08-06)

Run `48ba6c13` still had 10 `fail` and 24 `partial` Wikidata Studio verdicts after
the W-169 table fix. The judge was right on every bucket — the builder was wrong.
After the first W-170 ship the table was 18 fail + 19 partial: most person fails
were CREATE + live `P8189` while adoption was blanket-blocked on
`wikidata_crosscheck_fail`. Export (26) after the soft-reject ship still had
27 non-passing rows; these invariants close the remaining gaps without weakening
the eval-agent rubric:

1. **Person identifiers require a trusted authority row (extends W-166).**
   `P8189`, `P214`, and the other cluster-harvested external IDs MUST NOT emit when
   `heading_mismatch` is set or `wikidata_crosscheck_fail` is in the match row's
   `guard_flags`.
2. **Canonical soft-reject MUST strip identity claims, not only dates.**
   `hmo_canonical_wikidata._suppress_unconfirmed_person_identity` removes
   `P569`/`P570`, identifier PIDs, and `P1559`. Persons left identifierless are
   dropped (W-154). Legacy `person_projection` alone is not enough — Studio source
   is `hmo_wikibase+marc`. Auto-detect only the **same-family / different-given**
   conflation (Sara Molho ID on a Shabtai label) — broader preferred↔label
   disagreements (family vs personal headings, toponymic bynames) ship on many
   full-passing rows and MUST NOT strip. Clear a bad `existing_qid` when
   `heading_mismatch` is set.
3. **W-168 adoption MUST refuse wrong identity, not every crosscheck flag.**
   Block on `heading_mismatch`, or when a **same-script** probe candidate label
   conflicts with the item label (Maurizio→Kagel). Manuscripts skip the label
   gate (catalog/composite keys, not title likeness). Cross-script (HE item vs
   EN Wikidata) adopts when (a) a trusted Latin form on the item matches the
   candidate (EN label/alias, or `preferred_name_lat` whose Hebrew preferred
   also matches the HE label), or (b) a small fail-closed HE→Latin given/surname
   map agrees (hyphen-split EN tokens; secondary mapped tokens cover
   `יהודה שאול` ↔ `Yehuda Ben-Shaul`) — never by identifier alone. When adoption
   refuses on label conflict, **strip the probe-matched identifier** from the
   item and rewrite `duplicate_check` to `absent` so CREATE is not still a
   W-139 fail. Clear a prior bad `existing_qid` on heading mismatch or label
   conflict. Do **not** blanket-block adoption solely because
   `wikidata_crosscheck_fail` is present after identifiers were stripped.
4. **P1559 MUST equal the public Hebrew label** (or be omitted) — never a Mazal
   preferred_name that names someone else. Re-align after canonical↔legacy merge.
5. **Canonical-reference P921 requires a topical subject hit.** Bible-book and
   Talmud-tractate QIDs from `canonical_references` emit only when a MARC 650
   topical subject heading also names the same concept
   (`canonical_reference_grounded_in_subjects`). Person/org headings that merely
   contain a biblical name (600 `שמואל`) MUST NOT ground a Books-of-Samuel P921.
   Canonical sanitize also drops ungrounded bible-book P921 and catalog-note
   `P1684` values such as `רשומה זמנית | …`.
6. **Description years follow audited precision (W-164).** English descriptions
   never embed Hebrew century text or century midpoint years; Hebrew descriptions
   omit those midpoints too. `manuscript_record_label` MUST receive the MARC
   record so language follows 041/008, and MUST name the resolved holder (or say
   `catalog record`) — never invent `NLI record` for a Cambridge holding.
7. **Printed facsimiles label as facsimiles in Hebrew.** When
   `_is_printed_facsimile_record`, the `he` designation is facsimile wording and
   stays consistent with `P31=Q571`.
8. **Millimetre dimensions without units.** Unit-less `145X100` pairs are millimetres
   when either dimension is ≥100.
9. **Same-family surname particles.** Soft-reject same-family·different-given
   treats `אל חמדי` / `אלחמדי` as one surname so a father's Mazal ID on a son's
   label is stripped (Person_17).
10. **Date-gated patronymic father-ID.** When Mazal emits P569/P570 and the
    preferred given is embedded as a non-given token in the MARC label (or
    vice versa) while givens differ, strip identity — Person_91
    (יהויכין vs מרדכי 1290–1355). Without dates this must not strip the ~12
    full-passing father/son patterns.
11. **Subset verify MUST resolve `__LOCAL:` from the full Studio catalog.**
    `attach_local_reference_targets(items, catalog=…)` keeps P1574/P3342
    evidence when only non-passing manuscripts are judged.
12. **P1559 equal to `labels.he` is label-backed** in claim_sources when
    authority rows were slimmed; weak editor/commentator descriptions without
    dates fall back to the generic person line.
13. **MARC role wrappers MUST normalise before ROLE_TO_PID.** Forms like
    `(מעתיק)` / `"(מעתיק)"` strip to the Hebrew relator so P11603/P3342 emit
    (`role_normalize.normalize_marc_role`); `מוזכר` maps to P3342.
14. **P1071 is production place only.** MARC 751 / `related_places` /
    non-matching `kima_places` stay on P7153; bare 751$a and
    `$e=related place` MUST NOT fill production `place`. Amran city is
    **Q172597** (verified live) — never Q48200 (Kufra District) or the
    governorate Q275720 alone when a city override exists.
15. **Manuscript P1476 MUST stay MARC 245.** Auto-replacing a linked/known
    work title with the shelfmark (export-29) left `claim_sources` on
    `marc.title` while the value became P217 and regressed 19 `full`→`partial`
    manuscripts in export-30. Do not resurrect that swap without rewriting
    the evidence channel in the same change.
16. **Orphan P3342 bare QIDs drop.** After local-ref resolve, a manuscript
    P3342 naming a public QID that is not any built person's `existing_qid`
    and lacks P1932 is removed (export-29 Malkiel orphan).
17. **Quantity claims MUST carry `unit` into the judge fixture.** P2048/P2049
    are millimetres; omitting `unit` from fingerprint statements made the
    judge treat `95` as centimetres against MARC `9.5 ס"מ` (export-31).
18. **Catalog mention markers MUST NOT pollute aliases.** Trailing
    `(מוזכר)` / ownership relators strip via `_MARC_RELATOR_RE` like other
    MARC role parentheses.

Tests: `backend/tests/unit/test_wikidata_nonpassing_buckets.py`,
`test_person_heading_and_crosscheck.py`, `test_duplicate_qid_adoption.py`.

### Rule W-171 — Corpus-scale Mode-α predicates + Mode-β sticky-full (added 2026-08-08)

Export-32 on canary run `48ba6c13` still had 17 partial + 1 fail after W-170.
Fifteen of sixteen `full→partial` regressions had **identical** claim
signatures — a `w170_v1` schema bump forced DeepSeek to re-score unchanged
payloads. Fixes MUST be predicate-driven for the full filtered corpus
(~123k MARC rows in `filtered_manuscripts_after_906a.tsv`), never CN/shelfmark
allowlists.

**Projection (Mode α):**

1. **ISBD title/subtitle** — `split_isbd_title_subtitle` on 245 `$a`/`$b` so
   P1476 and P1680 never duplicate the full `a : b` string.
2. **P1574 specificity ladder** — `work_link_specificity.refine_exemplar_work_qid`
   prefers megillah/book QIDs (Esther = **Q131068**, live-verified; must stay in
   `QID_LABELS` or the export quality gate raises `MISSING_VALUE_LABEL`), blocks
   whole-Bible/Tanakh when piyyut/miscellany evidence lacks whole-collection
   attestation, and drops redundant bare `Q234460` exemplars.
3. **Agent public QIDs** — `person_linking` withholds a resolved QID when
   `heading_mismatch` or `identity_compatible(marc, label)` fails.
4. **Extent** — P1104 always carries leaf/page unit from `extent_unit` /
   `parse_extent`.
5. **P1922** — only `is_incipit_text` (catalog workflow notes excluded).
6. **Aliases** — subtract per-record contained/related work titles.
7. **Inception** — century `year_start` midpoints must not steal an exact CE
   year embedded in `dates.original_string` (e.g. 1676 inside a 16–17th-c.
   range).

**Verify (Mode β + durability):**

8. **Rubric/skill** — universal Mode-β clauses (absent claims ≠ defects;
   trust `value_label`; honour quantity `unit`; facsimile `semantic_type`;
   WPM holder+shelfmark labels; P1476 may equal linked work title). Skill
   version `w171_v1`.
9. **Sticky-full** — `partition_wikidata_verify_cache` reuses a prior
   `full`/`pass` verdict when the schema-agnostic `claims_fingerprint`
   matches (override `ai_verdict` carried through
   `apply_wikidata_item_override`). Partial/fail never stick; claim changes
   and duplicate-class transitions still re-judge. Verdict schema
   `WIKIDATA_VERDICT_SCHEMA=w171_v1`.

Tests: `backend/tests/unit/test_wikidata_corpus_scale_w171.py`.


### Rule W-172 — Judge fixture keeps quantity unit + export-33 hygiene (added 2026-08-08)

Export-33 on canary `48ba6c13` after W-171: **239 full / 12 partial / 1 fail**.
Nine manuscripts already carried `P1104` with `unit=Q107256474` (leaf), but
`eval-agent/eval_agent/ingest/wikidata_items.py::compact_statements` **dropped
`unit`** when building the judge prompt, so DeepSeek invented “missing unit
qualifier” and partialled correct claims. Backend
`wikidata_verify_fixture.fingerprint_statements` already kept `unit` — the
eval-agent re-compact was the hole.

**Invariants:**

1. **Judge statements must include `unit` / `value_type`** whenever present on
   the Studio statement. The Wikibase quantity `unit` field **is** the unit;
   never demand a separate unit qualifier.
2. **Hebrew manuscript descriptions omit shelfmark** so `descriptions.he` cannot
   equal the holder+shelfmark `labels.he` when the century fragment is absent.
3. **P1684** — scholarly catalog attribution (`לפי דעת`, `מיוסד על`, `וראה:`)
   is a catalog note, not an inscription.
4. **Person aliases** — authority preferred names and seeded aliases that fail
   `heading_matches` against the item label are omitted (no different-person
   alias pollution).
5. **Facsimile works** — descriptions use “printed facsimile edition”, not
   “preserved in a Hebrew manuscript”.
6. **Mode-β** — do not invent that `existing_qid` is a disambiguation page
   without `P31=Q4167410` in evidence (export-33 Person_71 / Q209579 is a
   human, Victor Amadeus II).
7. Schema / skill salt **`w172_v1`**.

Tests: `backend/tests/unit/test_wikidata_export33_w172.py`.

### Rule W-173 — RELATED_WORKS P1574 ladder + Hebrew preferred identity (added 2026-08-08)

Export-34 on canary `48ba6c13` after W-172: **245 full / 6 partial / 1 fail**.

**Projection:**

1. **RELATED_WORKS → P1574** must run `refine_exemplar_work_qid`. Bare
   `Bible` / `תנ״ך` aliases on piyyut manuscripts must not survive; whole-
   scripture attestation comes from the manuscript record, not from the
   candidate alias alone.
2. **P1922** — catalog chronology notes (`בשנת…`, `בעמוד N…`) are not
   incipits (`is_incipit_text`).
3. **P8189** — when a Hebrew Mazal `preferred_name_*` fails
   `heading_matches` against the person label, strip identifiers (export-34
   Person_147/95/32). Latin-only preferred rows are not used for this gate.
4. **Aliases** — post-build `_scrub_person_aliases` re-applies the
   `heading_matches` filter.
5. **P195 value_label** — `Q1028334` is Cambridge University Library in
   `QID_LABELS` (judges must not invent NLI).

Schema / skill salt **`w173_v1`**.

Tests: `backend/tests/unit/test_wikidata_export34_w173.py`.
