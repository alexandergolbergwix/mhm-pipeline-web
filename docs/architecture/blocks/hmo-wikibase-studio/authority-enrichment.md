# HMO authority evidence

HMO enrichment must be fail-closed.  Matchers may provide Mazal, KIMA, VIAF,
or Wikidata candidates, but a candidate is a public authority claim only when
its identifier is valid and there is one unambiguous identifier for that
authority kind.  Conflicting QIDs remain in `authority_evidence` with
`accepted: false` so curators can inspect them without risking a false
positive Wikibase claim.

The pure helpers in `backend/converter/authority/evidence.py` are intentionally
network-free.  Callers should pass source/value pairs from the canonical RDF
graph or approved authority payload, then persist the returned evidence next
to the HMO draft.  A Wikidata QID is always normalized to the external
Wikidata namespace and must never be used as a local Wikibase QID.

Required checks before upload:

- normalize and validate every external identifier;
- deduplicate equivalent URI and bare-ID forms;
- abstain on multiple candidates of the same kind;
- retain rejected candidates and the reason in review evidence;
- derive Wikidata Studio claims only from accepted HMO evidence.
