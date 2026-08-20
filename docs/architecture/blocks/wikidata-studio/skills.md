# Wikidata Studio — Skills

> Up: [Wikidata Studio](README.md)

### Skill: add or change a P/Q constant

1. Open `https://www.wikidata.org/wiki/Property:PXXX` (or `/QXXX`) — never rely
   on memory or docs; constants can merge/redirect.
2. Confirm label, **data type**, and item-of-property constraint notes match
   the intended use (that's how P7416/P50 went wrong).
3. Edit `backend/converter/wikidata/property_mapping.py` **and** the desktop
   copy (byte parity) — or edit desktop and re-sync.
4. If removing a wrong constant, add it to `_KNOWN_BAD_P31_QIDS` /
   `_KNOWN_BAD_VALUE_QIDS` in `item_validator.py` plus a regression test in
   `backend/tests/unit/test_item_validator.py`.

### Skill: add a validator check

1. Add the check inside `item_validator.validate_item` with a stable UPPERCASE
   `code`, correct `severity` (ERROR blocks writes), and a community-link
   `reference`.
2. Extend `backend/tests/unit/test_item_validator.py` (including a
   "builder never violates" case) and, if the check should block uploads,
   `backend/tests/unit/test_wikidata_upload_guards.py`.
3. No wiring needed: build-time badges and the upload hard gate both call
   `validate_item` already.

### Skill: run a dry-run upload

1. UI: Wikidata Studio page → "Dry run" (starts a `wikidata_upload` job with
   `dry_run: true`). API: `POST /api/runs/{id}/wikidata-studio/upload?dry_run=true`.
2. Prefer having a Wikidata token in Settings so ownership (own vs foreign) is
   truthful on dry-run (Rule W-99). Use **Wikidata (test) bot password** for
   `upload_target=test` and **Wikidata (live)** for live (Rule W-178).
   Without a token, existing QIDs block unless a QID-bound foreign accept is set.
3. Read per-item outcomes: `exists` (would UPDATE own item), `would_adopt`
   (ledger/reconcile match or accepted foreign), `success` (would CREATE),
   `skipped`/`blocked` (foreign without accept, ownership unknown, reconcile
   outage, or validator ERROR — message says which).
4. No live writes in dry-run; moratorium still does not apply to dry-run.
5. If every row fails with **permissiondenied** / "permissions needed", the
   bot password authenticated but cannot create items — open
   `Special:BotPasswords` on the same wiki and enable **High-volume editing**,
   **Edit existing pages**, and **Create, edit, and move pages**, then paste the
   new password into Settings. Upload jobs now abort once at login when
   `createpage`/`edit` are missing.
6. `upload_target=test` remaps live P/Q by English label + datatype
   (search or stub CREATE) so test.wikidata.org can accept the **same**
   WikiProject Manuscripts claim set. Leftover snaks **block** the item
   (Rule W-186) — they are not stripped. Quantity units must remap to a
   test-local millimetre/leaf/page entity. CREATE conflicts adopt the
   existing test Q/P (W-187); property maps are keyed by
   `(live_pid, value_type)`; prior MHM stub items are reusable claim
   values. Foreign-but-alive reconciled QIDs on test clear for CREATE
   (W-181) — never UPDATE community items (W-184). Session P/Q maps are
   warmed once per test upload job.

### Skill: watch upload progress (tray View / Rule W-141)

1. Job-tray **View** on `wikidata_upload` navigates to
   `/runs/{id}/wikidata-studio?job={jobId}`; `WikidataItemsPanel` opens
   `WikidataUploadProgressModal` once and strips `?job=`.
2. Starting or attaching an active upload from `WikidataUploadPanel` opens the same
   modal. Progress rows come from `recent_item_outcomes` / `outcome_counts`; the
   review table still patches via `onUploadOutcomes` (Rule W-110). Title + badge
   follow `upload_target` (test/live/dry-run) and stay sticky after cancel/finish
   (frontend R19) — they MUST NOT flicker to Live when the active poll drops the job.
3. **Close** dismisses the modal without cancelling; **Cancel upload** calls
   `POST …/jobs/{id}/cancel` while the job is still active. Cancel progress/result
   still carries `upload_target`.

### Skill: accept modifying a foreign Wikidata QID

1. Open the item drawer when `existing_qid` is set.
2. Tick **I accept modifying Q… even if I did not create it** — PATCHes
   `accept_foreign_modify=true` + `accepted_foreign_qid=<that QID>`.
3. Re-run dry-run/upload. Accept must match the reconciled QID at write time;
   a different QID still skips. Duplicate CREATE remains forbidden.
4. Contract: `docs/wikidata-data-access.md` + Rule W-99.

### Skill: map HMO / project Wikibase claims to public Wikidata P/Q

1. Edit only `backend/converter/wikidata/hmo_wikidata_pq_mapper.py` allowlists
   (`HMO_LOCAL_NAME_TO_WIKIDATA_PID`, `HMO_CLASS_TO_WIKIDATA_QID`) — keep WPM
   alignment (`P9302` script, `P1104` folios, no MS P50).
2. Never map a bare project Cloud `P…`/`Q…` by numeric identity; project PIDs
   need the schema ledger ontology URI.
3. Sync ontology `owl:equivalentProperty` in `hebrew-manuscripts.ttl` when
   changing a mapped PID.
4. Extend `backend/tests/unit/test_hmo_wikidata_pq_mapper.py` (+ canonical
   claim cases in `test_hmo_canonical_wikidata.py`). Rule W-100 / studio R38.

### Skill: force-rebuild the Studio

1. UI: tick "Skip cache (force fresh build)" → Rebuild — starts
   `wikidata_studio_build` with `force_rebuild=true` and shows
   `JobProgressInline` (Rule W-106). Or enqueue the same kind via
   `POST /runs/{id}/jobs`.
2. This bypasses only the fingerprint cache — the inference cache
   (VIAF/authority/labels) is untouched. The fresh result is written back to
   `wikidata_studio_cache` so the next normal GET is fast.
3. If you see `cache_stale: true` on a normal GET, inputs changed since the
   last build — a plain Rebuild (no force) suffices.

### Skill: resolve blocked items

1. **Validator block** (`Blocked by validator (ERROR: CODE…)`): fix via item
   overrides — e.g. `NO_IDENTIFIER` → approve/repair the authority match so an
   external ID flows in; `TRAILING_PUNCTUATION`/`INVERTED_NAME_LABEL` → edit
   the label override — then Rebuild and re-run the dry-run.
2. **Reconciliation unavailable**: WDQS outage/429 — nothing to fix locally;
   retry when WDQS is reachable. Never work around by hand-creating the item.
3. **Rule-38 / foreign skip** (`refusing to modify QXXX` / not created by your
   account): default policy forbids community UPDATE. Either leave the item,
   or use the drawer foreign-accept checkbox bound to that QID (Rule W-99),
   then re-dry-run. Never CREATE a duplicate.
4. **Identifierless skipped persons** (not blocked): if they already have
   VIAF/NLI/QID in authority evidence, force-rebuild so W-188 can stamp
   P214/P8189 before omit. Remaining skips are name-only — approve a real
   Mazal/VIAF/QID in Authority Enrichment, then rebuild. Never CREATE a
   name-only person (W-154 / W-185).

### Skill: load all items in the modern review panel

1. The backend caps `page_size` at **500** on `GET …/wikidata-studio`.
2. Use `fetchAllStudioItems(runId, {approvedOnly, …})` from
   `frontend/src/api/wikidataStudio.ts` — it pages at `STUDIO_MAX_PAGE_SIZE`
   until `total` is reached. `WikidataItemsPanel` and pre-upload verify in
   `WikidataUploadPanel` both call this helper.
3. The legacy sidebar view still uses server-side pagination (50/page) via
   `WikidataStudio.tsx` — do not mix the two patterns on the same surface.

### Skill: measure Studio item quality locally

1. Run `backend/scripts/local_measure_verify.py --channel wikidata` against the selected run. It is read-only: it rebuilds in a scratch directory and does not change the database, cache, or Wikidata.
2. Compare item totals by entity type. A sudden loss of every authority-backed person indicates an input/join regression; fewer persons with explicit `no external identifier` or `unsupported role` skips is intentional fail-closed filtering, not a job failure.
3. Treat validator ERRORs, generic placeholder labels, missing source-specific descriptions, and unresolved local targets as build defects. Do not map an unfamiliar MARC role to a Wikidata property from memory; verify its live property semantics and entity-type constraints first.
4. A full semantic audit with the configured Tier-1 judge sends source-derived item and MARC evidence to that external provider. Obtain explicit curator approval before running it.

### Skill: apply AI autofixes

1. Open the item's compare panel (items with a QID get a `wikidata_live`
   snapshot) or run the `autofix_from_wikidata` action in the verification
   modal.
2. Only `confidence: "high"` fixes are merged (`merge_ai_fixes`) into an
   override PATCH via `POST …/items/{local_id}/apply-ai-fixes`; everything else
   stays a suggestion. Rebuild to see the applied state.
### Skill: audit a downloaded Wikidata export

1. Run the read-only checker against JSON or CSV; it never touches Postgres,
   caches, Wikidata, or the source file:
   `cd backend && .venv/bin/python -m scripts.check_wikidata_export_quality \
   /path/run-wikidata-approved.json --output /tmp/wikidata-quality.json`.
2. Read `counts` first, then inspect only the compact `findings` rows.
   `authority-approved-only` means the root `approved_only` filtered authority
   and NER inputs; it is not item approval or an AI pass.
3. For AI partial/fail rows, use `scripts/analyze_wikidata_verdicts.py`; pass the
   modern diagnostic export so complete `ai_verdict_json` and MARC context are
   available to the Codex CLI.

### Skill: verify enriched work metadata

1. Force-rebuild after changing approved authority/NER rows; the builder consumes content `author`/`work_author` and `wikidata_id`/`wikidata_qid` only at build time.
2. Diagnostic exports need P50/local/P2093 for author-bearing works and P1574 for approved matches; legacy files without `ai_verdict_json` must be re-exported and re-verified.


### Skill: audit a large MARC TSV before a Studio build

1. Run the streaming audit; it normalizes every record but retains only a
   bounded deterministic build sample:
   `cd backend && .venv/bin/python -m scripts.audit_marc_tsv_scale`
   `/path/manuscripts.tsv --sample-build 1000 --json /tmp/marc-scale.json`.
2. Require `normalization_errors=0` and `build_errors=0` before a large run.
3. Review only aggregate signal counts and bounded examples; the script never
   loads the full TSV into memory or emits the corpus to logs.

### Skill: inspect export label regressions

For a fresh export, group partial/fail verdicts by `entity_type` and inspect
English labels, P217, P195, and `property_label`/`value_label` nulls first.
Re-run the build after mapper changes because old JSON exports retain the old
labels and cannot validate the new projection.
