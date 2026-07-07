# Research Surface — How it works

> Up: [Research Surface](README.md)

**Graph loading.** Every RDF-plane endpoint funnels through
`_load_graph_or_404` (`linked_data_explorer.py:111`): check project
membership → collect run ids → `_restore_missing_ttls` (re-seed
`state/runs/{run}/manuscripts.ttl` from the authoritative Postgres
`rdf_artifacts` row — Heroku's filesystem is ephemeral) → `load_merged_graph`
(fingerprinted in-process cache) → 404 if the merged graph is empty. Analytics
run in `asyncio.to_thread` since rdflib is synchronous.

**Summary caching + coherence gate.** `/research/summary` caches its result in
the two-tier inference cache under `kind="research.summary"`. The cache key
folds in each run's `RdfArtifact.built_at` + triple count, each run's Wikidata
Studio fingerprint, and the Wikibase URL, so a rebuild or a new approval
invalidates (`research.py:77`). `_is_coherent_summary` refuses to serve or
write "triples > 0 but zero entities" (the bad-window read); on an incoherent
live graph it force-reseeds TTLs from Postgres and recomputes once.

**Cross-source dedup.** `research_aggregate.merge_entities` union-finds
provider entities by identity keys with precedence
`qid: > cn: > viaf: > nli: > lbl: > raw:`; the normalized-label key is emitted
only when no strong identifier exists, so two same-titled manuscripts never
over-merge. `by_source` counts per merged entity satisfy
`max(by_source) ≤ total ≤ sum(by_source)`.

**Provenance movement map (single MS).** The router
(`research_provenance.py:308`) loads `RunRecord` + `AuthorityMatch` rows,
pre-resolves owner coordinates (`owner_place` then, if it abstains,
`institution_place`), and calls the pure `build_provenance_map`. Stop kinds,
in emission order: `production` (KIMA coords + MARC date band), typed
provenance-event stops `acquisition` / `conservation` / `exhibition` (from
`record["provenance_events"]`, Rule W-32), `significant_place` (undated place
matches), `owner` (birth-year-sorted, biographical-proxy geo, flagged
`inferred_geo`), and a final `current_holder` anchor hard-pinned to the NLI in
Jerusalem. Owners are gated by approval (A1), confidence high/medium (A2), an
anachronism check with a 5-year buffer (A5), and no-coords → `dropped` side
list (A7). Edges connect consecutive geolocated stops; an edge between two
undated stops is `directed: false` (D2).

**Corpus movement.** `_build_corpus_items` extracts one production-arc item
per record (production point → NLI holder) plus `event_places`, `owners`,
`genres`, `places` facets. The full unfiltered list is cached under
`kind="research.movement"` keyed by a project fingerprint (record count + each
match's id/qid/confidence/approved); year/place/genre/owner filters are applied
in Python per request so one cache entry serves every filter combination.

**Pathfinding.** `build_nx_graph` projects only 8 HMO predicates
(`has_author`, `has_scribe`, `has_owner`, `has_illuminator`,
`has_production_place`, `mentions_place`, `realises`, `is_carried_out_by`)
into an **undirected** networkx graph; `find_shortest_path` caps at 6 hops and
returns `{path, edges}` with human-readable edge labels.

**SPARQL console.** Three backends behind one UI: local rdflib
(`/research/sparql`), project Wikibase proxy (`/sparql/wikibase`, 503 when
`WIKIBASE_SPARQL_URL` unset), and public Wikidata proxy (`/sparql/wikidata`,
in-process 64-entry LRU). All share `_validate_query` (SELECT/CONSTRUCT only,
keyword blocklist for writes), a 1000-row cap, and a 30 s timeout. Export
reuses the same validation/execution and streams CSV/JSON/BibTeX/RIS with a
`Content-Disposition: attachment` header. `corpus.py` federates the same query
over every project the user belongs to, tagging rows with `_source_project`.

**Geo-enrichment chain (owners).** Catalogues never give owner locations, so
`research_geo_enrich.owner_place(qid)` queries WDQS: require `P31=Q5` (A3),
then take the first property in `P551 → P937 → P20 → P19` that has exactly one
valid `P625` coordinate (A8 abstains on conflicting coords per property; A6
rejects NaN / out-of-range / (0,0)). `institution_place` is the mirror for
collections/libraries (`P159 → P276 → P131`), abstaining when the entity IS a
human. Both are wrapped in the two-tier inference cache under
`kind="wikidata.person_place"` and honour `MHM_NO_NETWORK`.
