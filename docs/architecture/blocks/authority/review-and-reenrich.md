# Authority Enrichment — canonical review boundary

> Up: [Authority Enrichment](README.md)

Standalone Authority review and re-enrichment mutations are retired. The
former routes (`authority/re-enrich`, `authority/rebuild`, match edits,
approvals, and auto-approve) return HTTP 410 by default and emit retirement
telemetry. This prevents a legacy mutation from diverging from canonical HMO
state.

Authority matching remains an internal HMO creation step: rebuild HMO Studio
with `refresh_authority=true`, apply the hardening and false-positive gates,
upload/update the Wikibase items, and read every live item back. Accepted
Mazal, KIMA, VIAF, and Wikidata evidence is persisted on the canonical HMO
entity.

Read-only `AuthorityMatch` rows are deliberately retained for provenance,
historical exports, and shadow comparison. Use
`backend/scripts/run_hmo_production_e2e.py <run-id>` to validate the authority
false-positive gate together with HMO→RDF and HMO→Wikidata projections.
