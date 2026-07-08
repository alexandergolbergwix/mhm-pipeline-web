# RDF / HMO-Ontology Graph Build — Rules

> Up: [RDF / HMO-Ontology Graph Build](README.md)

1. **R1 — Only approved rows reach the graph.** Authority matches and NER
   entities MUST be filtered on `approved IS TRUE` before merging
   (`rdf.py:271`, `rdf_build_job.py:58`). *Why:* unvetted candidates must
   never produce `owl:sameAs` / external-ID triples in a scholarly artefact.
2. **R2 — Curator overrides win.** `override_text/type/role` on
   `ExtractionApproval` MUST take precedence over model predictions, and
   `RdfTripleOverride` rows are re-applied after every rebuild. *Why:* the
   curator's decision is the ground truth (Rule W-24).
3. **R3 — Provenance-event nodes are gated on coordinates.** NEVER emit an
   event/place node without plausible lat/lon (`graph_builder.py:749`).
   *Why:* the maps consume these places; a fabricated point is a silent
   scholarly error (Rule W-32 / desktop Rule 60).
4. **R4 — Every build write-throughs `RdfArtifact`.** Any path that writes
   `manuscripts.ttl` MUST upsert the TTL into Postgres in the same
   request/job, and read paths MUST call `ensure_ttl_on_disk` before touching
   the file. *Why:* Heroku's slug filesystem is ephemeral; on-disk-only state
   evaporates on every deploy (Rule W-39 — `RdfArtifact` is the precedent).
5. **R5 — `backend/converter/` is a byte-identical mirror.** NEVER edit
   vendored converter files directly in the web repo; change the desktop
   repo and re-run the sync. *Why:* the two ports must produce identical RDF;
   drift makes desktop↔web comparisons meaningless (Rule W-10/W-34).
   **Known exception (as of 2026-07-07, Rule W-43):** the desktop repo's
   `converter/rdf/graph_builder.py` and several `converter/transformer/*.py`
   files currently carry unrelated, untested WIP changes, so a full
   `sync_converter_to_web.sh` run would delete web-imported modules and pull
   in failing code. The Rule W-43 fix was hand-ported (same four edits, both
   repos) instead of synced; run the real sync once the desktop WIP lands
   and passes tests, then delete this note.
6. **R6 — Canonical ontology lives in the desktop repo.**
   `backend/ontology/hebrew-manuscripts.ttl` and `shacl-shapes.ttl` are
   copies made at sync time; MUST NOT be hand-edited here. *Why:* Rule W-34
   names `pipeline/ontology/hebrew-manuscripts.ttl` as canonical.
7. **R7 — Builds bust every downstream cache.** After a rebuild, delete
   `graph_*.json` / `graph_viewport_*.json` and call
   `research_graph.invalidate_cache(run_id)`. *Why:* stale Cytoscape/index
   caches would show the old graph after the curator explicitly rebuilt.
8. **R8 — The mapper never blocks the event loop.** All rdflib/pyshacl work
   runs through `asyncio.to_thread`. *Why:* a 100-MS build takes seconds;
   blocking would freeze every other request on the dyno.
9. **R9 — Coverage report failures are non-fatal.** Projection/ontology
   coverage writers are wrapped in try/except and only logged
   (`rdf_build.py:385-417`). *Why:* reports are diagnostics; they must never
   fail a successful graph build.
10. **R10 — Geography-tab parity requires a rebuild.** Runs enriched after
    an RDF build MUST be rebuilt for new places/provenance events to appear
    in the graph-sourced Geography surfaces. *Why:* the TTL is a snapshot of
    approvals at build time (Rule W-32 note).
11. **R11 — SHACL build validation MUST use `inference="none"`.** NEVER pass
    `inference="rdfs"`/`"owlrl"`/`"both"` to `ShaclValidator.validate()` in
    `_run_shacl_sync`. *Why:* the ontology reuses properties across multiple
    hierarchy levels by design; RDFS inference cross-types nodes from
    `rdfs:domain` axioms that only name the *primary* class, then validates
    them against unrelated shapes — 1319 false-positive violations on one
    real corpus, zero real ones (Rule W-43). Every node the graph builder
    emits already gets its real `rdf:type` asserted explicitly; shapes must
    match on that, never on inferred typing.
12. **R12 — New `ManuscriptView`/paradigm-individual code MUST self-type.**
    Any new "view" or controlled-vocabulary individual node (mirroring
    `CatalogingView`/`PhilologicalView` + `BibliographicParadigm`/
    `PhilologicalParadigm`) MUST get its superclass (`hm:ManuscriptView`) and
    the individual's own class (`hm:ViewType`) asserted directly in the data
    graph at construction time in `graph_builder.py` — never rely on the
    ontology file's `owl:NamedIndividual` declaration or `rdfs:subClassOf`
    being merged in at validation time (Rule W-43).
13. **R13 — MARC 561$a provenance MUST NOT mint `E8_Acquisition`.** Generic
    provenance text belongs on `hm:ownership_history` / `rdfs:comment` on the
    manuscript; only typed `provenance_events` with `type=acquisition` (541) may
    emit `CIDOC.E8_Acquisition` nodes. *Why:* 561 ownership/censorship notes
    were exported as Wikibase Acquisition items with mismatched descriptions
    (Rule W-45).
14. **R14 — 505/500 work titles MUST pass `clean_marc_label` + descriptive
    filter.** `is_descriptive_content_title()` rejects note fragments (`גם …`,
    `כולל גם נוסח …`) before they become Work/Expression/TextTradition nodes;
    Wikibase labels go through the same sanitizer at export. *Why:* ISBD quote
    artifacts and descriptive notes dominated AI autofix `fail` verdicts on run
    `48ba6c13` (Rule W-45).
