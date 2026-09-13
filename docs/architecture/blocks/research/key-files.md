# Research Surface — Key files

> Up: [Research Surface](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/research_graph.py` | Merge per-run TTLs into one cached rdflib graph (LRU of 4, mtime+size fingerprint; `invalidate_cache(run_id)` after RDF rebuild) |
| `backend/app/pipeline/research_queries.py` | Pre-defined SELECT-only SPARQL analytics: co-occurrence, people network, ownership chains, geography, provenance events, summary counts. `ENTITY_WHERE` is the shared work/person/place UNION (Rule W-230) |
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
| `backend/app/routers/research_agent.py` | Sessions, JWT tools, canvas CRUD/download/export, local AG-UI stub |
| `backend/app/services/research_agent/` | Grants, tool wrappers, wiki reads, `scope`/`sanitize`/`sparql_templates`, local AG-UI |
| `backend/app/models/research_agent.py` | `ResearchAgentThread`, `ResearchAgentArtifact`, `ResearchAgentGrant` |
| `backend/app/migrations/versions/0042_research_agent.py` | Thread / artifact / grant tables |
| `modal/modal_research_agent.py` | Pydantic AI + AG-UI deploy target (never imported; Rules W-15 / W-228 / W-231; `retries=3`) |
| `frontend/src/routes/ResearchAssistant.tsx` | Chat + canvas split at `/runs/:runId/linked-data-explorer` |
| `frontend/src/routes/LinkedDataExplorer.tsx` | Compatibility re-export of `ResearchAssistant` |
| `frontend/src/components/research/ResearchChat.tsx` | Chat log + quick actions; bouncing wait dots while the agent is busy with no stream text (frontend R22) |
| `frontend/src/components/research/ResearchCanvas.tsx` | Artifact tabs, markdown editor, map/SPARQL/network/cluster embeds |
| `frontend/src/api/researchAgent.ts` | Session + SSE AG-UI client |
| `frontend/src/lib/canvasState.ts` | AG-UI event reducer for canvas + messages; `humanizeAgentError` (Rule W-231) |
| `frontend/src/components/research/ProvenanceMapPanel.tsx` | Leaflet map, `KIND_COLOR`/`KIND_LABEL` per stop kind, animated arcs, single-MS + corpus modes |
| `frontend/src/api/research.ts` | Typed client: `MapStopKind`, `MapStop`, `CorpusManuscript`, `CorpusEventPlace`, `PathResult`, … |
