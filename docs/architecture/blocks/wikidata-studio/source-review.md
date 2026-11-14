# Source choices and requirement coverage

> Up: [Wikidata Studio](README.md) · Rules W-327, W-329, and W-330

`wikidata_source_choices.py` defines source-specific decisions for a manuscript.
`wikidata_source_coverage.py` inventories original passages and the nine email requirements.
`wikidata_source_review.py` exposes the authenticated local API.
The item drawer uses `WikidataSourceReview.tsx` and `wikidataSourceReview.ts`.

## API

`GET /runs/{run_id}/wikidata-studio/source-review?local_id=...` returns source records and candidates.
Each candidate retains the control number, source key, source text, folio, and source hash.
The response also exposes citations, catalogue-status notes, source gaps, and stale choices.
The drawer shows draft targets only when the title and folio identify that occurrence.
It links the exact catalogue record beside the source text.
The authority approval filter remains distinct from item approval.
The endpoint does not require item approval.
The [record/page guards](source-choice-scope.md) restrict each decision to its proven source scope.
The UI binds each view to the run and local item ID.
It resets form state on a scope change and ignores results after that view unmounts.
An accepted local save still belongs to its original source; this rule does not cancel it.

`POST /runs/{run_id}/wikidata-studio/source-choice` accepts these fields:

- `local_id`, `control_number`, `candidate_key`, and `expected_source_hash` identify the exact source.
- `target_qid` names an existing work, library, or bibliographic publication.
- `evidence_uri` records an HTTP evidence URL without credentials.
- `decision` selects a match, rejects a match, or documents an unresolved passage.
- `reason` explains each curator decision.
- `discard` clears the exact choice, including a stale choice.

The endpoint never fetches an arbitrary evidence URL.
It reads target identities only from the fixed public Wikidata API.
It requires a compatible work class, exact title evidence, and compatible author evidence.
A title alone needs the exact source control number in the public references.
A physical manuscript cannot serve as the work target.
A rejected work match remains an unresolved occurrence; it does not create a work.
Distinct folios remain distinct claims.
An ambiguous source scope or more than one matching claim requires review.
Only one current-library choice may exist for each control number.
A later explicit choice replaces the previous choice; imports reject conflicting choices.

Bibliographic choices require a compatible publication year or an exact ISBN/DOI identifier.
An explicit author must match the public author evidence.
The builder emits [P1343](https://www.wikidata.org/wiki/Property:P1343) with [P304](https://www.wikidata.org/wiki/Property:P304) when the citation proves pages.
The builder retains the complete original citation when identity remains unresolved.
It never converts a citation sentence into a catalogue number.

## Proof and history

Migration `0055_source_review_choices` adds nullable JSONB on `wikidata_item_overrides`.
The endpoint rechecks and locks the source after the public lookup.
It records the full proof through `apply_event` before the row update, in one transaction.
It clears item approval and the old verdicts.
The response requires a rebuild and a new review.
The generic item PATCH rejects proof fields.

Canonical and legacy builds validate the source hash and candidate again.
A source change or a missing source item stops the build.
Imports re-prove choices against current source records and current public identities.
JSON exports retain choices; diagnostic CSV retains their complete JSON.
History can restore or clear choices explicitly.
Publication and the uploader recheck selected public identities before a send.
An existing foreign work remains a relation target; the choice never authorizes an edit to it.

## Coverage

The drawer shows a separate state for each of the nine requirements.
A source gap differs from an unresolved identity, a source review, and an unchecked passage.
A local choice does not count as a public correction.
A documented unresolved passage remains unresolved.
The completion packet separates public reads, verified claims, source gaps, identity reviews, pending repairs, and unchecked passages.
The audit rechecks source-choice proof and current target metadata.
It uses that proof for selected libraries, work identities, and citations.
A matching public value still needs exact scope and source references.
An uncertain 942 association remains a manual review case.
The audit cannot propose a label replacement from an unproven city association.
`source-passages.csv` retains the passage inventory.
Point 9 cannot pass merely because every manuscript has a public read.
Point 2 retains the difference between Gila's wording and the separate manuscript/work model.

P50 promotion compares compatible work occurrences before it adds an optional credited name.
One author claim retains both distinct source blocks and the original scope qualifiers.
Current build versions: `source-aware-works-v24` and `hmo-wikidata-v28`.

Tests: `test_wikidata_source_choices.py`, `test_wikidata_source_review.py`, and `wikidataSourceReview.spec.tsx`.
