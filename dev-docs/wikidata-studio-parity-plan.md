# Wikidata Studio — HMO-Parity Modernization Plan

Status: **implemented** (2026-07-10). See Rule W-57 in `CLAUDE.md`.

---

## 1. Context — why now

The HMO Wikibase Studio was built *after* the Wikidata Studio and, through a
long fixup loop (runs `48ba6c13` audits, Rules W-41/W-42/W-44/W-45/W-48/W-50/
W-52/W-53/W-54), accumulated a materially better architecture:

- a **merged per-item read model** (`fetch_merged_hmo_items`) that joins build
  cache + curator overrides + live-wiki mappings + last-upload audit rows +
  SHACL issues + stale-sanitized AI verdicts into one row shape;
- a **durable upload audit trail** (`wikibase_cloud_writes` + badges) with
  honest outcome vocabulary (`create/adopt/update/skip/unchanged/failed/
  blocked`);
- a **single-item push** loop (Apply AI fix → Push now) that closes the
  verify→fix→publish cycle without a full corpus re-upload;
- a **hard quality gate at build time** (`hmo_export_quality`) plus a
  fail-closed **SHACL gate at upload time**;
- a **rich review table + detail drawer** (filter popups, badges, pagination,
  filtered-scope verify) instead of a sidebar list;
- an **upload hub** with default-off pre/post-upload AI verification and a
  curator confirm gate on failures (never silent block, never silent proceed).

The Wikidata Studio has the *guards* (Rule W-30 fail-closed reconcile +
validator gate + moratorium) but not the *surface*: no audit trail, no upload
outcome visibility, no per-item push, no verdict persistence on the override
row, a bespoke sidebar UI, and a QuickStatements export that bypasses every
guard. This plan closes the gap.

### 1.1 Current Wikidata Studio inventory (research summary)

Backend (`backend/app/routers/wikidata_studio.py`, 2143 lines, prefix
`/runs/{run_id}/wikidata-studio`):

| Surface | State today |
|---|---|
| Build | `GET /` paginated slice from `WikidataStudioCache` (fingerprint, Rule W-26); miss → 409 + `wikidata_studio_build` job; `force_rebuild` supported |
| Overrides | `PATCH /items/{local_id}` → `WikidataItemOverride` (labels/descriptions/aliases/add\_statements/remove\_statements/statement\_edits/**approved**), versioned via `ProjectEvent` |
| Reconcile | `POST /reconcile` — **preview only**, never affects upload behaviour (by design, Rule W-30) |
| Upload | `POST /upload` → `wikidata_upload` job; `_prepare_for_upload` = reconcile-before-create + `validate_item` hard gate + `ReconciliationUnavailableError` fail-closed + `MORATORIUM_LIFTED` / `WIKIDATA_TEST_MODE` gates |
| Compare | `GET/POST /items/{id}/wikidata-compare[/apply]` live-vs-studio diff + merge |
| AI verify | `wikidata_verify` job + SSE; evaluators `wikidata_item` / `wikidata_autofix`; verdict cache `w50_v1` (content-addressed, Rule W-51); **verdicts persist to inference cache only, NOT to the override row** |
| Apply AI fix | `POST /items/{id}/ai-fixes/apply` → `merge_ai_fixes` (exists, but only reachable from `VerdictsTable` inside the modal) |
| QS export | `GET /quickstatements.txt` — **raw builder output, no reconcile/validate/moratorium gate** (documented residual gap in Rule W-30) |
| Frontend | `WikidataStudio.tsx` (1362 lines): sidebar+panel item view / custom flat statement table; header toolbar (rebuild, reconcile, verify, dry-run/live upload, QS, approved-only) |

Tests pinning it: `unit/test_wikidata_upload_guards.py` (16),
`unit/test_wikidata_studio_slicing.py` (33), `test_wikidata_autofix_apply.py`,
`test_wikidata_entity_compare.py`, `test_wikidata_verdict_cache.py`,
`integration/test_wikidata_verify_endpoints.py`,
`frontend/e2e/wikidata-studio.spec.ts` (15).

### 1.2 HMO reference architecture (what we're copying)

| Capability | HMO implementation |
|---|---|
| Merged read model | `hmo_item_views.py::fetch_merged_hmo_items` — cache + overrides + mappings + latest audit write + SHACL + stale-sanitized verdict |
| Audit trail | `wikibase_cloud_writes` table + `services/wikibase_audit.py` (`record_wikibase_write`, `fetch_latest_wikibase_writes` — portable group-by-max, never 500s the request) |
| Item table | `HmoItemTable.tsx` — Label · Local ID · Class · Source URI · Data status · QID · Last push · Validation · AI verdict · Approved · Deep dive; search, `ColumnFilterPopup` per column, sort, 25/page pagination, `useReportDerivedIds` filtered-scope reporting |
| Detail drawer | `HmoItemDetailDrawer.tsx` — badges row, editable labels/descriptions, add-statement, reconcile, Save override, Approve, history 📜, **Verify with AI / Autofix / Apply AI fix / Apply fix & push / Push now** |
| Upload hub | `ItemUploadPanel.tsx` — dry-run, update-existing, allow-validation-errors, **pre-upload verify** (fail → confirm gate: Review flagged / Upload anyway), **post-upload autofix verify**, `Tier1ModelSelect`, job progress + `AgentFlowDiagram`/`VerdictsTable` inline |
| Verify modal | `HmoItemVerificationModal.tsx` on `useVerifyJob` (job-backed, Rule W-44 — never long-lived SSE for large scopes) |
| Verdict persistence | `HmoStudioItemOverride.ai_verdict/ai_verdict_at` + inference cache; `sanitise_stale_*` invalidates on input change (Rule W-51) |
| Single-item push | `push_single_item` shared by bulk pass-1 and `POST /items/{id}/push`; commits DB tx before slow network call (Rule W-40) |
| Quality gates | `assert_export_quality` (hard block at build) + SHACL gate at upload (Rule W-42) + label sanitization (`und`→`en`, gershayim-aware quote balance) |
| Outcome vocab | created/updated/**adopted**/skipped/unchanged/blocked/failed (+ `would_*` dry-run variants); adopt = reconcile matched an existing live item, mapping recorded, no create |
| Validation errors list | `GET /items/validation-errors?on_wiki_only=` cleanup surface |
| Export/import | `GET /items/export?format=json\|csv` (incl. ai_verdict columns) + `POST /items/import` |

---

## 2. Key judgment calls (apply throughout)

1. **Reuse `wikibase_cloud_writes` for the Wikidata audit trail** — new
   channel `wikidata_upload`, `target_kind="item"`, `target_key=local_id`,
   `wikibase_id=QID`. The table schema (channel/operation/target/outcome) is
   already generic and `fetch_latest_wikibase_writes` is channel-parameterised.
   A second parallel table would duplicate the audit service for zero gain.
   (The table name is slightly off — acceptable; a rename migration is not
   worth the churn.)
2. **Do NOT touch `converter/wikidata/uploader.py`** — byte-identical desktop
   mirror (Rule W-4/W-10). All new logic lives in `app/pipeline/
   wikidata_upload.py` and new `app/pipeline/wikidata_item_views.py`, exactly
   as Rule W-30 did.
3. **`WikidataItemOverride` gains `ai_verdict`/`ai_verdict_at`** (migration),
   mirroring `HmoStudioItemOverride`. Verdict-in-inference-cache-only is why
   the current table has no AI-verdict column and the modal is the only place
   verdicts are visible. Persistence follows Rule W-17/W-51 patterns
   (fresh `session_scope`, content-addressed `cache_key`, stale sanitization).
4. **Adopt-on-reconcile writes a durable QID ledger.** Today a reconcile match
   during upload sets the QID only on the in-memory item; nothing durable
   records "this local entity IS Qxxxx", so the *next* build/upload re-runs
   live reconciliation for the same entity. Reuse `wikibase_entity_mappings`
   (`entity_kind="instance"`, `ontology_uri=` the item's stable source key —
   see Phase 3 for the key choice) so upload becomes idempotent the way HMO's
   is. This is the single biggest "entity creation" improvement.
5. **Frontend: build Wikidata siblings of the HMO components, extract shared
   pieces where cheap.** `ColumnFilterPopup`, `AiVerdictPill`,
   `useReportDerivedIds`, `useVerifyJob`, `AgentFlowDiagram`, `VerdictsTable`,
   `Tier1ModelSelect`, `HistoryTimeline` are already shared. The table/drawer/
   upload-panel get Wikidata-specific copies (`WikidataItemTable`,
   `WikidataItemDetailDrawer`, `WikidataUploadPanel`) rather than a premature
   generic abstraction — the HMO ones are heavily HMO-shaped (SHACL, source
   URIs, deferred links). Where a badge is genuinely identical
   (`UploadOutcomeBadge`), lift it to a shared module.
6. **Keep the existing sidebar Item view and statement-table view** as
   secondary views (the `<details>`-style preservation precedent from Rule
   W-16). The new table+drawer becomes the default. This keeps the 15 existing
   e2e specs mostly green during the transition and preserves the statement-
   level editing surface the drawer won't fully replicate at first.
7. **Close the QuickStatements gap by gating, not deleting.** Route the QS
   export through the same prepared-items pipeline (`_prepare_for_upload`)
   with `?gated=true` default: blocked items are dropped from the TSV and a
   header comment lists what was excluded and why. An explicit
   `?gated=false&i_understand=true` escape hatch preserves the raw export for
   debugging. Rationale: Rule W-30 explicitly flags this as the remaining
   bypass of the April-failure protections; approved-only filtering is not a
   dedup/validity control.
8. **Validator grows toward `hmo_export_quality`, stays imperative.** Wikidata
   has no SHACL shapes; `item_validator.py` is the equivalent gate. Add the
   label-hygiene lessons from W-45/W-50/W-52/W-53 as WARNING/ERROR codes
   (see Phase 4) rather than inventing a SHACL layer for Wikidata.
9. **Cache-salt bumps per Rule W-51**: `WIKIDATA_VERDICT_SCHEMA` bumps to
   `w57_v1` when the rubric/evaluator inputs change (Phase 6); the build
   fingerprint already self-invalidates.
10. **No auto-approve rule builder in v1.** HMO parity doesn't include one
    (HMO has per-item approve only); Wikidata writes are the highest-risk
    surface in the whole app (Rule 25 moratorium, Epìdosis deletion request).
    Bulk "approve all visible" (explicit curator action over an explicit
    filtered scope) is included instead; a rule builder can follow later if
    review volume demands it.

---

## 3. Gap matrix (what ships in which phase)

| # | Gap (Wikidata Studio today) | HMO counterpart | Phase |
|---|---|---|---|
| 1 | No durable upload audit; outcomes only in transient `run_job.result` | `wikibase_cloud_writes` + `fetch_latest_wikibase_writes` | **P1** |
| 2 | No merged read model; router slices raw cache + overrides ad hoc | `fetch_merged_hmo_items` | **P1** |
| 3 | AI verdicts not on override rows → invisible outside modal | `HmoStudioItemOverride.ai_verdict` + stale sanitization | **P2** |
| 4 | No single-item push; batch upload only | `push_single_item` + `POST /items/{id}/push` | **P3** |
| 5 | Reconcile match not durably recorded → re-reconcile every upload | `wikibase_entity_mappings` instance ledger + `adopt` outcome | **P3** |
| 6 | QS export bypasses all guards | (HMO has no ungated bulk export) | **P4** |
| 7 | Validator misses label/description-hygiene classes found in W-45/50/52/53 | `hmo_export_quality` codes | **P4** |
| 8 | No validation-errors cleanup surface | `GET /items/validation-errors` | **P4** |
| 9 | Sidebar UI; no rich table, no filter popups, no badges, no drawer | `HmoItemTable` + `HmoItemDetailDrawer` + badges | **P5** |
| 10 | Upload buttons in header; no pre/post-upload verify, no confirm gate | `ItemUploadPanel` | **P6** |
| 11 | No items export/import endpoints (only frontend SectionExportMenu) | `/items/export` + `/items/import` | **P5** |
| 12 | API client drift (`approved` missing from `ItemOverrideResponse` TS; no client for ai-fixes/clear-override) | — | **P5** |
| 13 | No bulk approve over filtered scope | (HMO: per-item only; we add scoped bulk) | **P5** |

Explicitly out of scope: coverage report (that is an RDF-projection concept —
already surfaced on the RDF/HMO pages; Wikidata items are match-driven, not
graph-driven), auto-approve rule builder (judgment call 10), desktop repo
changes, any live wikidata.org write (moratorium stands).

---

## 4. Phases

Each phase is independently mergeable and tested; later phases depend on
earlier ones only where stated.

### Phase 1 — Durable audit trail + merged read model (backend foundation)

**New** `backend/app/pipeline/wikidata_item_views.py`:

- `fetch_merged_wikidata_items(db, run_id, *, approved_only) -> list[dict]` —
  the Wikidata sibling of `fetch_merged_hmo_items`. Per item joins:
  - build row from `WikidataStudioCache.result_items` (existing shape,
    including `validation_issues` from `validate_item`);
  - override merge (existing `_group_entity_rows` / override-precedence logic,
    extracted out of the router so both the slice endpoint and the new view
    share it);
  - `wikidata_qid` + `on_wikidata` status (builder QID, override, or ledger —
    ledger arrives in P3; until then builder/override only);
  - `upload_outcome` / `upload_message` / `upload_at` from
    `fetch_latest_wikibase_writes(db, run_id, channel="wikidata_upload",
    target_kind="item")` (`None` if never attempted);
  - `ai_verdict` (P2; `None` until then).

**Modify** `backend/app/pipeline/wikidata_upload_job.py` and
`wikidata_upload.py::_upload_sync`:

- Thread a `WikibaseAuditContext` (actor/project/run/job) through the live
  upload loop; `record_wikibase_write` per outcome with channel
  `wikidata_upload`, operations `create/update/skip/failed/blocked` (+
  `adopt` from P3). Audit intent is recorded with the outcome message
  (validator codes for blocked, reconcile reason for adopt/skip) — mirroring
  `hmo_item_upload.py`. Dry-run writes **no** audit rows (parity with HMO).
- Add `OPERATION_BLOCKED` to `wikibase_cloud_write.py` constants if absent.

**Modify** `wikidata_studio.py` router: `GET /` item rows gain
`upload_outcome/upload_message/upload_at` (from the merged view) so the
frontend can render outcome badges without a second call. `_slice_items`
filter set gains `upload_outcome`.

Tests: extend `test_wikidata_upload_guards.py` (audit rows written per
outcome, dry-run writes none, audit failure never fails the job — swallowed
like HMO), new `test_wikidata_item_views.py` (merged fields populated from
`wikibase_cloud_writes`, latest-row-wins), extend
`test_wikidata_studio_slicing.py` (upload_outcome filter).

Migration: none (reuses `wikibase_cloud_writes`).

### Phase 2 — AI-verdict persistence on the override row

**Migration** `00XX_wikidata_override_ai_verdict`: add `ai_verdict JSONB`,
`ai_verdict_at TIMESTAMPTZ` to `wikidata_item_overrides`.

**Modify** `wikidata_studio.py::_wikidata_verify_event_stream` (and the
job-backed path in `verify_job.py` if it has its own persistence hook): after
the stream finishes, `_persist_wikidata_verdicts_to_overrides` opens a fresh
`session_scope` (Rule W-17 pattern — the request session is closed by then)
and upserts verdict summaries into `WikidataItemOverride.ai_verdict` keyed by
`local_id`. Summary shape mirrors HMO: `overall`, per-dimension flags,
`reasoning`, `suggested_fixes`, `model`, `judged_at`, `cache_key`,
`session_id`, `evaluator`.

**Stale sanitization**: `fetch_merged_wikidata_items` runs
`sanitise_stale_wikidata_verdict` (already exists in
`wikidata_verdict_cache.py`) against the item's current
`wikidata_verdict_input_fingerprint` so a rebuild/override edit clears the
pill without `override_cache` (Rule W-51).

**New endpoint** `GET .../items/{local_id}/cached-verdicts` is *not* needed —
HMO's `GET /items/ai-verify/cached-verdicts?tier_model=` bulk shape is copied
instead so the table can pre-populate pills for items judged in prior
sessions but never persisted (backfill path).

Tests: `test_wikidata_verdict_persistence.py` (persist on stream end, stale
verdict dropped after override edit, cache_key mismatch dropped), extend
`integration/test_wikidata_verify_endpoints.py`.

### Phase 3 — Single-item push, adopt semantics, durable QID ledger

This is the "improve Wikidata entity creation" core.

**Ledger.** Reuse `WikibaseEntityMapping` with `entity_kind="instance"` and a
new discriminator so HMO and Wikidata rows never collide. Two options were
considered; chosen: **prefix the `ontology_uri` key with the target**, i.e.
store `wikidata:{stable_source_key}` — no schema change, and the HMO queries
already filter by their own URI namespaces. The stable source key per entity
type: manuscripts → control number (`marc:{cn}`), persons → the authority
match identity (`match:{authority_match_id}`), works → `work:{run-scoped
local_id}`. Recorded on: successful CREATE (live), reconcile ADOPT (live or
explicit per-item reconcile), and manual QID confirmation via override.
`wikibase_id` = QID. **Consulted before live reconcile** in
`_prepare_for_upload`: ledger hit → treat as existing (update path), skip the
WDQS round-trip. Ledger rows are per-run=NULL (global): a person adopted in
run A must not be re-created by run B — this is exactly the April-failure
class.

**Adopt outcome.** `_prepare_for_upload`'s reconcile-hit branch changes from
silently flipping the item to update-mode to an explicit `adopted` /
`would_adopt` outcome (+ ledger write + audit row with the match reason).
Upload response counters gain `adopted`.

**Single-item push.** **New** `POST .../items/{local_id}/push`
(`?allow_validation_errors=false` — no effect on ERROR-severity codes, which
always block; it only bypasses WARNING-severity soft-blocks if we introduce
any):

- Builds the item's **override-merged** state via
  `fetch_merged_wikidata_items` (parity with HMO: bulk upload does NOT apply
  overrides beyond what the builder consumed; single push does — this is the
  Apply-AI-fix loop);
- Runs the full `_prepare_for_upload` gate on the single item (reconcile
  fail-closed + validator hard gate + moratorium/test-mode check);
- Commits the read transaction before any network call (Rule W-40);
- Executes via the same `WikidataUploader` instance-level guards;
- Records ledger + audit rows; returns
  `{local_id, status, qid, message}`.

Given the moratorium, in practice this endpoint will run against
test.wikidata.org (`WIKIDATA_TEST_MODE=true`) or return the moratorium
refusal — the endpoint must surface `moratorium_lifted`/`test_mode` in its
response exactly like the bulk path so the UI can label the button honestly.

**Per-item reconcile preview→adopt.** **New**
`POST .../items/{local_id}/reconcile` (mirror of HMO's): runs the
entity-type-appropriate reconciler, and on a confident match records the
ledger mapping (status `adopted`) *without* writing to Wikidata. 503 on
`ReconciliationUnavailableError`. This lets a curator resolve "does this
person already exist on Wikidata?" one item at a time, durably.

Tests: extend `test_wikidata_upload_guards.py` (ledger consulted before WDQS;
ledger hit skips reconcile; adopt records ledger + audit; single push blocked
by validator ERROR; single push commits tx before network — mirror
`test_pid_lookup_transaction_is_closed_before_the_sparql_call`), new
`test_wikidata_qid_ledger.py`, `test_wikidata_single_push.py`.

Migration: none (ledger reuses existing table) — unless key-prefix collision
checks demand a `target` column; decide at implementation time, default no.

### Phase 4 — Validation upgrades + QuickStatements gate closure

**`item_validator.py` additions** (each with a regression test; constants
live-verified per Rule W-26-constants before use):

- `GENERIC_DESCRIPTION` (warning): description is empty, equals the label, or
  is a known boilerplate pattern (W-48/W-52 lesson).
- `LABEL_QUOTE_NOISE` (warning): unbalanced ISBD quotes / `""` wrappers /
  `(in MS …)` scope suffixes in labels — reuse the gershayim-aware helpers
  already vendored in `converter/rdf/rdf_helpers.py` (W-45/W-50/W-53).
- `LABEL_LANG_MISMATCH` (warning): Hebrew text in the `en` label slot or
  Latin-only text tagged `he` (W-52 `latin_label_in_he`).
- `INVERTED_PERSON_LABEL` — verify whether the existing Epìdosis
  inverted-name check covers the trailing-`אבן` uninversion case from W-53;
  extend if not.
- `DESCRIPTION_REPEATS_LABEL` (warning) — W-53.
- Severity policy stays: ERROR blocks writes (hard gate unchanged), WARNING
  surfaces in badges and the pre-upload verify prompt but does not block.

**Build-time hard gate** (`assert_wikidata_export_quality`, new small module
mirroring `hmo_export_quality_gate.py`): run over built items inside
`execute_studio_build` *before* the cache upsert, raising only on the
ERROR-severity subset that indicates a build bug rather than a data issue
(e.g. an item with no label at all, a P31 from `_KNOWN_BAD_P31_QIDS`). A
regression can then never even reach the cache/QS/upload. Keep the raising
set minimal — Wikidata items legitimately vary more than HMO's ontology-
driven exports; everything else stays a per-item badge.

**QS gate** (judgment call 7): `download_quickstatements` gains
`gated: bool = True`. Gated path: run `_prepare_for_upload` over the item set
(this makes the QS download hit WDQS — acceptable; it's a curator-initiated
bulk-export moment, and `ReconciliationUnavailableError` must fail the
download with a 503 rather than emit ungated lines). Blocked items are
excluded; the TSV starts with comment lines
(`# excluded N items: local_id — reason`). CREATE lines for items the ledger/
reconciler resolved to an existing QID are rewritten as QID-update lines.
Ungated escape hatch requires both `gated=false` and `ack=raw` query params
and is logged.

**New endpoint** `GET .../items/validation-errors` (mirror of HMO): items
whose current merged state carries ERROR-severity issues; `on_wikidata_only`
filter for cleanup of already-live items.

Tests: extend `unit/test_item_validator.py` (new codes ×2-3 cases each),
new `test_wikidata_export_quality.py`, extend router tests for the QS gate
(gated excludes blocked, 503 on WDQS outage, escape hatch, comment header),
`test_validation_errors_endpoint.py`.

### Phase 5 — Frontend review surface (the big UI change)

New components under `frontend/src/components/wikidata/`, mirroring the HMO
set 1:1 (reusing shared primitives — `ColumnFilterPopup`, `AiVerdictPill`,
`useReportDerivedIds`, `Glass`/`GlassPill` per Rule W-35, Zustand rules per
W-36):

- **`WikidataItemTable.tsx`** — columns: Label · Local ID · Entity type ·
  Data status (new/will update/updated — port `resolveHmoItemDataStatus`) ·
  QID · Last upload (outcome badge) · Validation (validator badge with
  popover listing codes) · AI verdict (pill + reasoning/fixes popover) ·
  Approved (checkbox → PATCH `{approved}`) · Deep dive. Search box,
  per-column filter popups (entity_type / data_status / upload_outcome /
  validation / ai_verdict / approved), sortable headers, 25/page pagination,
  filtered-ids reporting via `useReportDerivedIds`. Server-side slicing stays
  (the run can hold thousands of items) — the column filters map onto
  `_slice_items` params extended in P1; purely client-side filters are
  acceptable only within the fetched page, so prefer server params.
- **Badges**: `WikidataUploadOutcomeBadge` — extract HMO's
  `HmoItemUploadOutcomeBadge` into a shared
  `components/shared/UploadOutcomeBadge.tsx` (same vocab) and have both wrap
  it. `WikidataAiVerdictBadge` likewise wraps the shared verdict-pill+popover.
  `ItemValidatorBadge` (exists) gains the popover listing issue codes.
- **`WikidataItemDetailDrawer.tsx`** — right slide-over (Glass `drawer`
  variant): badges row; editable labels/descriptions/aliases; statement list
  with per-statement Exclude/Undo + qualifiers/references disclosures (port
  from the current `ItemPanel`); **Compare with Wikidata** (embed the
  existing `WikidataComparePanel`); **Reconcile** (P3 endpoint, shows
  adopted/not-found); **Verify with AI / Autofix with AI / Apply AI fix /
  Apply fix & push / Push now** (P3 push; button label reflects
  moratorium/test-mode); Approve/Unapprove; Save override; Clear override
  (finally wiring the existing DELETE endpoint); history 📜
  (`HistoryTimeline`, entity type `wikidata_override` — already versioned).
- **`WikidataItemsPanel.tsx`** — orchestrator: toolbar (Refresh, Export/
  Import, **Verify with AI (n over filtered)**, **Autofix with AI (n)** —
  QID-holding items only, **Approve all visible (n)** with a confirm),
  embeds build controls, the upload panel (P6), table, drawer, verification
  modal.
- **`WikidataStudio.tsx` route refactor**: new table+drawer becomes the
  default view; the existing sidebar Item view and StatementTableView remain
  as toggle options (judgment call 6). Header keeps Rebuild/skip-cache,
  approved-only, QS download (now labelled "gated"), export menu.
- **API client fixes** (`api/wikidataStudio.ts`): add `approved` to
  `ItemOverrideResponse`; add `Studio.applyAiFixes`, `Studio.clearOverride`,
  `Studio.pushItem`, `Studio.reconcileItem`, `Studio.validationErrors`,
  `Studio.exportItems/importItems`; add merged-row fields
  (`upload_outcome/message/at`, `ai_verdict`).
- **Backend export/import**: `GET .../items/export?format=json|csv` (incl.
  ai_verdict + upload outcome columns) + `POST .../items/import` (re-applies
  overrides for known local_ids), copied from `hmo_studio_items.py`.

Tests (Rule W-19 — every click path): extend
`frontend/e2e/wikidata-studio.spec.ts` + new
`wikidata-item-table.spec.ts`, `wikidata-item-drawer.spec.ts` with mocked
routes: column filter → captured query params, approve checkbox → PATCH
body, drawer push → POST captured, apply-fix → override PATCH, badge
popovers, export/import. Backend: `test_wikidata_items_export_import.py`.

### Phase 6 — Upload hub with pre/post-upload AI verification

**`WikidataUploadPanel.tsx`** — port of `ItemUploadPanel.tsx`:

- Checkboxes: **Dry run** (default on), **Update existing** (maps to current
  behaviour), **Approved items only** (existing param), **Verify with AI
  before upload** (default off), **Verify with AI after upload (autofix)**
  (default off), `Tier1ModelSelect` (Rule W-46).
- Pre-verify: scope = items that would be written (dry-run outcome
  `would_create`/`would_update`/`would_adopt`), action `audit_wikidata_item`,
  job kind `wikidata_verify` via `useVerifyJob` (job-backed per Rule W-44 —
  the modal/panel never opens a raw SSE for large scopes). Any `fail`
  verdict → confirm gate: **Review flagged items** (opens
  `WikidataVerificationModal` scoped to the failed ids — warm cache, near-
  instant) or **Upload anyway** — never silent block, never silent proceed
  (the Rule W-41 invariant).
- Post-verify: on live-upload job success, scope = outcomes in
  `{created, updated, adopted}`, action `autofix_from_wikidata`; verdicts +
  `suggested_fixes` persist to overrides (P2), lighting up the table's
  AI-verdict column and the drawer's Apply-fix/Push buttons with no further
  backend work.
- Moratorium honesty: the panel header shows a persistent pill —
  `moratorium active` / `TEST MODE (test.wikidata.org)` / `LIVE` — sourced
  from the upload response fields, so a curator always knows which wiki the
  Live button targets.
- Live upload continues through `useRunJobAttachment("wikidata_upload")` +
  `JobProgressInline`; outcome table gains the `adopted`/`blocked` rows and
  per-row messages.

Cache-salt bump: if Phase 4's validator additions or any rubric change alters
what the judge sees, bump `WIKIDATA_VERDICT_SCHEMA` → `w57_v1`
(`wikidata_verdict_cache.py`) so stale verdicts miss automatically — no
`override_cache` needed (Rule W-51).

Tests: `frontend/e2e/wikidata-upload-panel.spec.ts` (pre-verify fail →
confirm gate both branches, post-verify auto-start, moratorium pill states),
backend `test_run_job_params` extension if new params are added to the verify
job kind (likely none — `wikidata_verify` already exists).

### Phase 7 — Docs, rules, cleanup

- New **Rule W-57** in `CLAUDE.md` (Wikidata Studio parity: audit trail,
  merged view, QID ledger, adopt semantics, gated QS, verdict persistence) +
  bump the W-pointer in `AGENTS.md`/`README.md`.
- Update `docs/project-hierarchy-plan.md` (routes added) and
  `docs/architecture/blocks/wikidata-studio/` pages
  (key-files/how-it-works/rules/tests) per the docs-on-code-change skill;
  final pre-push check via Rule W-49.
- Retire `plans/wikidata-review-parity-and-labels.md` Feature 4 (superseded
  by this plan; Features 1-3/5 already done).
- Delete the dead-code paths: the unused `Studio` client gaps become wired
  (nothing to delete), but re-audit `POST /reconcile` bulk preview — keep it
  (it now shows adopt candidates against the ledger).

---

## 5. Sequencing & dependencies

```
P1 (audit + merged view)  ──►  P2 (verdict persistence) ──►  P6 (upload hub)
        │                              │
        └──►  P3 (push/adopt/ledger) ──┤
        └──►  P4 (validation + QS gate)┴──►  P5 (frontend table/drawer)
P7 last.
```

P1 is the foundation everything reads from. P5 can start against P1+P2 output
shapes with P3/P4 buttons feature-flagged off if those land later. Estimated
relative sizes: P1 ~M, P2 ~S, P3 ~L (the ledger semantics need care), P4 ~M,
P5 ~XL (the bulk of the work), P6 ~M, P7 ~S.

## 6. Migrations summary

| Migration | Contents |
|---|---|
| `00XX_wikidata_override_ai_verdict` | `ai_verdict JSONB`, `ai_verdict_at TIMESTAMPTZ` on `wikidata_item_overrides` (P2) |
| (none) | Audit reuses `wikibase_cloud_writes`; ledger reuses `wikibase_entity_mappings` (P1/P3) |

## 7. Hard invariants that must survive this work

1. **Moratorium (Rule 25)**: no live wikidata.org write without
   `MORATORIUM_LIFTED=true`. Single-item push gets NO special exemption.
2. **`uploader.py` stays byte-identical** to desktop (Rule W-4/W-10); all new
   logic in `app/pipeline/`.
3. **Fail-closed reconcile** (Rule W-30): a WDQS failure is `blocked`, never
   "absent → create". The new ledger *reduces* WDQS calls but a ledger miss
   still reconciles live and still fails closed. The gated QS download
   inherits this (503, never raw lines).
4. **Validator ERROR = hard block** in every write path (bulk, single push,
   gated QS), regardless of approval state.
5. **No auto-approve of AI verdicts** — curator always confirms; verdicts are
   pills + suggested fixes, applied only via explicit Apply actions.
6. **Rule W-40**: every new endpoint commits its read transaction before any
   Wikidata/WDQS network call.
7. **Rule W-51**: verdict cache keys stay content-addressed; schema-salt bump
   on rubric/input change; stale sanitization on read paths.
8. **Rule W-44**: large-scope verify goes through the job runner
   (`useVerifyJob` + `run_jobs.progress.session_snapshot`), never a raw
   long-lived SSE.
9. **Rule W-36**: new frontend components select primitives from stores,
   fingerprint-guard parent callbacks, use `useCallback`-stable handlers.
10. **Rule W-19**: every new click surface ships with a mocked-route e2e
    spec; backend guard changes extend `test_wikidata_upload_guards.py`.

## 8. Risks / open questions

- **Ledger key stability** (P3): the person key `match:{authority_match_id}`
  breaks if a run's matches are re-enriched with new row ids. Alternative:
  key persons by their strongest external identifier set (VIAF/NLI) with the
  match id as fallback. Decide at P3 implementation with a look at how
  re-enrich upserts preserve `AuthorityMatch.id` — if ids are stable across
  re-enrich (upsert, not delete-insert), the simple key is fine.
- **Gated QS latency**: reconciling a full run for a TSV download can take
  minutes. Mitigation: run it as a background job returning a download token,
  or cap gated QS to approved items (which is the real use case anyway).
  Decide by measuring on a real run in P4.
- **`_group_entity_rows` extraction** (P1): the router's override-precedence
  logic is subtle (Rule W-24). Extract with characterization tests first;
  `test_wikidata_studio_slicing.py`'s 33 cases are the safety net.
- **Two views during transition** (P5): keeping the legacy sidebar view
  doubles the surface temporarily. Sunset it once the new table has a full
  e2e suite and one real curation session has used it.
- **test.wikidata.org drift**: single-item push will realistically be
  exercised in test mode first; test.wikidata QIDs must never enter the
  production ledger — namespace ledger keys by target
  (`wikidata:` vs `wikidata-test:`) from day one.

## 9. Curator ops after each deploy

- P1/P2: none (additive). Old runs show `—` for Last upload until their next
  upload; verdict pills populate on the next verify run.
- P3: existing on-Wikidata items get ledger rows lazily (on next
  reconcile/upload/adopt); optionally run a one-shot backfill script from
  historical `run_job.result` outcomes if we want history preserved —
  decide at P3 (script: `scripts/backfill_wikidata_ledger.py`, dry-run
  first).
- P4+P6: if the verdict schema salt bumps, re-verify is sufficient — no
  `override_cache` (Rule W-51).
