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
Then (live + dry-run with `enforce_ownership`) `wikidata_existence` confirms
the QID via batched Action API `wbgetentities` (50 ids, throttled 429 backoff;
Rule W-185) and classifies ownership with the curator token: **own → UPDATE**,
**foreign without accept → skip**, **foreign with QID-bound `accept_foreign_modify` → UPDATE (audited)**, **unknown →
block**. Upload jobs prefetch all native `existing_qid`s once into an
existence cache before the write loop. `None` from the Action API is retried
before fail-closed block; on test, confirmed-missing **and foreign-but-alive**
QIDs still clear to CREATE (Rule W-181) — never UPDATE a community test
Q-number (Rules W-183 / W-184). `POST /reconcile` is a **preview only** — the authoritative reconcile
always re-runs inside `upload_items`. Contract: `docs/wikidata-data-access.md`
(Rule W-99).

### Upload job, moratorium, QS download

`POST /upload` (or the `wikidata_upload` run-job, which uploads item-by-item
with progress + cancel) builds native items fresh via `_build_native_items`,
then stamps Studio-cache / probe-adopted `existing_qid`s
(`_apply_cached_qid_adoption_to_native`, Rule W-177) so identifier-matched
UPDATEs do not fall back to SPARQL CREATE. Before each write the job emits
`item_outcome.status=processing` (and `processing_local_id`) so the review
table shows a loading pill on the row under work; terminal outcomes replace
it without a full reload (Rule W-110). It constructs **one**
`WikidataUploader`, calls `ensure_authenticated()` once, and reuses it for
every item (Rule W-179); auth/session failures (`permissiondenied`, logged-out,
global block — Rule W-185) abort the remainder. Writes use
WikibaseIntegrator's `is_bot` kwarg (Rule W-180) with **default false**
unless `WIKIDATA_MARK_AS_BOT` / `mark_as_bot=True` (Rule W-181) — accounts
without the MediaWiki bot right hard-fail if `is_bot=True`. Upload natives
come from the **Studio cache** (same public set as the review table), not
an HMO-only rebuild. `_build_native_items` runs
`prepare_wikidata_upload_native_items` (recover trusted VIAF/NLI from HMO
and legacy evidence shapes, omit identifierless persons, rollup work P2093 —
Rules W-188 / W-185 / W-154). On
`upload_target=test`, QIDs missing on
test.wikidata.org, foreign-but-alive Q-numbers, and WDQS outages clear to CREATE rather than block
(live stays fail-closed). Test writes **remap** live P/Q to labeled test properties and class stubs
(exact-label search + optional CREATE, adopt-on-conflict; session maps warmed once per job).
Holder and runtime QIDs that are missing from `QID_LABELS` still stub
(institution table, live English label, or `MHM live {qid}`); a property
whose live English label already exists with the wrong datatype CREATEs
`{label} (MHM {datatype})`. Item CREATE label conflicts UPDATE our test Q
or uniquify the English description (Rule W-189).
After remap, leftover snaks whose datatype or
target still cannot exist on test.wikidata.org **refuse the item**
(`blocked`) — they are not stripped so a partial CREATE can succeed
(Rule W-186). Labels still go out only when the full remapped claim set
is writable (Rules W-182 / W-183 / W-186 / W-187). It optionally filters to
item-approved (`item_approved_only`), unwraps the user's encrypted Wikidata
token (also for dry-run when present, so ownership preview is truthful),
loads per-item foreign accepts from `WikidataItemOverride`, and calls
`wikidata_upload.upload_items`. The curator picks `upload_target` in the UI
(`dry_run` default | `test` | `live`). Live mode without `upload_target=live`
(and without legacy `MORATORIUM_LIFTED=true`) returns every item as `skipped`;
the uploader independently re-enforces via `allow_live` / env
(`uploader.py`). All four desktop Rule-38
modification guards stay intact (`_is_our_item`, `_assert_modifiable` in
`_build_wbi_item`, `_would_create_identity_conflict` per statement, pre-write
`_assert_modifiable`); foreign accept on **live only** via
`register_foreign_accept` (never cache priming; test ignores accept and CREATEs
instead of UPDATEing foreign-alive QIDs). Test Q remap reuses bot-owned items,
session stubs, or MHM stub descriptions (Rules W-184 / W-187).
The default source is the durable HMO Wikibase read-back. `native_items_from_hmo`
adapts normalized HMO labels, descriptions, aliases, accepted authority
evidence, and explicitly mapped Wikidata claims into the shared native item
model; direct uploads and `wikidata_upload` jobs preserve the selected source
before entering the same guarded uploader. Canonical QuickStatements are also
rebuilt through `prepare_items_for_export`, so export cannot bypass
reconciliation or validator blocking.

`GET /quickstatements.txt` serves a gated, source-scoped re-export by default;
blocked items are listed in the download header. An explicitly acknowledged
raw export remains available only with `gated=false&ack=raw`.

### AI review + autofix

`wikidata_actions.py` registers two eval-agent actions surfaced by
`WikidataVerificationModal` (SSE stream, sessions under
`wikidata-verify-sessions/<run_id>/`, verdicts cached in `inference_cache`).
Identical judge prompts reuse the content-addressed verdict cache
(Postgres + eval-agent on-disk); the modal leaves **Override cache** off by
default and surfaces cache-hit counts so curators see when the LLM was
skipped. Scope rows without a landed overall show a **judging…** pill while
the verify job is active. The worker, cache lookup, persistence, and merged
review table all canonicalise
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
manual apply. The diagnostic `GET …/items/export?format=csv` preserves the
item fields, linked MARC slice, validation issues, upload state, flattened
rubric fields, and complete `ai_verdict_json`, so non-passing rows can be
grouped into safe builder or rubric fixes without reconstructing the prompt.


Canonical reconciliation reads durable HMO entity rows first and only falls back to the legacy cache during migration.
