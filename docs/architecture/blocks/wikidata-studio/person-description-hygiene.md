# Person description hygiene

> Up: [Wikidata Studio](README.md) · Rule W-325

The 12 November 2026 report identified repeated role descriptions on Q141953242.
The RDF person can have separate comments for each role and manuscript record.
The HMO exporter joins those comments with ` · ` and truncates the result.
The previous Wikidata filter recognized a single generic suffix but missed this joined, truncated form.

The canonical adapter now recognizes the generated `Person (role) linked to manuscript` prefix.
It excludes that text from the public description.
It uses the normal source-enriched description or the concise person fallback.
It preserves a specific biographical description.
It retains the source comments and role evidence.

The canonical fingerprint uses `hmo-wikidata-v28`.
A normal rebuild invalidates the previous description projection.
The tests in `backend/tests/unit/test_hmo_canonical_wikidata.py` cover the reported aggregate and a specific biography.
The tests also check that the adapter preserves the source and returns a stable result.
The fix does not establish a person's identity or authorize an edit to a foreign entity.
