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
   the file. When a local file exists but its bytes differ from
   `rdf_artifacts.ttl_content`, overwrite from Postgres — never no-op
   merely because a path is present. *Why:* Heroku's slug filesystem is
   ephemeral and multi-dyno; stale on-disk TTL made HMO “skip cache” export
   old graphs while Postgres already held a rebuild (Rule W-39).
5. **R5 — Shared converter code is upstream-owned; exceptions are explicit.**
   The desktop repo remains canonical and the sync script is the normal path.
   Web-side incident ports must be documented, tested, and upstreamed before
   the next full sync. Current exceptions: unrelated desktop WIP blocks a full
   sync (W-43), and the modular source-aware work boundary is web-side pending
   upstream port (W-68). *Why:* untracked divergence makes desktop/web results
   incomparable; destructive sync would erase production fixes.
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
14. **R14 — 505/500 work titles MUST pass source-aware parsing plus label
    hygiene.** Clean 505 rows retain folio/sequence/source evidence; 500 rows
    come only from the anchored parser and are recomputed from raw MARC before
    RDF merge. `clean_marc_label` / `is_descriptive_content_title` remain the
    final hygiene layer. *Why:* broad כולל/comma/vav splitting minted catalogue
    prose as Work/Expression/TextTradition nodes (Rules W-45/W-68).
15. **R15 — Primary RDF nodes MUST carry Wikibase-ready `rdfs:comment`.**
    `graph_builder._stamp_wikibase_comment` (and the CU helper) attach English
    descriptions at build for manuscript/work/expression/person/place/production
    and epistemology nodes so `hmo_exporter` never falls back to generic
    `… in the Hebrew Manuscripts Ontology (HMO)` text. *Why:* 1283/1911 items
    in export (4) still had placeholder descriptions (Rule W-48).
16. **R16 — Every exportable entity gets a substantive label + description
    (Rule W-52).** `_add_production_event` labels `Production of MS {cn}
    ({place}, {date}, scribe …)`; Time-Span labels are `Production period
    {span}` not a bare year; `add_text_tradition` labels in the title's own
    script (Latin never tagged `he`) and skips unusable titles; manuscript
    labels fall back to `MS {shelfmark}` + an `en` `Jerusalem, NLI, …` label;
    genre/subject/place labels route through `label_language_for_text`;
    `_add_content_work` re-runs `parse_contents_entry` defensively. *Why:* the
    run-`48ba6c13` fixup-loop audit tied most residual `name_ok=partial`
    verdicts to system-only labels on Production/Time-Span/tradition nodes.
17. **R17 — Honest-negative grounding + person fidelity + second-pass label
    hygiene (Rule W-53).** `clean_person_display_name` uninverts a trailing
    ``אבן`` (Ibn) instead of deleting it; `infer_person_type` keeps a
    ``Surname, Given`` personal heading a person even under MARC 710/610.
    `_add_production_event` / the Paleographical_Unit loop state the negative
    (``… not recorded in the catalog record``) grounded in title/shelfmark/
    script/scribe rather than repeating the label; the synthetic *Unidentified
    textual content* work no longer mints a circular TextTradition.
    `rdf_helpers` collapses ISBD ``X" ו"Y`` conjunctions, truncates ``|``
    publication notes, rejects single-token vav fragments, and shortens
    over-long ISBD titles (`shorten_isbd_label`); persons emit one
    longest-Hebrew ``he`` label with org-worded `E74_Group` descriptions;
    one-word MS titles are shelfmark-disambiguated; every `E53_Place` mint site
    stamps a script-correct comment. *Why:* export (5) residual
    `name_ok=partial` clusters + 4 person-typing fails on run `48ba6c13`.
18. **R18 — Production descriptions always carry MS context (Rule W-54).**
    `_add_production_event` weaves the manuscript title + shelfmark into **every**
    `E12_Production` description, not only the fully-empty case, so a date-only
    description reads `Production of manuscript {cn} ('{title}', shelfmark {sh}):
    {date}.` instead of a bare date that repeats the label. *Why:* the residual
    `partial` Production items after W-53 had date-only descriptions the judge
    read as "merely repeats the label".
19. **R19 — Ontology + instance namespace is `https://w3id.org/mhm/ontology#`
    (Rule W-55).** The `HM`/`HMO` namespace in `converter/config/namespaces.py`
    (mirrored in research SPARQL prefixes + `property_mapping.HMO_NS_TEMPLATE` +
    the active ontology TTLs) moved off the non-dereferenceable
    `www.ontology.org.il/HebrewManuscripts/2025-12-06#` placeholder to the
    project's real w3id.org permalink. *Blast radius:* existing `RdfArtifact`
    TTLs + live-wiki `hmo_source_uri` values still hold old URIs — runs must be
    RDF-rebuilt, and items must NOT be re-uploaded until source URIs are
    migrated (duplicate-creation risk, Rule W-30/W-42). Desktop mirror needs the
    same swap before `/sync-from-desktop`.
