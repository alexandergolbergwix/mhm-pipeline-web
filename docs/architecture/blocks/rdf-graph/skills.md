# RDF / HMO-Ontology Graph Build — Skills

> Up: [RDF / HMO-Ontology Graph Build](README.md)

### Skill: rebuild RDF for a run
1. UI: run detail → RDF Graph → Build (options map to `RdfBuildOptions`).
   API: `POST /api/runs/{run_id}/rdf/build` with optional body
   `{add_epistemological_status, add_cataloging_view, add_philological_overlay}`
   — or enqueue a background job with `kind="rdf_build"` (same params).
2. Verify via `GET /rdf/status` (triples/manuscripts counts) and confirm the
   `rdf_artifacts` row updated (`built_at`).
3. If the run was authority-re-enriched, rebuild is mandatory before the
   Geography tab reflects new places (R10).

### Skill: sync converter from desktop
1. Run `/sync-from-desktop` (or
   `bash /Users/alexandergo/Documents/Doctorat/pipeline/scripts/sync_converter_to_web.sh`).
2. Confirm zero drift: `diff -q -r --exclude '__pycache__' --exclude '*.pyc'
   $DESKTOP/converter $WEB/backend/converter`.
3. Re-run `/smoke-routers` + `/run-tests` to catch API drift.
4. Never patch `backend/converter/*` directly (R5); ontology TTLs travel with
   the sync (R6).

### Skill: add a new ontology property to the graph
1. Add the term to the **desktop** `pipeline/ontology/hebrew-manuscripts.ttl`
   and emit it from the desktop `converter/rdf/graph_builder.py`.
2. Sync to web (skill above). Rebuild RDF for a test run.
3. Check `GET /rdf/ontology-coverage` — the new term should move from
   `missing_properties` to covered; extend
   `backend/tests/test_ontology_coverage.py` if the report format changed.
4. If the term should project to Wikidata, update the projection map behind
   `converter/wikidata/projection_coverage.py` and check `GET /rdf/coverage`
   for `projection_status: unknown` regressions.

### Skill: inspect coverage after a build
- `GET /api/runs/{id}/rdf/coverage` — per-class Wikidata projection status;
  `unknown_class_count > 0` means classes exist in RDF with no projection
  decision. Backed by `rdf_projection_coverage.json` next to the TTL.
- `GET /api/runs/{id}/rdf/ontology-coverage` (admin) — HMO classes/properties
  covered vs. total, with missing-term lists (`ontology_coverage.json`).
- Both 404 with "build RDF first" when no report exists on disk (they are
  not persisted in Postgres — rebuild to regenerate).

### Skill: debug missing places on the Geography tab
1. Was RDF rebuilt after the latest authority approvals? If not, rebuild (R10).
2. Is the authority match **approved**? Unapproved KIMA matches never merge (R1).
3. Does the provenance event have coords? `_merge_kima_place`
   (`rdf_enrichment.py:396`) only fills `lat/lon` when the KIMA payload has
   them; `_add_provenance_events` drops coord-less events by design (R3).
   Check `payload.kima_lat/kima_lon` on the match, then grep the TTL for
   `wgs84:lat`.
4. Names must overlap: `names_overlap(place_text, entity_text)` gates the
   coord write-back — a heavily normalised place string can miss.
5. For institution/gazetteer places see the [authority block](../authority/README.md)
   (Rule W-32 routing: KIMA → Ashkenazi gazetteer → `institution_place`).

### Skill: recover a run's TTL after a dyno restart
Nothing to do manually — any `GET /rdf/*` read calls `ensure_ttl_on_disk`
(`rdf_build.py:892`) which restores `manuscripts.ttl` from `rdf_artifacts`.
If reads still 404, no build ever succeeded for the run: rebuild. Note the
graph index and coverage JSONs are re-derived lazily (`ensure_index`) but
coverage reports require a rebuild.
