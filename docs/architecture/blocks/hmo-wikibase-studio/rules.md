# HMO Wikibase Studio — Rules

> Up: [HMO Wikibase Studio](README.md)

1. **R1 — Reconcile before create, fail closed.** Pass 1 MUST call
   `reconcile_item` before any `create_item`; a `ReconciliationUnavailableError`
   (network/HTTP failure) marks the item `failed`, NEVER "absent → create".
   *Why:* a transient SPARQL outage must not mass-duplicate items (same failure
   mode Rule W-30 closed on the Wikidata side).
2. **R2 — Never hold a DB transaction across a slow external call (Rule W-40).**
   `reconcile_item` commits its PID SELECT before the SPARQL call when `pid` is
   not pre-resolved; loop callers MUST pre-resolve via `resolve_source_uri_pid`.
   *Why:* Postgres' 2-minute idle-in-transaction backstop would kill the
   connection mid-job during the writer's ~61s retry backoff.
3. **R3 — Per-write commit for crash-safe resume.** Every successful schema
   create and item create/adopt MUST commit its `wikibase_entity_mappings` row
   immediately, and re-runs MUST skip already-mapped URIs.
   *Why:* resuming after a crash mid-batch must never re-create a live entity.
4. **R4 — Live batches run as `run_jobs` background jobs.** Schema bootstrap
   (~380 writes), item upload (dry-run + live), and IIIF manifest upload MUST
   NOT run inline in a request (Rule W-107). *Why:* Heroku's 30s router timeout,
   plus jobs give progress, dedup (409-attach), and cancellation.
5. **R5 — Update is a merge, never a wipe.** `update_item` uses
   `ActionIfExists.APPEND_OR_REPLACE`; a curator-added statement on the wiki
   that is absent from the build MUST be left untouched. *Why:* the upload must
   never destroy manual curation on the live instance.
6. **R6 — Deferred links are reported, never dropped.** Pass 2 MUST emit an
   `unresolved` outcome for any link whose target lacks a live QID.
   *Why:* silently missing item→item claims are invisible data loss.
7. **R7 — Every on-disk build cache needs a durable Postgres counterpart
   (Rule W-39).** Coverage reports, item builds, and TTLs MUST write through to
   `HmoCoverageCache` / `HmoStudioItemCache` / `RdfArtifact`. *Why:* Heroku
   dyno restarts wipe local disk; a disk-only cache silently forces 9–14 minute
   rebuilds of unchanged inputs.
8. **R8 — Build cache is fingerprinted by TTL bytes AND schema-mapping
   version.** Never serve a cached item build after a schema bootstrap changed
   the mappings. *Why:* previously-unresolvable statements may now resolve (or
   resolve to different PIDs).
9. **R9 — Truncate before write.** Labels/descriptions are capped at 250 chars
   and string/monolingualtext claim values at 400 in `hmo_exporter.py` — new
   value paths MUST go through `_truncate`. *Why:* overlong values fail the
   whole entity write with `ModificationFailed`.
10. **R10 — The writer's OAuth session MUST be the configured write user.**
    `build_server_wikibase_writer` 503s on a username mismatch. *Why:* all wiki
    history must attribute to `mhm-pipeline-web`; a consumer registered under a
    personal account would silently mis-attribute every edit.
11. **R11 — Every live write is audited; audit never breaks the write.**
    `record_wikibase_write` (and versioning `apply_event` calls) MUST swallow
    their own failures. Manifest uploads audit *intent* before the network call.
    *Why:* forensic recovery needs the trail, but an audit-table hiccup must
    not 500 a batch.
12. **R12 — Batch loops never let one bad item kill the batch.** Writer entity
    methods return `status="failed"` outcomes instead of raising; manifest
    upload catches per-file. *Why:* one malformed record must not abort
    thousands of good writes.
13. **R13 — SHACL tooling failures degrade; violations gate uploads (R19).**
    `build_shacl_report_for_items` logs and returns an empty report when pyshacl
    cannot run — the review UI must stay usable. That is NOT permission to ignore
    violations once a report exists.
14. **R14 — wikibase.cloud is not wikidata.org.** The Rule 25 moratorium and
    Rule W-30 Wikidata upload gates do NOT apply here, but HMO has its own SHACL
    gate (R19) and the same reconcile/validator discipline.
15. **R15 — Byte-identical vendoring.** `backend/converter/wikibase/` mirrors
    the desktop tree; sync via `pipeline/scripts/sync_converter_to_web.sh`,
    never edit divergently. *Why:* one source of truth for writer behavior.
16. **R16 — Adopt is audited distinctly from create.** A reconcile-match hit
    MUST log `OPERATION_ADOPT`, never `OPERATION_CREATE`. *Why:* the review
    table's "Last upload" column reads this to tell curators "linked to a
    pre-existing item" apart from "brand new" — collapsing them makes every
    corpus with pre-existing Wikibase items look like it minted duplicates.
17. **R17 — One function writes an item live.** `push_single_item` is the
    only place that performs a live create/update of an entity; the bulk
    pass-1 loop and the single-item push endpoint both call it. *Why:*
    letting the single-item endpoint grow its own create/update body would
    silently diverge from R1/R2/R3/R5/R9/R12 the next time one path changes.
18. **R18 — Upload-lifecycle AI verification never gates or fixes silently.**
    A failed pre-upload verdict MUST show a curator a choice (review vs.
    upload anyway), never auto-block or auto-proceed; a post-upload verdict
    only proposes a fix, the curator applies it (mirrors eval-agent R13).
    *Why:* project-wide human-in-the-loop invariant — AI verdicts are
    advisory everywhere in this app.
19. **R19 — SHACL Violation/Error blocks live upload by default.** Pass 1 and
    `push_single_item` consult `HmoStudioItemCache.shacl_report` via
    `blocking_shacl_issues` (`hmo_item_shacl_gate.py`); blocked items get
    `blocked`/`would_block` outcomes and never call Wikibase unless the curator
    sets `allow_shacl_errors=true`. *Why:* 2026-07 audit — 773/2131 items with
    SHACL violations were created because the write path ignored the report.
20. **R20 — Never emit `und` language codes to Wikibase.** Labels/descriptions
    are sanitized in `hmo_exporter.py`, `label_sanitize.py`, and
    `cloud_client._apply_labels_descriptions_aliases` before every write. *Why:*
    Wikibase Cloud rejects `und` (`not-recognized-language`), failing whole items.
21. **R21 — ParadigmBridge is RDF-only, not a Wikibase item.** Exporter skips
    `HM.ParadigmBridge` instances; bridge semantics stay in TTL + pass-2
    deferred links. Cataloging views MUST carry `hm:view_type`; RDF build dedupes
    duplicate `hm:external_identifier_nli` literals per node.
22. **R22 — Schema datatype inference is ontology-faithful (Rule W-47).**
    `ontology_schema_reader._infer_datatype` maps OWL ranges to Wikibase
    datatypes; URI reconciliation anchors (`hmo_source_uri`) use `url`;
    folio designations stay `string`; CIDOC object properties stay
    `wikibase-item`. AI schema verify enriches fixtures with OWL metadata before
    judging. *Why:* 2026-07-08 export had mass false partials/fails from judge
    blindness to descriptions and Wikidata-centric datatype assumptions.
23. **R23 — Every built item carries manuscript scope for AI verify (Rule W-48).**
    `HmoWikibaseExporter` MUST stamp `control_numbers` via incoming-edge RDF
    BFS (not URI-regex alone) and persist them on `ResolvedWikibaseEntity`;
    generic `… in the Hebrew Manuscripts Ontology (HMO)` descriptions MUST be
    avoided by stamping `rdfs:comment` at RDF build for primary scholarly
    entities. *Why:* export (4) on run `48ba6c13` still had 792 items with no
    MARC join and 1283 generic descriptions → mass AI verify fails/partials.
24. **R24 — Expression/505 label hygiene + vocabulary comments (Rule W-50).**
    `parse_contents_entry()` splits numbered 505 rows; Expression labels omit
    `(in MS …)`; genre/subject/material/script nodes get `rdfs:comment` at
    build. *Why:* export (4) had ~149 partials from 505 prefixes + `(in MS …)`
    in labels and 52+ generic SubjectType descriptions.
25. **R25 — Export quality gate is a hard block (Rule W-52).**
    `hmo_export_quality.audit_entity_drafts` + `assert_export_quality` raise
    before caching on any issue: blank-node export, Hebrew-in-`en`, generic
    description, work missing MS scope, missing label/description, and the
    W-52 codes `production_missing_label`, `timespan_bare_label`,
    `latin_label_in_he`, `unbalanced_label_quotes`, `witness_unusable_title`.
    `HMO_ITEM_VERDICT_SCHEMA` is `w53_v1` so a rebuild + re-verify invalidates
    stale verdicts without `override_cache`. *Why:* the run-`48ba6c13` fixup
    loop showed system-only/malformed labels drove most residual partials.
26. **R26 — Honest-negative + second-pass hygiene gate (Rule W-53).**
    `hmo_export_quality` adds `production_description_repeats_label` (blocks an
    `E12_Production` whose description is the label-repeating template rather
    than a grounded honest negative). Pairs with the RDF-graph R17 build fixes
    (Ibn uninversion, 710-person, grounded Production/PU comments, ISBD/pipe/vav
    label hygiene, place comments) and the eval-agent rubric 3d/3f/3g additions.
    `HMO_ITEM_VERDICT_SCHEMA` → `w53_v1`. *Why:* export (5) on run `48ba6c13`
    had 155 partial / 4 fail dominated by thin/malformed labels + person typing.
27. **R27 — Reconcile uses the instance direct-property URI, both namespaces
    (Rule W-56).** `hmo_item_reconcile.py` builds `{wikibase_cloud_base_url}/
    prop/direct/PNNN` (never `wdt:`, which resolves to Wikidata on
    wikibase.cloud → matched nothing) and matches `hmo_source_uri` in both the
    current w3id and legacy `ontology.org.il` namespaces via a `VALUES` clause,
    so a Rule-W-55 namespace migration never orphans a live item into a
    duplicate create. The `scripts/migrate_hmo_source_uri_namespace.py`
    dry-run-first migrator swaps the prod `wikibase_entity_mappings` (required)
    + live wiki `hmo_source_uri` (optional after R27). *Why:* the migration
    audit found reconcile had silently never matched (failing open to create).

27. **R27 — Wikibase QIDs and Wikidata QIDs are different namespaces.** HMO
    item `wikibase_id` values are local project identifiers; external Wikidata
    and VIAF authority values must be carried as explicit claims/identifiers.
28. **R28 — Item builds fail closed on every unmapped ontology URI.** The
 exporter MUST resolve the class URI, every statement predicate, and the
 `hmo_source_uri` reconciliation predicate against a schema-level mapping
 before caching any item build. Missing mappings raise
 `UnmappedOntologyUriError`; they must never become skipped claims. *Why:*
 omitting the source-URI predicate produces items that cannot be reconciled
 safely and hides ontology/schema drift until upload time.
29. **R29 — Canonical persistence follows both upload passes.** The upload
 pipeline MUST write and validate deferred item links before reading back or
 replacing durable canonical entities. Any unresolved, failed, or cancelled
 pass-2 operation leaves the run non-canonical; it must not publish a
 pass-1-only snapshot. *Why:* persisting before pass 2 silently omits
 relationships that were added moments later and can promote incomplete HMO
 state to downstream projections.


35. **R35 — Canonical persistence is all-or-nothing after live read-back.**
Upload mappings are keyed by source URI; every successful item must be read back
and normalized before any `hmo_canonical_entities` replacement. Missing QIDs,
read-back timeouts, failed/blocked writes, and duplicate identities keep the
canonical table unchanged and block the readiness gate. Scalar datatype retries
are limited to explicit legacy string-property errors; remote timeouts are not
blindly retried. *Why:* the first production migration silently produced zero
canonical rows and then hung on a Wikibase call, so a partial projection could
never become the source of truth.

36. **R36 — Serialize unsupported boolean claims before the live call.**
`wikibase.cloud` has no boolean snak type. Item create/update paths MUST route
boolean claims through the explicit string representation before calling the
remote writer, while preserving valid time and monolingual-text claims. *Why:*
the first production retry still sent a doomed whole-item boolean request for
every item, producing thousands of avoidable API errors and extending the
migration window.

37. **R37 — Canonical projections use durable live read-back only.** Canonical
 RDF and Wikidata/QuickStatements builders MUST read `hmo_canonical_entities`
 and MUST fail closed when rows are missing, malformed, incomplete, or
 duplicated. `HmoStudioItemCache.canonical_live` is a compatibility/read-model
 copy, never a source-of-truth fallback. Shadow differences must be classified;
 blocking or unclassified differences cannot promote a projection. *Why:* a
 transient cache snapshot can outlive or diverge from the all-or-nothing live
 Wikibase read-back and would silently reintroduce pre-canonical data.

38. **R38 — Canonical readiness is one integrity contract.** The durable gate,
projection gate, and production E2E audit MUST use the shared readiness result,
which reports expected and durable counts, missing/malformed rows, duplicate
local IDs/source URIs/QIDs, authority conflicts, stale or missing fingerprints,
and live read-back failures. A matching row count alone MUST NOT promote a
canonical cohort. *Why:* count-only checks can certify a corrupted or stale
read-back set as canonical.

39. **R39 — HMO creation must pass authority and read-back identity gates before
replacement.** Uploads MUST run the approved-row authority gate before any
external write. Every mapped live item MUST read back with the same QID, and
duplicate local IDs, source URIs, or Wikibase QIDs MUST abort before replacing
`hmo_canonical_entities`. *Why:* an authority collision or mismatched live
identity can otherwise create an irreversible false link while leaving a
plausible canonical row behind.

40. **R40 — Schema drift is observable and fail-closed.** The schema mirror
report MUST expose label and datatype drift; a changed property datatype is a
migration signal, not permission to mutate the live property in place. *Why:*
Wikibase rejects datatype mutation and silently refreshing the local row hides
an unsafe schema mismatch.
41. **R41 — Entity writes are throttled by configuration.** The writer MUST
apply the configured minimum interval to item/property/claim writes while
leaving reads unaffected. *Why:* retry backoff handles failures after they
occur; it does not protect a large sequential batch from rate-limit bursts.
42. **R42 — Blank-node IDs are graph-derived.** Typed blank nodes exported as
items MUST use a deterministic graph fingerprint, never rdflib's parse-local
blank-node identifier. *Why:* re-parsing or reordering a graph must not mint a
different local identity for the same described node.
43. **R43 — Deferred links may cross run boundaries.** Pass two MUST preserve
external target source URIs and consult all non-null-run instance mappings;
unmapped targets remain explicit `unresolved` outcomes. *Why:* a person or
work shared by two runs must not lose its relationship because it was absent
from the current build batch.
44. **R44 — Authority refresh on build-items MUST rebuild RDF (Rule W-101).**
When `refresh_authority=true`, the `hmo_item_build` worker
(`execute_hmo_item_build`) re-enriches matches, rebuilds the TTL from approved
rows, upserts `RdfArtifact`, and force-rebuilds the item cache. *Why:* otherwise
Mazal/KIMA/VIAF/Wikidata payload updates stay invisible behind a stale on-disk
or Postgres TTL.
45. **R45 — Four HMO pillars (Rule W-102).** (1) Canonical live Wikibase entities
are the post-upload root for RDF/Wikidata projections. (2) Public Wikidata P/Q
map only through ontology equivalents + `hmo_wikidata_pq_mapper`. (3) Schema and
emitted `hm:` terms stay 1:1 with `hebrew-manuscripts.ttl`. (4) Entities are
enriched from Mazal/KIMA/VIAF/Wikidata under fail-closed matching. *Why:* these
four constraints are the scholarly contract of the browser deployment.
46. **R46 — HMO item AI verify injects WPM + HMO→Wikidata skill context (Rule W-104).**
`hmo_wikibase_item` / autofix prompts include the curated skill pack with an
explicit projection checklist; structural entities stay HMO-only. Salt
`HMO_ITEM_VERDICT_SCHEMA=w124_v1`. *Why:* the goal is Wikibase items that
project to the best possible public Wikidata items under WikiProject Manuscripts.
47. **R47 — Item and manifest builds are background jobs (Rule W-106).**
`POST …/build-items` and `POST …/build-manifests` MUST enqueue
`hmo_item_build` / `hmo_manifest_build` and return immediately; live progress
uses `JobProgressInline`. *Why:* authority+RDF+export and IIIF generation
exceed Heroku's 30s router budget.
48. **R48 — Item and manifest uploads are background jobs (Rule W-107).**
`POST …/upload-items` and `POST …/upload-manifests` MUST enqueue
`hmo_item_upload` / `hmo_manifest_upload` for dry-run and live; missing item
build → 409 before enqueue. *Why:* sequential Wikibase writes and large
dry-run walks exceed the router budget.
49. **R49 — Authority ID collisions resolve in HMO Studio (Rule W-109).**
`GET/POST …/hmo-studio/authority-conflicts` lists and resolves approved
Mazal/Wikidata/VIAF collisions with versioned unapprove; do **not** reopen
retired Authority mutation routes (W-93). *Why:* upload gate (W-95) blocked
publish with no curator path after Authority retirement.
50. **R50 — Failed-item retry MAY scope upload via `local_ids`.**
`POST …/upload-items` accepts optional `local_ids`; pass 1 uploads only those
drafts; pass 2 still links deferred claims that touch the scope. The UI
**Retry N failed** button uses this. *Why:* a full re-walk of thousands of
already-mapped items is unnecessary after a SPARQL/transient failure.
51. **R51 — Live upload progress MUST patch changed review rows; quantity→string
snak mismatches MUST fall back.** `hmo_item_upload` streams
`item_outcome` + `recent_item_outcomes` in job progress after each pass-1
write. `ItemUploadPanel` applies those deltas via `onUploadOutcomes` /
`studioUploadProgress` (no mid-run full list reload / table unmount).
Terminal upload still silent-reloads. Bulk approve / verify may silent-reload
(throttled) while keeping the table mounted. Publication status shows
**failed** when `upload_outcome === failed`. `max_nesting_depth` exports as
string to match live P224; any remaining ``expected string`` scalar failure
retries via `_string_compatible_claim`. *Why:* full-table reload flickered
Publication status during publish (Rule W-110), and Hierarchy creates failed
on quantity snaks.
52. **R52 — URL claims MUST strip MARC quote wrappers before Wikibase write
(Rule W-111).** `restriction_url` (and every other `url` datatype claim) is
cleaned via `clean_url_value` at RDF emit, HMO export, and `_build_live_wbi_claims`
so a cached dirty build can still **Retry failed** without waiting on RDF
rebuild. *Why:* WBI rejects `Invalid URL "http://…"` when 540/939 `$u` wrappers
reach `datatypes.URL`.
53. **R53 — HMO item build progress MUST use 1-based pipeline steps (Rule W-112).**
`execute_hmo_item_build` reports `1/3` authority → `2/3` RDF → `3/3` export
with `unit=steps` and "Step N of 3" messages. Never leave an active step at
`0/3`. Job tray/inline UI show the unit + message. *Why:* skip-cache rebuild
looked stuck at `0/3` during the long authority refresh.
54. **R54 — Long HMO build steps MUST nest sub-progress (Rule W-113).**
Authority refresh reports `sub_processed`/`sub_total`/`sub_unit=entities`;
RDF rebuild reports `sub_unit=records`. `JobProgressInline` and the job tray
show a second bar when `sub_total > 0`. *Why:* `1/3 steps` alone hid hours of
entity matching as a full bar with no movement.

**Canonical fingerprint stability (Rule W-137 follow-up).**
`canonical_entity_fingerprint` coerces scalars/mappings/sequences to their empty
form before hashing: an absent `control_numbers` and an empty one are the same
state and must hash the same, otherwise fingerprinting a raw read-back snapshot
and re-fingerprinting it after `normalize_live_entity` disagree and the readiness
gate (R-W-94) reports a stale fingerprint for an unchanged item.
