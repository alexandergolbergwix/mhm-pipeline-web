# RDF graph + ontology

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

### Rule W-32 — Non-production provenance-event places on the maps (added 2026-06-14)

Web counterpart of desktop **Rule 60**. The corpus-movement + single-MS
provenance maps now plot custody events beyond production: acquisition
(MARC 541 $b), conservation/exhibition (MARC 583 $j), and institutional
ownership (named collections → seat). One additive record channel,
`record["provenance_events"]` (`{type, place_text, agent_name, year,
year_earliest, year_latest, source_field, lat, lon, wikidata_id,
certain}`), flows ingest → KIMA → RDF → maps. Production stays in
`record["place"]` — never regressed.

- **Ingest** (`app/pipeline/marc_ingest.py`): `_extract_provenance_events`
  reuses the vendored desktop `FieldHandlers` helpers so the `.mrc` path
  (desktop `extract_all_data`) and the TSV/JSON collapsed-key path emit
  byte-identical events. `extract_named_entities` yields a `*_place`-role
  place entity per event.
- **Authority** (`app/pipeline/authority.py`): `is_place` accepts any
  `*_place` role → KIMA fires. After a KIMA miss, the Ashkenazi gazetteer
  (`app/pipeline/ashkenazi_gazetteer.py` + `data/ashkenazi_communities.json`)
  fills the diaspora gap; a coord-bearing place survives the no-id drop.
  `research_geo_enrich.institution_place` resolves collection/library seats
  (P159→P276→P131→P625; abstains on humans). The router chains
  `owner_place` → `institution_place`; owner loops accept `organization`.
- **RDF** (`app/pipeline/rdf_build._merge_authority_ids` +
  `converter/rdf/graph_builder._add_provenance_events`): KIMA coords are
  written back onto the matching event; a CIDOC `E8/E10/E7` node +
  `P7_took_place_at` → `E53_Place` with `wgs84:lat/long` (+ `owl:sameAs`)
  + `P4_has_time-span` is emitted, gated on coords (**never fabricated**).
  `hm:mentions_place` makes `query_geography` surface it.
- **Maps**: `research_provenance_map.build_provenance_map` emits typed +
  dated `acquisition`/`conservation`/`exhibition` stops; `corpus_movement`
  adds `event_places` and folds them into the corpus place facet. Frontend
  `ProvenanceMapPanel.tsx` (`KIND_COLOR`/`KIND_LABEL`/`Legend`) +
  `api/research.ts` (`MapStopKind`, `CorpusEventPlace`) gained the kinds.

Existing runs must **rebuild RDF** for the Geography tab to pick up the
new places; the DB-sourced movement maps benefit immediately. 561
free-text NER is deferred (no ML). Tests:
`tests/test_provenance_events_ingest.py` (10),
`test_provenance_events_rdf.py` (5), `test_institution_place.py` (9),
`test_ashkenazi_gazetteer.py` (9), + Rule-60 cases in
`test_provenance_map_guards.py` and `test_corpus_movement.py`.

### Rule W-34 — Full HMO RDF projection (added 2026-06-17)

The web RDF build path must wire approved authority + NER enrichment into
``GraphBuilder`` before ``ExtractedData`` is materialised:

- ``backend/app/pipeline/rdf_enrichment.py`` merges approved Stage-2/3 rows.
- ``backend/app/pipeline/rdf_build.py`` passes ``RdfBuildOptions`` toggles
  (epistemology, cataloging view, philological overlay — all default on).
- Each build writes ``rdf_projection_coverage.json`` beside the TTL; the
  router exposes ``GET /runs/{id}/rdf/coverage``.
- Vendored converter code lives under ``backend/converter/``; sync from the
  desktop pipeline with ``pipeline/scripts/sync_converter_to_web.sh``.

Canonical ontology: ``pipeline/ontology/hebrew-manuscripts.ttl`` (copied to
``backend/ontology/`` at sync time).

### Rule W-43 — RDF SHACL validation MUST NOT use RDFS inference; type nodes explicitly instead (added 2026-07-07)

Root cause of the 773/2131 SHACL Violation/Error rows from Rule W-42's audit
(run `48ba6c13`): `rdf_build.py::_run_shacl_sync` called
`ShaclValidator.validate(..., inference="rdfs")`. The ontology deliberately
reuses several properties (`forms_part_of`, `has_expression`, `has_work`,
`has_script_type`, `mentions_scribe`, `paradigm_bridge`, …) across multiple
levels of the Manuscript/CodicologicalUnit/PaleographicalUnit hierarchy and
across the Colophon/Production/ParadigmBridge classes — by design, this is
the v1.4 nested-CU model. Each property's `rdfs:domain`/`rdfs:range` axiom
only names the *primary* class it was first declared for, never an
exhaustive union of every class that legitimately uses it. `pyshacl`'s RDFS
inference synthesizes new `rdf:type` triples from those axioms, so a
`CodicologicalUnit` or `Production` node that merely *uses* a property whose
domain says `Manifestation`/`Colophon`/`ParadigmBridge` gets silently
cross-typed as that class too — and is then validated against a shape that
was never meant to apply to it. On the real `cbf6a5a6` local run TTL this
produced 1319 violations (`Paradigm bridge must link to a TextTradition`
×282, `NLI identifier must be a single string value` ×280, `Paradigm bridge
must link to a Work` ×271, `Colophon must have text content` ×37, plus
`Expression`/`Manuscript` completeness noise) — none of which reflected a
real data problem; the same TTL is **0 violations** at `inference="none"`.

**Fix, two parts (defense in depth — either alone would have sufficed for
this corpus, both together close the gap for good):**

1. **`_run_shacl_sync` now calls `inference="none"`.** Every ontology class
   the graph builder emits already gets its real `rdf:type` asserted
   explicitly at construction time, so shapes can target it precisely
   without needing RDFS subclass/domain inference at all. This matches every
   other `ShaclValidator.validate()` call site in the desktop pipeline
   (`converter/main.py`, `converter_api.py`, `gui/main_window.py`, …), which
   already default to `inference="none"` — the web app's build-validation
   path was the only outlier.
2. **`graph_builder.py` (`GraphBuilder`) cleanup**, so future inference-mode
   experiments (or a curator manually running `pyshacl` with `rdfs` for
   deeper reasoning) don't reintroduce the same leak:
   - Removed `hm:mentions_scribe` triples on the Production/Manuscript
     nodes — the property is `rdfs:domain hm:Colophon`; only the Colophon
     node should ever carry it. Scribe linkage from Production/Manuscript
     already exists via `hm:has_scribe` + CIDOC `P14_carried_out_by`.
   - Removed the reversed `hm:paradigm_bridge` triples on the Work/
     TextTradition nodes (`rdfs:domain hm:ParadigmBridge` — the bridge
     points *at* them via `has_linked_work`/`has_linked_tradition`, they
     never point at the bridge).
   - `_add_cataloging_view`/`add_philological_view` now co-type their view
     node as `hm:ManuscriptView` explicitly (previously relied on
     `CatalogingView`/`PhilologicalView rdfs:subClassOf ManuscriptView`
     inference to make `ManuscriptViewShape` apply) and co-type
     `hm:BibliographicParadigm`/`hm:PhilologicalParadigm` as `hm:ViewType`
     directly in the data graph (previously that typing lived only in the
     ontology file's `owl:NamedIndividual` declaration, invisible to any
     SHACL run that doesn't merge the full ontology in as `ont_graph`).
   - Separately, the **desktop** pipeline's `_add_cataloging_view` was
     missing the `hm:view_type hm:BibliographicParadigm` triple entirely
     (a real, inference-independent bug — `PhilologicalView` got its
     `PhilologicalParadigm` typing, `CatalogingView` never got the
     bibliographic counterpart). Fixed there; the web copy already had this
     line, so this specific line does not appear in the web diff.

**Verification:** `tests/unit/test_ontology_golden_shacl.py` (desktop) was
itself part of the problem — its hand-rolled `pyshacl.validate()` call used
`inference="rdfs"` with the ontology only merged into `shacl_graph` (never
passed as `ont_graph`), so subclass/individual-type inference never actually
ran against the golden fixture in the first place; the shapes it should have
been failing were silently never triggered. It now calls the real
`ShaclValidator.validate(..., inference="none", ontology_path=...)` so the
test exercises the same code path production uses. Confirmed:
`inference="none"` → golden corpus conforms; `inference="rdfs"` on the same
corpus still surfaces the domain-leak noise (`canonical_hierarchy`,
`tradition_name`, and dozens of "Manuscript should embody at least one
Expression" warnings on non-Manuscript nodes) — proof the inference mode,
not the corpus, was always the bug.

**Residual gap — desktop↔web mirror drift (Rule R5 exception, temporary):**
the desktop pipeline repo's `converter/rdf/graph_builder.py`,
`converter/transformer/{field_handlers,uri_generator,gematria,
hebrew_date_parse,hebrew_gregorian_calendar}.py`, and
`ontology/hebrew-manuscripts.ttl` currently carry **other, unrelated,
uncommitted WIP changes** (genre-node refactor, Hebrew/Gregorian date
parsing consolidation, subject-record helpers) that are not yet tested/
finished — `sync_converter_to_web.sh` mirrors whole directory trees, so
running it right now would have deleted three transformer modules the web
backend still imports (`marc_ingest.py`, `date_entity_normalize.py`,
`converter/transformer/date_resolver.py`) and dragged in changes that break
`tests/unit/test_safety_guards.py` in the desktop repo. This fix was ported
by hand-applying only the four `graph_builder.py` edits above to the web
copy — **run `/sync-from-desktop` (or `scripts/sync_converter_to_web.sh`)
once the desktop WIP work lands and passes its own test suite**, not before.

Tests: `pipeline/tests/unit/test_ontology_golden_shacl.py`,
`pipeline/tests/unit/test_people_network.py` (confirms removing
`mentions_scribe` from Manuscript/Production doesn't break the people-network
query — it already reaches scribes via `has_scribe`/`P14_carried_out_by`),
`backend/tests/` (34 rdf/graph_builder/people_network cases, web repo).

---

### Rule W-87 — HMO RDF literals MUST be export-safe (added 2026-07-23)

The first production canonical migration attempt was blocked before upload by
21 unmatched label quote/parenthesis artifacts and 11 digital URLs serialized
with MARC quote wrappers. GraphBuilder now strips balanced outer quotes before
emitting `xsd:anyURI` values, and HMO labels pass the shared title sanitizer
while preserving Hebrew gershayim; an English-only label incorrectly tagged
`@he` is rerouted to `@en`. This keeps SHACL and the HMO export-quality gate
fail-closed without discarding legitimate abbreviation marks. Tests:
`test_graph_builder_codicological_labels.py` and
`test_hmo_exporter_descriptions.py`.

**Follow-up (Rule W-111):** `hm:restriction_url` was still emitted raw from
MARC 540/939 `$u`, so live uploads failed with WBI `Invalid URL "http://…"`.
All URL-typed paths now share `clean_url_value`, including export + upload
write sanitization so cached builds can retry without an RDF rebuild.


### Rule W-88 — GraphBuilder MUST emit only ontology-declared properties (added 2026-07-23)

The production migration found that anthology records still emitted the removed
`has_anthology_structure` and `number_of_works` predicates, so the schema
bootstrap could not resolve the generated HMO claims. Anthology manuscripts now
receive the declared `hm:AnthologyStructure` type and per-expression anthology
positions; removed predicates are not minted. Test: `test_graph_builder_codicological_labels.py`.
