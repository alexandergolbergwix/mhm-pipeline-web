# RDF / HMO-Ontology Graph Build — How it works

> Up: [RDF / HMO-Ontology Graph Build](README.md)

1. **Trigger** — `POST /runs/{run_id}/rdf/build` (`rdf.py:243`, synchronous,
   bounded wait) or the background `rdf_build` run-job
   (`rdf_build_job.py::run_rdf_build_job`, claimed/heartbeated per Rule W-38).
   Both load identical inputs:
   - `RunRecord.marc` rows (all records for the run),
   - `AuthorityMatch` rows **where `approved IS TRUE`** only,
   - `ExtractionApproval` rows **where `approved IS TRUE`** only, with curator
     `override_text/type/role` taking precedence over model predictions,
   - `RdfTripleOverride` rows (curator per-triple literal edits),
   - per-record `kima_places` name→URI maps.
2. **Enrichment merge** (`_run_mapper_sync`, `rdf_build.py:235`): for each
   record, `rdf_enrichment.py` folds in — `merge_approved_ner` (person_ner →
   authors/contributors; genre_ml → genres with `AIAttribution`/`Probable`
   certainty; contents_ner → contents/folio/work-author; provenance_ner →
   former_owner / ownership events / catalog refs), `merge_ml_genres` +
   `apply_genre_classifier_fallback` (only when MARC 655 is empty),
   `merge_approved_authority` (routes by `entity_kind`: person/place(KIMA)/
   topic/work/corporate; stamps VIAF/Wikidata/Mazal IDs, preferred names,
   cluster IDs), and `merge_kima_places_dict`. KIMA coords are written back
   onto matching `provenance_events` entries (`_merge_kima_place`,
   `rdf_enrichment.py:396` — explicit `None` checks, not `setdefault`). Validated place QIDs and KIMA/VIAF/Mazal IDs are propagated even when coordinates are absent; payload URI/QID forms are normalized before storage.
3. **Mapping** — an `ExtractedData` is built per record and passed to the
   vendored `GraphBuilder`, constructed with the three `RdfBuildOptions`
   toggles (`add_epistemological_status`, `add_cataloging_view`,
   `add_philological_overlay` — all default `True`; exposed on the build
   request body / job params). Control numbers are URI-sanitised for URI
   minting but the raw CN is kept for match lookups (`rdf_build.py:306`).
4. **Provenance events** (`graph_builder.py:730`, web Rule W-32 / desktop
   Rule 60): each `provenance_events` entry with a place text AND plausible
   lat/lon mints a CIDOC node (`E8_Acquisition` / `E10_Transfer_of_Custody` /
   `E7_Activity`) with `P7_took_place_at` → `E53_Place` carrying
   `wgs84:lat/long` (+ `owl:sameAs` Wikidata QID) and `P4_has_time-span` when
   dated; the MS links via `hm:has_provenance_event` and `hm:mentions_place`.
   Events without coordinates are silently skipped — points are never
   fabricated.
5. **Post-build**: triple overrides are re-applied (remove + add literal),
   TTL serialised to `backend/state/runs/{run_id}/manuscripts.ttl`, the
   SQLite graph index + catalog are rebuilt
   (`graph_index.build_and_persist_index`), and two reports are written
   beside the TTL: `rdf_projection_coverage.json` (Wikidata projection
   status per class, surfaced by `GET /rdf/coverage`) and
   `ontology_coverage.json` (HMO class/property coverage, admin-only
   `GET /rdf/ontology-coverage`).
6. **Durability**: the TTL text is upserted into `rdf_artifacts` in the same
   request/job (`rdf.py:355`, `rdf_build_job.py:150`). Every read-side
   consumer (`rdf.py`, `hmo_studio.py`, `linked_data_explorer.py::
   _restore_missing_ttls`) calls `ensure_ttl_on_disk` first, which re-seeds
   the local file from Postgres when the dyno recycled. Cache busting after a
   build deletes `graph_*.json` / `graph_viewport_*.json` and invalidates the
   research merged-graph LRU.
7. **Validation** — `POST /rdf/validate` runs pyshacl with `inference="none"`
   against `backend/ontology/shacl-shapes.ttl` + the HMO ontology (also the
   path used by the HMO item upload's SHACL gate, Rule W-42). RDFS inference
   is deliberately not used here — it cross-types nodes via shared-property
   `rdfs:domain` axioms and produces false-positive violations (Rule W-43).
8. **Vendoring** — shared `backend/converter/` code is normally synced from
   the desktop repo. Before a full sync, reconcile the documented W-43/W-68
   web-side exceptions upstream and run both suites; never let the sync delete
   a focused projection module or reintroduce an incident bug.

### Canonical HMO projection

 provides the deterministic Wikibase-snapshot → RDF boundary. It accepts only normalized canonical HMO entities, preserves source URIs and labels, validates every claim shape, and rejects malformed or duplicate state. The legacy MARC/authority build remains available during shadow comparison; canonical projection is the intended cutover source.

### MARC coverage

TSV/JSON ingestion runs the canonical desktop handlers before graph
construction. RDA carrier terms (336/337/338) remain RDF catalog evidence,
while Wikidata stays conservative. Run `scripts.audit_mapping_coverage` to
prove that no non-empty tag is silently dropped.

### Authority enrichment

Place nodes retain resolved Wikidata URIs as `hm:external_wikidata_uri` in
addition to RDF `owl:sameAs`; person nodes retain `hm:wikidata_id` and
`hm:viaf_id`. This keeps HMO Wikibase enrichment useful while preserving the
separate identifier namespaces of the project Wikibase and Wikidata.


Canonical HMO projection: `build_rdf_from_hmo_canonical_cache` reads durable `hmo_canonical_entities` first and falls back to the cache read model only for migration compatibility.
