# RDF / HMO-Ontology Graph Build

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

Projects a run's MARC records — enriched with curator-**approved** authority
matches and NER entities — into a single Turtle graph per run using the
Hebrew Manuscripts Ontology (HMO) + CIDOC-CRM/LRMoo, via the vendored desktop
`MarcToRdfMapper`/`GraphBuilder`. It also SHACL-validates the graph, writes
two coverage reports, builds a SQLite viewport index for the browser graph
view, and persists the TTL durably in Postgres (`rdf_artifacts`) so Heroku
dyno restarts never force a rebuild.

## Contents

- [Key files](key-files.md) — file-by-file map of the build path, routers, and vendored converter
- [How it works](how-it-works.md) — the 8-step build flow from trigger to vendoring
- [Rules](rules.md) — invariants R1–R19 (approved-rows-only, coord gating, durability, mirror discipline, Wikibase descriptions)
- [Skills](skills.md) — rebuild, sync-from-desktop, ontology extension, coverage inspection, debugging playbooks
- [Tests](tests.md) — test suites pinning this block

## Related blocks

- [authority](../authority/README.md) — produces the approved matches + KIMA coords merged here
- [extraction](../extraction/README.md) — produces the approved NER entities + ML genres
- [research](../research/README.md) — SPARQL explorer, geography/provenance maps read this TTL
- [hmo-wikibase-studio](../hmo-wikibase-studio/README.md) — HMO item build parses this TTL (coverage job, Rule W-39)
- [job-service](../job-service/README.md) — `rdf_build` background job lifecycle
- [caching](../caching/README.md) — RdfArtifact durability pattern, viewport/index caches
- [deployment](../deployment/README.md) — ephemeral dyno filesystem, converter sync workflow
