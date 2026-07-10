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
   (~380 writes) and item upload (~7800 writes) MUST NOT run inline in a request;
   dry-runs (no network) stay synchronous. *Why:* Heroku's 30s router timeout,
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
    `HMO_ITEM_VERDICT_SCHEMA` is `w52_v1` so a rebuild + re-verify invalidates
    stale verdicts without `override_cache`. *Why:* the run-`48ba6c13` fixup
    loop showed system-only/malformed labels drove most residual partials.
