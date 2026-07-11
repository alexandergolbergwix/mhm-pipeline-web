# Wikidata Studio — How it works: guards and upload

> Up: [Wikidata Studio](README.md)

*Continues from [build-and-cache.md](build-and-cache.md) (build pipeline,
overrides, validator moat).*

### Reconcile-before-create, fail closed

`_prepare_for_upload` (`wikidata_upload.py:157`) runs for **both** dry-run and
live upload, so the dry-run preview is truthful. Per entity type
(`_reconcile_for_upload`, `wikidata_upload.py:97`):

- **Manuscript** → `reconcile_manuscript_by_identifiers` (P3959 NNL catalog id
  first, then P217 shelfmark). P8189 was the historical silent-duplicate bug —
  manuscripts never carry it.
- **Person** → `reconcile_person_by_identifiers` (VIAF/NLI/LCCN/GND/ISNI) with
  cross-identifier conflict verification: a candidate holding a *different*
  value on any other identifier we propose is rejected (`reconciler.py:565`).
- **Work** → `reconcile_work_by_label_and_author`: SPARQL label match against
  `Q47461344` subclasses; a candidate whose P50 author differs is rejected as
  a different work (`reconciler.py:339`).

`reconciler._query` raises `ReconciliationUnavailableError` on any
network/429/5xx (`reconciler.py:147`) — never returns `[]` for an outage. The
item is then `blocked`, never created. After reconcile, `validate_item` runs;
any ERROR blocks the write regardless of create-vs-update or approval state.
`POST /reconcile` is a **preview only** — the authoritative reconcile always
re-runs inside `upload_items`.

### Upload job, moratorium, QS download

`POST /upload` (or the `wikidata_upload` run-job, which uploads item-by-item
with progress + cancel) builds native items fresh, optionally filters to
item-approved (`item_approved_only`), unwraps the user's encrypted Wikidata
token, and calls `wikidata_upload.upload_items`. Live mode with neither
`WIKIDATA_TEST_MODE=true` nor `MORATORIUM_LIFTED=true` returns every item as
`skipped` (`wikidata_upload.py:327`); the uploader itself independently
re-enforces the moratorium (`uploader.py:111`). All four desktop Rule-38
modification guards stay intact (`_is_our_item`, `_assert_modifiable` in
`_build_wbi_item`, `_would_create_identity_conflict` per statement, pre-write
`_assert_modifiable`).

`GET /quickstatements.txt` serves the cached QS blob (or an item-approved
filtered re-export). **Known residual gap**: QS lines carry raw CREATE
commands with *no* reconcile/validate gate — pasting into the QuickStatements
tool bypasses every guard above. `item_approved_only` is the only control.

### AI review + autofix

`wikidata_actions.py` registers two eval-agent actions surfaced by
`WikidataVerificationModal` (SSE stream, sessions under
`wikidata-verify-sessions/<run_id>/`, verdicts cached in `inference_cache`).
The worker, cache lookup, persistence, and merged review table all canonicalise
build `records` and fixture `record_ids` into the same MARC-aware verdict
fingerprint; otherwise stale sanitisation would hide a completed verdict.
The `wikidata_verify` job is committed before this scope is materialised, so a
large build cannot exhaust the 30-second `POST /jobs` router budget; the worker
surfaces an empty/invalid scope as a failed job instead of a stuck modal.
`wikidata_live_enrich.py` embeds a live-Wikidata compare snapshot
(`wikidata_entity_compare.build_compare`) into items that have a QID;
`wikidata_autofix_apply.merge_ai_fixes` converts only `confidence == "high"`
AI fixes into override fragments the curator applies via
`POST /apply-ai-fixes`. `WikidataComparePanel` renders the same diff for
manual apply.
