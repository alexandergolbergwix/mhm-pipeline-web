# Repeated retrievals of one source

> Up: [Wikidata Studio](README.md) · Rule W-323

The live uploader treats a retrieval date as source metadata.
It retains one reference when every other source snak matches exactly.
The retained reference has the latest recorded date.
An older incoming date cannot replace a newer date.
The code does not invent a date or perform a source fetch.

## Eligibility

The reference must have a URL or an external identifier.
It must have exactly one Gregorian P813 value with day precision.
Its timezone, before, and after values must be zero.
Every other source snak must match, including pages, quotations, identifiers, and publication dates.
The code preserves different sources and ambiguous retrieval dates.
Reference hashes and snak order do not establish separate evidence.

Normal updates of owned items can replace repeated blocks with the current reference.
Owner-only paths preserve existing blocks and skip another copy of the same source.
Those paths do not update the existing retrieval date.
Rule W-324 blocks every foreign write. Historical append logic remains read-only preview code.
Test-wiki paths use exact comparison because live P813 does not identify a test property.
Claim values, qualifiers, and ranks retain their existing guards.

## Historical proposals

The read-only proposal builder uses `coalesce_retrieval_references` for eligible date variants.
It retains `deduplicate_exact_references` for exact repeats.
Every proposal binds the QID, revision, entity digest, and complete before/after claims.
An explicit redirect response cannot supply a repair for the requested QID.
The [historical executor](historical-repairs.md) must still check source scope and ownership.
A proposal does not grant write permission or establish that every target belongs to the project.
The completion packet retains these cases as repairs until a current verified receipt closes them.

## Public lag check

A public read-only POST can test the same lag gate without an edit or an authenticated session.
Use `action=wbgetentities`, `props=info`, one public QID, and `maxlag=5`.
A successful GET alone does not prove that the POST lag gate will permit an edit.
Keep `maxlag=5` on bulk edits and respect `Retry-After` after a confirmed rejection.
Check the exact entity after an uncertain outcome before another attempt.
MediaWiki permits individual interactive requests to omit `maxlag`; this exception does not apply to an unattended bulk process.
See the [official guidance](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter)
and the [verified cleanup receipts](../../../audits/creator-scoped-cleanup-2026-11-12.md).

## Files and tests

- `backend/converter/wikidata/reference_retrieval.py`: pure reference selection and source identity.
- `backend/converter/wikidata/uploader.py`: normal, partial, and append-only upload paths.
- `backend/app/pipeline/wikidata_gila_repairs.py`: exact proposals and validation.
- `backend/tests/unit/test_wikidata_reference_retrieval.py`: screenshot case, source boundaries, date uncertainty, and exact proposals.
- `backend/tests/unit/test_wikidata_uploader_reference_blocks.py`: actual claim objects, ownership paths, and repeat-update stability.

The [12 November audit](../../../audits/gila-reference-retrievals-2026-11-12.md) records the public example and complete corpus counts.
