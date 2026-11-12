# Source repair safeguards

> Up: [Wikidata Studio](README.md) · Rules W-311–W-314

## Credited authors

A new P50 from a source P2093 retains the exact source name in P1932.
The conversion retains the rank, qualifiers, and complete reference blocks.
An existing compatible P50 retains its established scope.
This rule applies to public and local person targets.

## Colophon duplicates

The canonical sanitizer removes an exact wrapper after the curator overrides.
The position preserves the base indices of those overrides.
The source record, language, rank, role, folio, and exact text must agree.
One unique scoped counterpart must exist.
The sanitizer first combines the complete reference blocks.
An ambiguous or uncertain comparison retains both claims.
The canonical cache salt is `hmo-wikidata-v24`.

## Approved foreign manuscript ranks

An exact QID-bound approval permits an additive update on a foreign manuscript.
For P31, one exact existing claim supplies its normal or preferred rank.
The value and all qualifiers must match.
The uploader preserves the existing GUID, rank, metadata, and references.
Duplicate existing matches remain an error.
Owned items, other properties, deprecated ranks, and unapproved targets retain their existing guards.

## Source build without optional prose extraction

A `wikidata_studio_build` job accepts `extract_provenance_prose=false`.
The parameter must be a JSON boolean.
The default remains `true`.
The false value skips only the optional AI phase after the source cache save.
The build retains the source checks, quality gate, cache save, and normal success receipt.
The progress publisher and heartbeat remain active through the complete job.
A cancel request still prevents a successful finish.

## Operator check

Deploy the committed backend to Heroku and Modal before the source rebuild.
Use a normal canonical build with `force_rebuild=true`.
Use `extract_provenance_prose=false` for a source-only repair check.
Export the rebuilt source and check every changed fact before a live upload.
Use a short dry-run job before each short live job.
A refusal before a remote write can permit a corrected retry.
An uncertain remote outcome requires a fresh entity and history check first.

## Tests

- `test_wikidata_canonical_safety.py` checks exact credited names and complete source evidence.
- `test_wikidata_colophon_projection.py` checks wrapper equivalence, ambiguity, and override positions.
- `test_wikidata_uploader_reference_blocks.py` checks foreign ranks and the unchanged refusal cases.
- `test_wikidata_studio_build_job.py` checks the optional phase, boolean input, heartbeat, and progress.
