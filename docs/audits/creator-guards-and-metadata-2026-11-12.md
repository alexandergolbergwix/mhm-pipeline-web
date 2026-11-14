# Creator guards and repeated metadata

The local code now requires creator proof for every update to an existing Wikidata entity.
The change also corrects joined person descriptions and tests the additional title-reference example.
The agent did not deploy the code or edit a public entity.

## Creator-only policy

The user required an unconditional rule: never edit an entity from another account.
The previous code permitted an exact foreign QID after curator approval.
That exception now has no authority.

- The uploader checks the authenticated account and first-revision author before the build and before the send.
- A cached owned classification cannot replace creator proof.
- Bulk upload rejects foreign and unknown prepared targets, including stale prepared data.
- The deferred pass cannot update a foreign source.
- Publication plans block foreign updates even with historical consent.
- The Publication gateway checks creator proof before every send and checks the actual target.
- Historical repairs use the same creator-only guard.
- The item override API rejects new foreign approvals with HTTP 422.
- The UI removes manual and AI actions that grant foreign edit permission.
- QuickStatements emits no commands against an existing QID because it has no authenticated creator proof.

Existing QIDs remain available as statement targets through the normal identity checks.
Reference-only reuse remains available.
The policy grants no permission to create a duplicate after a blocked update.
Earlier correction proposals remain subject to this stricter policy.
A merge proposal cannot authorize an edit to a foreign recipient.

See [the creator-only contract](../architecture/blocks/wikidata-studio/creator-only-writes.md), Rule W-324.

## Repeated person description

[Q141953242](https://www.wikidata.org/wiki/Q141953242) contains a list of roles and manuscript identifiers in its English description.
The HMO exporter joins a separate RDF comment for each role and manuscript.
It joins those comments with ` · ` and truncates the result.
The previous Wikidata filter recognized a single generic description but missed the joined form.

The canonical adapter now excludes that generated person-role prefix.
It selects a source-enriched description or the concise person fallback.
It preserves specific biographies, source comments, and role evidence.
The canonical version advances to `hmo-wikidata-v26`, so the normal rebuild invalidates the previous projection.

The tests cover the exact reported aggregate, source preservation, repeat stability, and a specific biography.
See [description hygiene](../architecture/blocks/wikidata-studio/person-description-hygiene.md), Rule W-325.

## Repeated title references

[Q141747600, revision 2568070012](https://www.wikidata.org/w/index.php?title=Q141747600&oldid=2568070012) has four references on its Hebrew title.
They cite the same NLI record `990001285000205171` and the same source URL.
Their retrieval dates are 3, 5, 10, and 11 November 2026.
The earlier uploader treated each date as a separate reference identity.

The existing local reference fix selects one citation with the latest recorded date, 11 November 2026.
The additional regression test covers these four dates and this exact source URL.
The complete audit already lists this claim with four blocks before selection and one retained block.
Its claim ID is `Q141747600$7E9C74EA-B631-4FE0-A0C9-425448DCE12C`.

The agent did not change that public claim.
The [reference rule](../architecture/blocks/wikidata-studio/reference-retrievals.md) preserves distinct sources and ambiguous dates.
The new creator guard also applies to any future reference cleanup.

## Validation

The broad backend check passed 2,393 tests and reported one author-reference failure.
That test also fails in a separate process with the unchanged `HEAD` canonical module.
It is `test_wikidata_canonical_enrichment.py::test_merge_prefers_safe_p50_over_legacy_p2093`.
The failure concerns duplicate P50 reference representations. This change does not alter that merge function.

All new creator, API rejection, person-description, and title-reference cases passed.
Ruff reports no finding on the changed Python lines or the new guard test file.
`git diff --check` passes.
Seven modified TypeScript files pass syntax and unused-symbol checks.
Full frontend type, unit, and browser checks remain unverified because the frontend dependencies are absent.
Yarn could not install them because the registry connection returned `EBADF`.
The updated UI specs cover old approval data, the absence of foreign-edit controls, and reference-only reuse.

The local validation receipt is `state/creator-guards-2026-11-12/validation-receipt.json`.
The backend logs are `/tmp/creator-guard-final-tests.log` and `/tmp/creator-guard-baseline-test.log`.
The TypeScript check log is `/tmp/creator-guard-ts-final.log`.

## Trellis candidate

Capture `059250bc-2443-471a-8e08-a4e9a6baaf7d` records the creator-only policy.
Proposal kind: ambiguous. Target: none. Review: pending.
The capture is a candidate, not stable knowledge.
