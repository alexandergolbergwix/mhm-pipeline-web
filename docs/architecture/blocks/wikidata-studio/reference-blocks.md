# Wikidata Reference Blocks

> Up: [Wikidata Studio](README.md) | Rule W-304

## Representation

`WikidataStatement.reference_blocks` is a list of source blocks. Each block is
a list of snaks. `references` is the flat compatibility view, not proof of
source boundaries. When blocks exist, both views must contain the same semantic
snaks. The flat view removes duplicate snaks. `snak_identity` ignores display
labels but checks property, type, value, and relevant metadata. See
`converter/wikidata/item_models.py` and `quickstatements.py`.

Fresh NLI, NLI authority, and VIAF constructors return a process-local
`FreshSourceReference`. Its private token and unchanged snapshot distinguish a
new source from imported flat data. A statement can use this marker to create
one block only when it has no existing reference evidence. Copying the list to
a plain list or restoring JSON does not recreate the marker. Fresh item snaks
carry `value_namespace: "wikidata"`; the marker is not a general proof for old
data. The explicit heuristic extension permits only one valid `P887` item snak.
`mark_fresh_live_reference` also requires the unchanged marker and an exact
single-block match; it cannot certify an arbitrary old list.

## Merge And Transport

Canonical enrichment and shard merge retain distinct complete source blocks for
compatible statements. They deduplicate semantically equal blocks and preserve
qualifier and rank distinctions. Serialization carries both views through native
items, Studio cache and rows, overrides, Publication documents, and deferred
statements. Alias and local-target rewrites must update both views. Different
claims or uncertain identities are not proof that source evidence can merge.

Old flat references with more than one snak cannot establish source boundaries.
The Studio cache marks them stale; upload preparation and Publication source and
gateway validation reject them. A merge also rejects them. Rebuild from source
instead of guessing a grouping. An empty flat list or one flat snak can pass
this particular boundary check; other validation still applies. Malformed or
inconsistent views and empty blocks fail validation. The low-level
`WikidataUploader._build_claim` can interpret a flat list as one block, so it is
not a replacement for the preceding upload or Publication validation gate.

## Writes And Exports

The uploader constructs one Wikibase reference per block. Reference-only changes
can cause an update; owned updates retain existing references and add distinct
ones. The regular two-pass job defers a whole statement when a main value,
qualifier, or reference has an unresolved local target. Pass 2 resolves its
targets and retains the blocks; unresolved targets remain outcomes, not partial
reference writes. Owner-only rejects local targets instead of deferring them.

QuickStatements uses `!` before the first snak of each later block. It omits a
statement with ambiguous flat multi-snak provenance or an unresolved local
reference and emits a comment. QuickStatements is not a full upload receipt.
Section CSV retains `statements_json`, including blocks and built overrides.
An inconsistent cached override can appear in that export with
`REFERENCE_PROVENANCE_UNVERIFIED`; export is not write authorization.

Publication validates `statements`, `claims`, and `deferred_statements`. Only
the deferred field permits unresolved local references at the gateway. Profile
`1-nodes` retains deferred statements in the sealed Release; a later Release
must add the connections. Older sealed Releases cannot be repaired in place.
Prepare and review a new Release from a valid current source when needed.

## Reference-Only Patches

A `statement_edits` patch that sets only `references` or `reference_blocks`
validates in the built-statement context. `_reference_source_with_override`
(`backend/app/routers/wikidata_studio.py`) merges the patch into the built
statement at the edit index. A patch whose index matches no built statement
stays an unbound extra entry. The provenance check then runs on the merged source.
A patch never passes by checking its own fields alone.

## Test Wiki And Limits

Test Wikidata P/Q numbers do not identify live Wikidata entities. Item reference
values require an explicit live namespace or URI, or local target evidence
verified against the test ledger. The current test session must remap and verify
the target before claim construction. A bare QID is ambiguous; an unverified
local or mapped target blocks. Test reference calendar remapping is unsupported
when a calendar is explicit. Unsupported or unremapped reference snaks block the
item rather than being discarded. Test remapping may create test stubs.

`audit_test_wikidata_upload.py` adds `test_reference_blocks_unverified` when a
written test item has native references. It does not certify live source blocks
from a test write. The current frontend `Snak` type and legacy statement view use the
flat `references` field and do not display block boundaries. A snak count is not
a source count. Review the JSON or remote claims for source grouping.

These are local code contracts, not evidence of deployed code, a successful
live write, or complete correction of historical Wikidata data. Old flat data
cannot recover its original grouping. See [Gila audit risks](gila-audit-risks.md)
for remaining work-identity and folio limitations.
