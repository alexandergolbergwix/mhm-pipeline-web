# Research Surface — Key files

> Up: [Research Surface](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/research_graph.py` | Merge per-run TTLs into one cached rdflib graph (LRU of 4, mtime+size fingerprint; `invalidate_cache(run_id)` after RDF rebuild) |
| `backend/app/pipeline/research_queries.py` | Pre-defined SELECT-only SPARQL analytics: co-occurrence, people network (server-side networkx spring layout), ownership chains, geography (+heatmap), per-MS provenance events, summary counts |
| `backend/app/pipeline/research_aggregate.py` | Cross-source Overview: union-find entity dedup across rdf / wikidata / wikibase, keyed QID > control-number > VIAF > NLI > label |
| `backend/app/pipeline/research_graph_ops.py` | rdflib → undirected networkx graph over 8 HMO predicates; `get_neighbors`, `find_shortest_path` (max_depth 6) |
| `backend/app/pipeline/research_provenance_map.py` | Pure builder for the single-MS movement map: stops, edges, dropped list, integrity guards A1–A7/D2 |
| `backend/app/pipeline/research_geo_enrich.py` | Owner→place via Wikidata biography (P551→P937→P20→P19→P625) and institution→seat (P159→P276→P131→P625); guards A3/A6/A7/A8 |
| `backend/app/pipeline/corpus_movement.py` | Pure corpus-movement item extraction + filter/facet builders (production arc → NLI only) |
| `backend/app/pipeline/graph_index.py` | Per-run graph catalog JSON + SQLite node/edge index + viewport budgeting for the RDF canvas (built at `POST /rdf/build`, read by `/rdf/catalog` and `/rdf/viewport`) |
| `backend/app/routers/research.py` | `/projects/{id}/research/{summary,co-occurrence,people-network,ownership,geography}`; TTL restore from `rdf_artifacts`; coherence-gated summary cache |
| `backend/app/routers/research_provenance.py` | `/research/provenance` (timeline), `/research/manuscripts` (picker), `/research/provenance-map`, `/research/movement{,/facets}` |
| `backend/app/routers/research_entity.py` | `/research/entity?uri=` — URI → label/type/roles/manuscripts/geo + authority identifiers from DB |
| `backend/app/routers/research_evidence.py` | `/research/evidence?uri=` — URI → MARC source, approval trail, authority matches (reverse `MS_<cn>` minting) |
| `backend/app/routers/research_pathfinding.py` | `/research/neighbors?uri=`, `/research/path?from=&to=` |
| `backend/app/routers/research_export.py` | `POST /research/sparql/export` — CSV / JSON / BibTeX / RIS streaming download |
| `backend/app/routers/linked_data_explorer.py` | SPARQL console backends: local HMO graph, Wikibase proxy, Wikidata proxy; owns `_validate_query`, `_load_graph_or_404`, `run_wikibase_sparql` |
| `backend/app/routers/saved_queries.py` | Saved-SPARQL CRUD (`saved_queries` table; viewer reads, editor writes) |
| `backend/app/routers/corpus.py` | `POST /research/corpus/sparql` — cross-project federation over all memberships, `_source_project` column added |
| `frontend/src/routes/LinkedDataExplorer.tsx` | 9-tab shell: summary, cooccurrence, network, ownership, geography, provenance, movement, relationships, sparql (lazy-loaded panels) |
| `frontend/src/components/research/ProvenanceMapPanel.tsx` | Leaflet map, `KIND_COLOR`/`KIND_LABEL` per stop kind, animated arcs, single-MS + corpus modes |
| `frontend/src/api/research.ts` | Typed client: `MapStopKind`, `MapStop`, `CorpusManuscript`, `CorpusEventPlace`, `PathResult`, … |
