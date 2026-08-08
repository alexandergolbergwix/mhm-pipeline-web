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

The build schema is part of the fingerprint. Schema `source-aware-works-v5`
also treats a cached person carrying an ERROR-level `NO_IDENTIFIER` issue as
stale (`wikidata_studio.py:50`). The current builder either emits an external
identifier or omits the person, so this shape can only come from an older
cache. GET marks that cache `cache_stale=true`; the curator must force-rebuild
before reviewing or uploading it.

Canonical enrichment applies a second notability boundary after matching
the HMO projection with fresh MARC/authority items. `merge_legacy_into_canonical`
omits canonical or unmatched legacy persons unless the final item has an
existing QID or a publishable P214/P8189/P244/P227/P213/P268 statement
(Rule W-154). The canonical assembler repeats that boundary after curator
overrides and reconciliation, immediately before validation and cache
persistence, so a person cannot be reintroduced by a later build step.

The cache is source-scoped (`legacy` or `canonical`). Every cache lookup and upsert includes the source, and the source is included in the `wikidata_studio_build` job parameters, so a forced canonical rebuild cannot silently return or overwrite the legacy row; old cache rows with a null source are treated as legacy for compatibility.

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

Canonical source mode reads durable `hmo_canonical_entities` rows first; the per-run HMO cache is only a migration fallback. This keeps Wikidata Studio projections independent of cache retention. Claim predicates and item values are translated to public Wikidata P/Q only through `hmo_wikidata_pq_mapper` (ontology local-name / ledger URI → allowlist; bare project Cloud QIDs never become Wikidata values — Rule W-100).

**Canonical enrichment (Rule W-125):** before validation, `execute_studio_build`
also runs the legacy MARC/authority builder (`build_items_for_run`,
`return_native=True`) and merges those claims onto HMO-rooted items via
`wikidata_canonical_enrichment.merge_legacy_into_canonical`. Canonical
`local_id`, P2888/P973 bridges, and `existing_qid` win; research claims
(P571/P1071/P407/P1574/P217/P195/…, person dates/VIAF/Mazal, work P50)
are unioned. Build fingerprint salt `hmo-wikidata-v9` includes the MARC/
authority enrichment fingerprint so either input invalidates the cache.
Summary flag: `legacy_enriched`; `projection_source` becomes
`hmo_wikibase+marc` when enrichment applied.

Final canonicalization is source-scoped and runs after overrides and person
publishability filtering (Rule W-155). Hard-rejected authority matches cannot
reintroduce biographical dates or a public person item; broad `P921` values
and `P195` values without a verified current-holder match are removed. Only
after that pass does the builder resolve `__LOCAL:` targets, so no surviving
statement points at a person or work that the public item set omitted. Accepted
work evidence carries its source MARC control number for AI verification.

**Canonical rollup (Rule W-117):** only `manuscript` / `person` / `work` become
Studio items. HMO classes with `summarized_in_wikidata` strategy contribute
allowlisted claims onto the parent item when they share a control number
(`hmo_canonical_wikidata._rollup_sources_for`). Build summary reports
`rolled_up_entities` and `summarized_hmo_nodes`.

The HMO-to-Wikidata boundary is deliberately narrow. `hmo_instance_qids_for_run`
reads the run upload ledger, and `hmo_wikidata_projection` accepts a link only
when the ontology URI exactly matches the canonical `MS_<control-number>` URI
and the local QID is syntactically valid. Control numbers are canonicalised at
this lookup boundary, including MARC values stored with surrounding quotes.
Conflicting QIDs for one manuscript are omitted. The builder emits the real
HMO Wikibase `/wiki/Item:Q<n>` URL; it never treats a local QID as a global
Wikidata QID and falls back to the stable slug when no safe HMO mapping exists.

### Item overrides, approval, statement exclude

`WikidataItemOverride` stores a sparse curator diff per `(run_id, local_id)`:
`labels` / `descriptions` / `aliases` (null clears a key), `add_statements`,
`remove_statements` (indices into builder output), `statement_edits`
(`{"<idx>": partial}`), item-level `approved` (`None` = unreviewed), and
optional `accept_foreign_modify` + `accepted_foreign_qid` (Rule W-99 —
explicit per-QID permission to UPDATE a community Wikidata item).
`PATCH /items/{local_id}` merges partially and emits a versioning event
(`wikidata_override` entity type, Rule W-21). Overrides are re-applied at every
rebuild by `_apply_override` (`pipeline/wikidata_studio.py`) — edits first,
then removals, then appended statements. The inline ✗ Exclude button in the UI
just PATCHes `remove_statements` (Rule W-27). The drawer foreign-accept
checkbox PATCHes the accept pair. `local_id_for_item` gives the
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

The builder keeps catalog identifiers in P3959/source metadata rather than public labels, and each person item records compact `authority_evidence` (VIAF/NLI identity, preferred names, and dates). Verification and `fetch_merged_wikidata_items` both run `attach_local_reference_targets` on the complete item set before fingerprinting or stale sanitisation, so internal `__LOCAL:<id>` evidence cannot make persisted verdicts disappear. Work items and source manuscripts carry `work_candidate_evidence`; a new work without accepted evidence fails validation. Before the eval-agent fixture write, `wikidata_verify_evidence.enrich_items_with_verify_evidence` attaches a multi-channel `verify_evidence` pack (MARC + VIAF + Mazal + existing Wikidata + HMO Wikibase) and `attach_wikidata_marc_context` ensures the same slice used by cache keys (Rule W-124). Verify fetch canonicalises quoted control numbers so Studio `record_ids` still join run MARC. The evaluator prompt surfaces each pack plus the WikiProject Manuscripts skill block. `w124_v1` / `records_marc_v6` keys include the evidence pack, work evidence, prompt-visible statement labels, qualifiers, references, authority, local targets, and the MARC slice. Static genre/subject QIDs require verified `QID_LABELS`; uncertain mappings and broad `Jews` P921 claims fail closed.

### Semantic projection safety

Work projection uses `work_candidates.assess_work_candidate`: clean Hebrew
MARC 505 titles are structural contents evidence; MARC 500 titles require the
semantically anchored named-work parser; rejected NER stays rejected; Latin-only
505 headings require authority/QID evidence. Accepted and rejected decisions
retain source field/text, folio, sequence, and reason. Embedded authors are
removed from public labels and used only for exact author linking; works do not
inherit manuscript P407. Hebrew work labels receive English only from trusted
catalog romanization. Other R21 authority/person/manuscript semantic gates remain
fail-closed.

### Modular builder boundary

`item_builder.py` preserves the public `WikidataItemBuilder` API and owns only shared state, orchestration, reconciliation, and compatibility exports. Projection behavior is split by entity concern: manuscript core, manuscript metadata, contents/subjects, person linking, person construction, and work construction. The extracted mixins call the same helpers and emit the same `WikidataItem`/`WikidataStatement` models, so the cache, validator, QuickStatements exporter, uploader, and web serialization contract remain unchanged.
### Export contracts and work identity

The legacy section export (`/wikidata-studio/export`) rebuilds the same source
items but now stamps each row with item-level `approved`, `ai_verdict`, and
`ai_verdict_at` from `WikidataItemOverride`; its root `approved_only` remains
the authority/NER input filter and `approval_scope` states that explicitly. CSV
rows retain records, statements, validation issues, authority evidence, work
candidate evidence, and complete verdict JSON. Work projection resolves only exact
verified aliases, approved content-level work QIDs, exact authority author QIDs,
or safe P2093/local-person fallbacks; it never fuzzy-matches a title. Contents-NER
`author`/`work_author` metadata is consumed before title splitting so source-backed
authors cannot disappear between enrichment and item construction.


When `HMO_CANONICAL_FIRST=true`, the Wikidata Studio build route defaults legacy requests to the durable canonical HMO projection. The flag is the rollout boundary; explicit legacy mode remains available while it is disabled.
AI verification loads and the read path retains the same curator
override-merged item state used by the
review view before computing verdict fingerprints (R65). This keeps labels,
descriptions, aliases, and statement edits from making a freshly judged item
appear stale on the next table read.
Derived ledger QIDs and local-reference display labels are excluded from this
stable validation boundary (R66).
The read path calls the same `slim_item_for_verdict_persist` projection used
by write-through persistence before comparing stable keys (R67).
Stable keys also omit display-only labels generated for `__LOCAL:` targets, so
subset verification and full-table reads share the same durable item state
(R68).
Before stale sanitisation the merge path also replays cached QID adoption and
live value-label glosses so the table hashes the same item state verify wrote
(R72 / Rule W-169).

### Corpus-scale projection + sticky-full verify (Rule W-171)

Mode-α projection uses shared predicates (`isbd_title`, `work_link_specificity`,
`identity_compatible` at person-link emit, leaf-unit extent, incipit gate,
per-record work-title alias subtraction, century vs embedded CE year) — never
control-number allowlists. Verify scope partitioning
(`wikidata_verify_scope.partition_wikidata_verify_cache`) reuses a prior
`full`/`pass` verdict when the schema-agnostic `claims_fingerprint` still
matches (sticky-full), so a payload-neutral schema bump does not re-judge
unchanged fulls. Override merge carries `ai_verdict` for that fallback;
`override_cache`, claim edits, and duplicate-class transitions still re-judge.
