# HMO authority evidence

HMO enrichment must be fail-closed.  Matchers may provide Mazal, KIMA, VIAF,
or Wikidata candidates, but a candidate is a public authority claim only when
its identifier is valid and there is one unambiguous identifier for that
authority kind.  Conflicting QIDs remain in `authority_evidence` with
`accepted: false` so curators can inspect them without risking a false
positive Wikibase claim. The exporter also rejects an accepted external identifier reused by multiple distinct HMO source URIs and removes the corresponding claims.

**Richness without guessing (Rule W-101).** Approved matches should mint the
full accepted identifier surface into RDF before HMO export: Mazal
(`hm:mazal_id`), KIMA (`hm:kima_id` + coords + `hm:geonames_id`), VIAF
(`hm:viaf_id` + cluster `owl:sameAs`), and Wikidata (`hm:wikidata_id` /
`hm:external_wikidata_uri`). Postgres KIMA matching abstains on multi-QID
conflicts exactly like SQLite. `POST …/build-items?refresh_authority=true`
rebuilds RDF + upserts `RdfArtifact` so refreshed matches are not trapped
behind a stale TTL.

The pure helpers in `backend/converter/authority/evidence.py` are intentionally
network-free.

`HmoWikibaseExporter` stores the normalized list on every draft and resolved
entity as `authority_evidence`; the build cache and review API therefore expose
the same accepted/withheld evidence without another network lookup.  It reads
the canonical RDF predicates `hm:wikidata_id`, `hm:external_wikidata_uri`,
`hm:viaf_id`, `hm:kima_id`, `hm:mazal_id`, `hm:external_uri_nli`,
`hm:authority_id`, and `owl:sameAs`.

KIMA and Mazal identifiers are never inferred from arbitrary numbers.  KIMA
requires an explicit `kima_id` source.  Legacy `external_uri_nli`/
`authority_id` values are treated as Mazal only for the validated `987…`
namespace; ordinary NLI/control numbers are ignored.

`backend/scripts/audit_hmo_authority_consistency.py` audits an exported run for
missing external links, duplicate local-QID assignments, and malformed authority
URLs. Run it against large exports before changing reconciliation policy. Callers should pass source/value pairs from the canonical RDF graph or approved authority payload, then persist the returned evidence next to the HMO draft.  A Wikidata QID is always normalized to the external
Wikidata namespace and must never be used as a local Wikibase QID.

Required checks before upload:

- normalize and validate every external identifier;
- deduplicate equivalent URI and bare-ID forms;
- abstain on multiple candidates of the same kind;
- retain rejected candidates and the reason in review evidence;
- derive Wikidata Studio claims only from accepted HMO evidence.
