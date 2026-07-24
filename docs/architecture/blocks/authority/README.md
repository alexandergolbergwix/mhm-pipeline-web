# Authority Enrichment

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

Resolves every named entity extracted from a run's MARC records (persons,
places, works, corporate bodies, meetings, topics) against four external
authority sources — **Mazal** (NLI authority file), **KIMA** (Hebrew place
gazetteer with coordinates + QIDs), **VIAF**, and **Wikidata** — and persists
one `AuthorityMatch` row per `(record, entity, kind, role)` with a confidence
bucket, source attribution, a rich JSONB payload, and a stack of hardening
guard flags. The matcher now runs inside HMO Studio creation; accepted evidence
feeds the canonical HMO item and its RDF/Wikidata projections. Historical
AuthorityMatch rows remain read-only for provenance and migration audits.

## Contents

- [Key files](key-files.md) — file-by-file map of the backend pipeline, vendored matchers, routers, models, migrations, and frontend components
- [How it works — matcher routing](matcher-routing.md) — entity production/dedup, routing by kind, homonym scoring + abstain, confidence tiering
- [How it works — guards](guards.md) — the hardening stack and the full guard-flag table
- [How it works — payload, backends, caching](payload-and-backends.md) — payload completeness contract, the three backends, cache tiers, dedup keys
- [How it works — review & re-enrich](review-and-reenrich.md) — re-enrich surfaces, curator review UI, auto-approve, provenance-event places
- [Rules](rules.md) — the 17 invariants (R1–R17) this block enforces
- [Skills](skills.md) — playbooks: add a guard, re-import Mazal/KIMA, re-enrich after deploy, resolve a homonym, add an authority source, validate VIAF/Wikidata cross-source agreement
- [Tests](tests.md) — the test suites pinning this block

## Related blocks

- [extraction](../extraction/README.md) — NER entities join MARC entities upstream; shares `ColumnFilterPopup` and the review-surface pattern
- [eval-agent](../eval-agent/README.md) — `.../matches/{id}/ai-verify` verdicts land in `payload.ai_verdict`
- [rdf-graph](../rdf-graph/README.md) — approved matches merge into the RDF projection (`rdf_enrichment.py`); payload URIs become `owl:sameAs`
- [caching](../caching/README.md) — Redis L1 / Postgres inference cache the matcher rides on
- [job-service](../job-service/README.md) — background re-enrich job claiming/heartbeat
- [frontend](../frontend/README.md) — glass components, render-stability rules the authority UI obeys
- [wikidata-studio](../wikidata-studio/README.md) — consumes `cluster_ids`, QIDs, preferred names for item building


The former Authority review page and its mutation endpoints are retired. The
legacy `/runs/:runId` bookmark route immediately replaces with HMO Wikibase
Studio; compatibility mutation calls return HTTP 410 and emit
structured `legacy_authority_mutation_retired` telemetry with route, run, actor,
and status metadata. Enrichment runs internally during HMO Studio creation.
Read-only AuthorityMatch data remains available for provenance, historical
exports, and shadow audits.
