# RDF / HMO-Ontology Graph Build — Key files

> Up: [RDF / HMO-Ontology Graph Build](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/rdf_build.py` | Async wrapper around the mapper: `build_rdf_graph`, `RdfBuildOptions`, SHACL validation, Cytoscape JSON, `rdf_output_path_for_run`, `ensure_ttl_on_disk`, `normalise_matches` |
| `backend/app/pipeline/rdf_build_job.py` | Background job (`kind="rdf_build"`, dispatched by `run_job_service.py:340`): loads approved rows, builds, write-throughs `RdfArtifact`, busts caches |
| `backend/app/pipeline/rdf_enrichment.py` | Merges approved authority + NER + ML genres + KIMA places into flat MARC dicts before mapping |
| `backend/app/pipeline/graph_index.py` | Graph catalog + SQLite index + viewport payloads for scalable visualization |
| `backend/converter/rdf/graph_builder.py` | Vendored desktop `GraphBuilder` — the actual HMO/CIDOC triple emitter, incl. `_add_provenance_events` (line 730) |
| `backend/converter/rdf/ontology_coverage.py` | HMO ontology class/property coverage report |
| `backend/converter/wikidata/projection_coverage.py` | `rdf_projection_coverage.json` writer (which RDF classes project to Wikidata) |
| `backend/ontology/hebrew-manuscripts.ttl` | Canonical HMO ontology (copied from desktop at sync time) |
| `backend/ontology/shacl-shapes.ttl` | SHACL shapes used by `POST /rdf/validate` |
| `backend/app/routers/rdf.py` | All `/runs/{run_id}/rdf/*` endpoints (build, coverage, ontology-coverage, catalog, viewport, ego, graph, node, ontology-usage, download.ttl, validate, triple overrides, status) |
| `backend/app/routers/linked_data_explorer.py` | Project-level SPARQL over the merged run graphs (`/projects/{id}/research/sparql[...]`); restores missing TTLs before querying |
| `backend/app/models/rdf_artifact.py` | `rdf_artifacts` table — durable TTL per run (PK `run_id`, `ttl_content`, counts) |
| `pipeline/scripts/sync_converter_to_web.sh` | Upstream sync of `converter/{rdf,transformer,config}` + `projection_coverage.py` + both ontology TTLs |
| `.claude/commands/sync-from-desktop.md` | `/sync-from-desktop` playbook (rsync + drift diff) |
