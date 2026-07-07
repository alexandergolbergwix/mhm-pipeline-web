# Wikidata Studio — How it works: build and cache

> Up: [Wikidata Studio](README.md)

*Continued in [guards-and-upload.md](guards-and-upload.md) (reconcile, upload
job, moratorium, QS download, AI review + autofix).*

### Build pipeline + fingerprint cache

`GET /runs/{id}/wikidata-studio` (`wikidata_studio.py:643`) loads records,
matches, entity approvals, and overrides, plus `hmo_instance_qids_for_run`
(control_number → live HMO Wikibase QID, so P2888/P973 point at the uploaded
item instead of the static slug), then computes
`compute_build_fingerprint(...)` — a SHA-256 over every build input
(`wikidata_studio.py:84`). Cache logic:

- **Fingerprint match** → serve `WikidataStudioCache.result_items` instantly.
- **Fingerprint differs** → serve the stale cache with `cache_stale=true`; the
  curator must click Rebuild (no passive background job on page load). *(Note:
  this supersedes CLAUDE.md Rule W-26's "invalidated automatically" wording —
  the cache is *detected* stale automatically, but rebuild is curator-driven.)*
- **No cache row** → enqueue a `wikidata_studio_build` run-job and 409 with
  `{code: "studio_build_in_progress", job_id}`; the frontend attaches to the job.

`execute_studio_build` (`wikidata_studio.py:448`, also called by the job)
prewarms Hebrew transliterations (network disabled inside the sync builder),
runs `build_items_for_run` in a threadpool, applies curator overrides *before*
validation (`_build_sync`, `pipeline/wikidata_studio.py:223`), validates every
item, exports QuickStatements, stamps `local_id` + `approved` per item, and
upserts the cache row. `?force_rebuild=true` bypasses the fingerprint check
but still writes the cache. Server-side slicing (entity_type filter, search,
sort, pagination, max 500/page) is applied on the cached items.

### Item overrides, approval, statement exclude

`WikidataItemOverride` stores a sparse curator diff per `(run_id, local_id)`:
`labels` / `descriptions` / `aliases` (null clears a key), `add_statements`,
`remove_statements` (indices into builder output), `statement_edits`
(`{"<idx>": partial}`), and item-level `approved` (`None` = unreviewed).
`PATCH /items/{local_id}` merges partially and emits a versioning event
(`wikidata_override` entity type, Rule W-21). Overrides are re-applied at every
rebuild by `_apply_override` (`pipeline/wikidata_studio.py:482`) — edits first,
then removals, then appended statements. The inline ✗ Exclude button in the UI
just PATCHes `remove_statements` (Rule W-27). `local_id_for_item` gives the
stable handle: `item.local_id`/`id`, else `entity_type::<first external id or label>`.

### Validator moat layer

`item_validator.validate_item` maps each check to a named 2026-04 community
complaint. ERROR codes include: `EMPTY_LABEL`, `ANONYMOUS_PERSON`,
`KOVETZ_PLACEHOLDER`, `TRAILING_PUNCTUATION`, `INVERTED_NAME_LABEL`,
`INSTITUTION_AS_PERSON`, `P3959_ON_HUMAN`, `P8189_BAD_PREFIX`,
`P1559_LATIN_AS_HE`, `MULTIPLE_P1559_SAME_LANG`, `NO_IDENTIFIER`,
`HE_LABEL_IS_LATIN`, `EN_LABEL_IS_HEBREW`, `AMBIGUOUS_SINGLE_NAME`,
`P50_ON_MANUSCRIPT`, `P7416_AS_QUANTITY`, `BAD_VALUE_QID`, `P31_WRONG_QID`,
`P31_MANUSCRIPT_AS_HUMAN`, `P3959_ON_NON_MANUSCRIPT`; warnings include
`VIAF_ON_NON_PERSON` and `MISSING_P3959`. Known-bad QID tables
(`_KNOWN_BAD_P31_QIDS`: Q179808 Palme d'Or; `_KNOWN_BAD_VALUE_QIDS`:
Q21857942) pin past copy-paste catastrophes so they can never sneak back. The
validator runs in **two places**: build time (`validation_issues` per item →
UI badges) and as a **hard gate inside the upload path**.
