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
2. Read per-item outcomes: `exists` (would UPDATE via method), `success`
   (would CREATE), `blocked` (reconcile outage or validator ERROR — message
   says which). The dry-run reconciles and validates exactly like live.
3. No token, no moratorium flag, and no writes are involved in dry-run.

### Skill: force-rebuild the Studio

1. UI: tick "Skip cache (force fresh build)" → Rebuild; or
   `GET …/wikidata-studio?force_rebuild=true`.
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
3. **Rule-38 skip** (`refusing to modify QXXX`): the existing item wasn't
   authored by this user — edit it manually on Wikidata or leave it; the
   pipeline will not touch community items.

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
