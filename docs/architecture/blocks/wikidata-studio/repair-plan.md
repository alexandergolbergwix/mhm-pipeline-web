# Repair plan

The separate page is `/runs/:runId/wikidata-studio/repair-plan`.
The Studio header links to the page.
The page retains the normal authenticated session.

## Endpoint

`GET /api/runs/{run_id}/wikidata-studio/repair-plan`
requires project access through `_lookup_run_with_access`.
The endpoint reads the saved canonical build with `approved_only=true`.
It does not call a public service or change a source, draft, approval, or public item.
The session middleware can update its normal session timestamp.
The endpoint has no execution method or special-key exception.

The endpoint accepts `page_size` from 1 to 100 and a `cursor` with at most 256 characters.
The default page size is 50.
The response contains the total item count and the next cursor.
It contains saved validation codes and QID-valued positions in local edits.
A QID-valued position requires review. It does not prove an invalid catalogue code.
The response omits the complete cache, item statements, credentials, and source-choice proofs.

The `build_token` binds the cache ID, build date, and input fingerprint.
The next page sends `expected_build_token`.
A changed token returns HTTP 409.
The endpoint also checks the build metadata after its page read.
This check does not replace source freshness checks in the normal write path.
Local edits can change between page requests.

## Operator steps

1. Open the repair plan for the run.
2. Inspect the saved validation codes and positional edits.
3. Open Studio through the page link.
4. Review the source and correct the local draft through the normal controls.
5. Rebuild the canonical items.
6. Run the quality checks.
7. Review the Publication release.
8. Run the Publication dry run.
9. Publish the reviewed changes through the normal controls.
10. Verify the public result and retain the receipt.

The page lists these steps. It does not execute them.
The response always reports `mode=read_only` and `public_verification=not_checked`.
No percentage or empty page certifies Gila completion.
Foreign-created items remain read-only relation targets.
Normal public writes retain fresh creator proof for UID 6935067.
Automatic reference repairs retain the edit limit for their path and the existing retry state.
This page does not remove an access block or grant a foreground exception.

## Files and tests

- `backend/app/routers/wikidata_repair_plan.py`: bounded read endpoint and response models.
- `backend/tests/test_wikidata_repair_plan.py`: scope, auth, page limits, SQL reads, and stale-build tests.
- `frontend/src/api/wikidataRepairPlan.ts`: typed client through `api/client.ts`.
- `frontend/src/routes/WikidataRepairPlan.tsx`: plan page and normal Studio link.
- `frontend/e2e/wikidata-repair-plan.spec.ts`: mocked page reads, cursor, refresh, and error tests.

The production repair inventory now requires an administrator in both `AdminRoute` and its API dependency. The separate [backend controls](admin-reference-repairs.md) own execution.
