# Research Surface (Corpus Analytics, Graphs, Maps, Queries)

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

The Research surface (frontend route `/runs/:runId/linked-data-explorer`, "Linked
Data Explorer") answers scholarly questions over an already-built corpus: which
works travel together in the same manuscripts (co-occurrence), which scribes /
authors / owners are socially connected (people network), who owned each
manuscript (ownership chains), where manuscripts were produced and mentioned
(geography + heatmap), how a single manuscript moved through space and time
(provenance timeline + movement map), how the whole corpus moved
(corpus-movement map with facets), how any two entities are connected
(neighbors / shortest path), and anything else via a read-only SPARQL console
with saved queries and citation-format export.

It reads from **two data planes**:

1. **RDF plane** — per-run `manuscripts.ttl` files merged into one in-process
   rdflib graph per project (`research_graph.py`). All SPARQL analytics, entity
   detail, pathfinding, and geography read this plane.
2. **DB read-model plane** — `run_records` (raw MARC) + `authority_matches`
   (approval, confidence, QIDs, KIMA coords in `payload`). The provenance /
   corpus-movement maps and the evidence panel read this plane, because they
   need curator-approval state and coordinates that may postdate the last RDF
   build.

The Overview summary additionally merges a third and fourth source — the run's
built Wikidata Studio items and a live project Wikibase SPARQL endpoint — into
one deduplicated entity count (`research_aggregate.py`).

## Contents

- [Key files](key-files.md) — pipeline builders, routers, and frontend map of the surface
- [How it works](how-it-works.md) — graph loading, summary coherence gate, dedup, maps, pathfinding, SPARQL console, geo-enrichment
- [Rules](rules.md) — invariants R1–R13 (read-only SPARQL, never fabricate coords, fail-closed geo, cache discipline)
- [Skills](skills.md) — add stop kinds / analytics queries / SPARQL backends, debug maps and tabs
- [Tests](tests.md) — test suites pinning this block

## Related blocks

- [rdf-graph](../rdf-graph/README.md) — builds the per-run TTL + graph index this surface reads
- [authority](../authority/README.md) — produces the `authority_matches` payloads (KIMA coords, QIDs, approval) the maps consume
- [frontend](../frontend/README.md) — panel/tab conventions, glass components, Zustand rules
- [caching](../caching/README.md) — the two-tier inference cache every research cache key lives in
