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
sort, pagination, max 500/page) is applied on the cached items. The modern
**Items panel** (`WikidataItemsPanel`) loads the full corpus for client-side
table filters by calling `fetchAllStudioItems` — repeated `GET`s at
`page_size=500` until `total` is satisfied (never exceed the API cap in one
request).

Before grouping build inputs, `build_items_for_run` runs every MARC record, approved authority match, and approved NER entity key through `canonical_control_number`. This makes harmless storage formatting such as surrounding quotes or whitespace equivalent to the record’s clean 001. The normalisation is a build-boundary requirement: grouping any one source by its raw control number can silently remove its authority/person or NER/work projection.

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


### Verification evidence contract

The builder keeps catalog identifiers in P3959/source metadata rather than public labels, and each person item records compact `authority_evidence` (VIAF/NLI identity, preferred names, and dates). `_fetch_wikidata_verify_items` retains exact `record_ids` and annotates any resolvable `__LOCAL:<id>` statement with `local_reference_targets`; this is internal two-pass upload syntax, not a malformed Wikidata QID. The build schema marker is bumped when these fields change so a Rebuild cannot serve stale item shapes. The evaluator context also includes contents, work mentions, genres, catalog fields, and related works; cache schema/version salts change whenever this evidence contract changes.


### Semantic projection safety

Structured `contents` work mentions are emitted only for known Wikidata works or curator-approved authority matches; unapproved NER work rows are ignored. Hebrew work labels receive an English label only when the record contains trusted catalog romanization fields. Authority rows marked corporate or sourced from organization MARC tags are never emitted as unresolved human items, and person dates are accepted only from the exact authority-ID match. Subject network lookup and genre inference are disabled by default. Manuscript notes are not emitted as P7535/P5008/P17/P131 claims; P195 is asserted only when the source holder supports it. These gates keep the builder fail-closed when MARC text is ambiguous.

### Modular builder boundary

`item_builder.py` preserves the public `WikidataItemBuilder` API and owns only shared state, orchestration, reconciliation, and compatibility exports. Projection behavior is split by entity concern: manuscript core, manuscript metadata, contents/subjects, person linking, person construction, and work construction. The extracted mixins call the same helpers and emit the same `WikidataItem`/`WikidataStatement` models, so the cache, validator, QuickStatements exporter, uploader, and web serialization contract remain unchanged.
