# Exact scope for source choices

> Up: [Wikidata Studio](README.md) · Rules W-331 and W-332

`wikidata_source_choices.py` applies a local choice to its exact source evidence.
A title and folio identify the work claim scope; P3959 identifies the source record.
The code reads record IDs from complete reference blocks and compatible legacy reference fields.

## Work choices

A changed target requires references from the selected record only.
A shared claim or unproven record scope requires review.
An unchanged target can retain other complete source blocks and accept the selected source.
Conflicting decisions for one title/folio scope stop before any claim change.
The code never chooses a public target from a title alone.

## Bibliographic choices

P1343 uses the exact publication target and P304 page scope.
A rejected match cannot remove other page ranges or another record's citation.
A shared claim requires review before removal.
A compatible selected claim receives the selected record's reference if it lacks that reference.
The merge preserves complete source blocks.

## Library choices

Conflicting library/shelfmark choices across records require review.
A replacement of a current claim requires exact record scope.
An unchanged claim retains its rank and complete references.
The merge adds a missing selected source without a replacement of the claim.
Claims with P580, P582, or P585 retain their historical scope.
Multiple matching claims require review.

## Import and cache boundary

The source candidate reader imports `structured_work_attribution` through `item_builder`.
The facade export avoids a cycle in a fresh interpreter.
The build versions are `source-aware-works-v24` and `hmo-wikidata-v28`.
Older cached projections require a rebuild.
This change adds no route, model field, job kind, or foreign-edit exception.

Tests: `backend/tests/unit/test_wikidata_source_choices.py` and `backend/tests/test_wikidata_source_review.py`.
