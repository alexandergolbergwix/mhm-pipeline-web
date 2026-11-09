# Owner-Only Claim Safety

> Up: [Owner-only upload](owner-only-upload.md) | Rule W-303

## Claim Guards

The owner-only cached-item path skips the general lossy upload preprocessing.
An earlier Studio build can still omit or degrade local links. The uploader
therefore blocks any surviving `__LOCAL:` reference and any `P1574=Q234460`
generic text claim. It does not remove remote claims merely because a source
property is absent. It skips both the deferred-link cleanup and the stale
property cleanup that the general owned-item path can perform.

The owner-only branch never uses `REPLACE_ALL`. When the proposed item has a
built `P31`, it blocks a property if any existing claim has no semantic match in
the built claims. Qualifier identity preserves duplicate counts and ignores
order and transport hashes. Without built `P31`, the
branch can append a new claim without that complete-property check. It retains
existing claims in both cases. Both preview and live preparation use the same
branch.

## Metadata And Counts

For each proposed language, the uploader blocks a change to a non-empty
existing label or description. It also blocks empty or malformed values,
unreadable metadata, and a label equal to the resulting description. Existing
descriptions are not removed to resolve a collision. The uploader can add a
label or description where none exists. It validates aliases and can add new
aliases without removing existing ones.

For claims, the uploader blocks invalid native ranks, ambiguous matches,
incompatible snak types, different qualifiers for the same snak, and a rank
change on a matching claim. Matching claims retain their rank and existing
reference objects. The uploader appends distinct proposed references; it does
not deduplicate existing reference objects. Invalid proposed qualifiers or
reference snaks block the item. Owner-only reference construction accepts
`item`, `url`, `time`, `string`, and `external-id`. It constructs one
reference per explicit block and deduplicates snaks within that block (W-304).
Old flat multi-snak data must pass the upstream provenance gate; that gate
rejects it rather than guessing its source boundaries.
Quantity claims reject noncanonical or unsupported units instead of silently
using a dimensionless unit.

The branch records each appended claim and each added reference in
`added_properties`. It also records a `labels` marker for label or description
changes and an `aliases` marker for alias changes. For existing items,
`new_claims` is the length of that list. It is a change count, not a count of
distinct new claims or properties. The edit summary calls it `claims`. When
the count is zero, the uploader checks native claim presence before returning
`exists`; an alias-only or reference-only addition can trigger a write.

## Limits

The job loads a local Studio snapshot and overrides before processing items.
The job does not bind them to a revision or re-read them immediately before
each write. A later local change can therefore leave the job with stale data.
Remote `baserevid` protects the checked Wikidata revision, not local freshness.
The claim guard cannot prove that a local snapshot contains all source data.
It cannot establish the accuracy of source claims. The rank guard validates
the native claim; conversion outside the strict `P3959` path can normalize
source ranks before this guard sees them. `P3959` itself requires `normal` or
`preferred`; other native claims also accept `deprecated`.

Strict audit persistence occurs after the item outcome. If the worker exits
after a remote write but before audit or job persistence, neither record is
guaranteed to exist. A missing audit row is not proof that no write occurred.
Inspect the remote item before retrying an interrupted or uncertain outcome.

`test_wikidata_owner_preprocessing.py` tests omitted and partial source claims,
explicit additions, generic text, and the non-owner comparison. These tests use
mocked remote writes. `test_wikidata_owner_only.py` tests the `P3959` ranks and
reported audit failures. `test_wikidata_owner_metadata.py` tests metadata and
claim preservation with real WikibaseIntegrator objects and mocked writes. The
tests do not prove crash recovery, local freshness, or live Wikidata behavior.
