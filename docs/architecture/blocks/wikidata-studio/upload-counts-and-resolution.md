# Upload counts and source resolution

> Up: [Wikidata Studio](README.md) · Rules W-315–W-317

## Upload state

`wikidataItemDataStatus.ts` assigns one upload state to each review row.
The header and the filter use the same resolver and readable labels.
A successful `adopt` needs a message that confirms an update.
A reconcile-only adoption remains unconfirmed.
A blocked row needs source review, including a row without a QID.
An intentional skip to an existing QID means reference-only use.
A failed upload remains a failure.
A source with `rebuild_required` cannot receive current upload confirmation.
The AI review count reports non-pass verdicts separately.

`WikidataReviewRefreshTracker` records terminal job IDs for the current run and source.
A new terminal live upload or AI verify job causes one silent table reload.
The explicit upload target takes precedence over the legacy dry-run flag.
Historical terminal rows do not cause a reload loop.
The table rejects late responses from earlier loads.

## Complete connection payload

`wikidata_deferred_payload.py` builds pass two from the complete native source.
The helper resolves all known local targets and retains all other source facts.
Unknown targets remain outside this payload.
The helper combines complete reference blocks for equivalent claims.
The helper requires P31 and refuses an unresolved final connection.
The normal uploader retains all ownership, target, namespace, and source guards.
Deploy this shared backend code to Heroku and Modal before a repair upload.

## Source audit

`compare_gila_source_facts` accepts explicit `approved_author_qids` proof.
Each key combines the exact source control number and normalized author heading.
Exactly one approved public QID must match an active public P50.
A native author name alone does not supply identity approval.
Doubled quote transport forms normalize; uncertainty marks remain exact.
Deferred notes remain source evidence.
Structured part titles retain identity review until the parent identity is clear.

## Verification

- `test_wikidata_deferred_payload.py` checks the complete source and unresolved targets.
- `test_wikidata_uploader_reference_blocks.py` checks the earlier relation and all reference blocks.
- `test_wikidata_upload_job_test_namespace.py` checks live/test separation through both passes.
- `test_wikidata_gila_identity_audit.py` checks exact author proof and contrary cases.
- `wikidataItemDataStatus.spec.ts` checks receipt states and count totals.
- `wikidataReviewRefresh.spec.ts` checks terminal transitions and exact scopes.
- `wikidata-studio.spec.ts` checks readable filter values and upload counts.

## Operator checks

Read current source records and public entities before a source identity choice.
Preserve existing curator edits and use the true base statement indices.
Use the normal versioned API for source edits.
Use exact target dry runs before short live upload jobs.
Check public revisions and complete reference blocks after each repair.
Refresh the browser and compare the header with the filter counts.
Do not convert an unknown source identity into a successful upload count.
