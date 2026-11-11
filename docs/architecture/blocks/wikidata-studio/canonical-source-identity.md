# Canonical work source identity

> Up: [Wikidata Studio](README.md) · Rule W-308

**R161 — Current source evidence must corroborate an author-qualified work URI.**
The canonical adapter regenerates the full local URI with `UriGenerator.work_uri`.
It uses the raw source title and the exact title transforms from `GraphBuilder._add_work`.
The transforms sanitize, shorten, and add manuscript scope where necessary.
The adapter compares the result with the complete source URI.
It then limits the work to the matching source records.
It uses P2093 for the source name and does not infer a public author QID from the URI.
It binds the work evidence to one corroborated source record.
An unsupported author-qualified URI cannot create a work.
A title that itself contains `by` retains the authorless-title path.

The canonical cache salt is `hmo-wikidata-v23`.
`local_measure_verify.py` now checks native and serialized output with the production export gate.
The complete audit must pass that gate before it reports success.
The export gate initializes the item builder before the content mixin in a new process.

Tests: `test_canonical_work_uri_author_scope.py` checks distinct authors and absent source proof.
It also uses the real graph producer for scoped short titles and long ISBD titles.
See [source context](../eval-agent/source-context.md) for the immutable context boundary.
