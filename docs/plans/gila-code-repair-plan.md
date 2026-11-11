# Gila audit code repair plan

Date: 11 November 2026.
Status: local implementation and historical review batches complete on `codex/gila-code-repair`.
Deployment and live corrections remain separate actions.

## Implementation progress

- The initial 164 tests passed before code changes.
- Five new full-path regressions failed against the original code.
- The code preserves repeated folios, distinct authors, and script source wording.
- The code now separates bibliography prose from explicit catalogue codes.
- The canonical author path uses the verified authority matcher.
- The final corpus contains 897 manuscripts and 10,518 expected source facts.
- The final comparison has zero unexplained source-fact losses and zero validation-error items.
- The backend unit suite passed 2,240 tests. The integration and cache suites passed 92 tests.
- Two frontend behavior tests passed with local cached dependencies. The exact dependency build remains unverified.
- Migration 0053, architecture rules, per-fact exports, and revision-bound historical proposals are present.
- Source uncertainty, unresolved local targets, and historical live repairs remain explicit review cases.
- See [the implementation receipt](../audits/gila-code-implementation-2026-11-11.md) for evidence and limits.

The objective is to remove every reproducible code defect from Gila's audit and prevent the defects from recurring.
The code must preserve source meaning, source scope, uncertainty, and references through the final upload payload.
Historical Wikidata repairs require separate, explicit claim changes after the code passes its checks.
Unresolved scholarly identities require review; the code must expose those cases without an invented identity.

## Baseline and limits

Use [the full audit](../audits/gila-live-wikidata-2026-11-11.md) and its CSV receipts as the initial evidence.
The audit covers 897 source records, 897 live manuscripts, and 794 linked works.
Its live snapshot covers 11 November 2026, 12:00–12:03 Israel time.
Code inspection used commit `e407512d` as its baseline. Recheck HEAD before implementation.

| Evidence | Initial result | Interpretation |
|---|---:|---|
| Exact duplicate references | 862 blocks on 55 entities | Historical defects; verify the current write paths before another code change. |
| Component links below the local proposal | 355 links across 198 manuscripts | Trace each difference; the legacy proposal is not an independent correctness oracle. |
| Folio-qualified links below the proposal | 56 links across 28 manuscripts | Check occurrence identity, canonical merge, and deferred local targets. |
| Mixed historical work | `Q141175543` | Repair the source partition; prevent recurrence through identity checks. |
| Duplicate Sukkah colophon | One old defective statement | Produce an explicit replacement/removal proposal with the correct source text. |
| Script detail loss | `קראית-אשכנזית` | Preserve the complete source term in both build paths and the public statement. |
| Absent source fields | 90 cases | Retain the absence; do not manufacture values. |
| Identity or scope cases | 1,220 cases | Expose a review decision and its evidence for each case. |
| Missing current ledger entry | `Q141754492` | Verify the exact source mapping before a separate local ledger repair. |

Keep the accepted distinction between physical manuscripts and written works.
Keep `Q47461344` on work entities. Use supported physical classes on manuscript entities.
Keep the current fixes for shelfmarks, dates, Ktiv membership, named unknown scribes, and Cantarini's author link.
Commits `2873b5b9` and `e407512d` already address ownership, unknown-value claim comparison, and owned work label conflicts.
Test those behaviors before changing them again.

## Implementation order

| Step | Work | Dependency | Completion evidence |
|---|---|---|---|
| 1 | Reproduce defects and trace each source fact | Audit receipts | Independent fixtures and an explicit cause for each missing fact. |
| 2 | Repair work identity and author trust | Step 1 | No cross-author merge or ambiguous alias rewrite. |
| 3 | Preserve components, folios, and roles | Step 2 | Source occurrences survive both build paths and the final payload. |
| 4 | Preserve references and repair historical statements | Steps 1–3 | Complete source blocks and explicit, revision-bound repair proposals. |
| 5 | Preserve script detail and other source meanings | Steps 2–3 | Specific script text, correct scope, and valid bibliography representation. |
| 6 | Extend the audit and review report | Steps 1–5 | Every expected fact has a verified result or an explicit review reason. |
| 7 | Complete integration, cache changes, and documentation | Steps 2–6 | Applicable tests pass; fresh builds use the corrected contracts. |
| 8 | Prepare separate live correction batches | Step 7 | Reviewable exact changes, then authorized writes and live verification. |

## Step 1 Reproduce and trace the defects

Use the pinned audit inputs first. Refresh live revisions before a repair proposal or a write.
Create small source fixtures for the three manuscripts, repeated titles, conflicting authors, and distinct reference blocks.
Derive expected facts from reviewed MARC passages, independently of the builder under test.

Trace each fact through these boundaries:

`MARC → prepared record → legacy proposal → canonical merge → native item → cache → upload preparation → deferred link → live claim`

Record the source field, source text, control number, occurrence, expected scope, and actual output at each boundary.
Classify each link difference as a projection defect, identity block, approval block, absent target, upload failure, or source-review case.
Inspect the actual canonical Studio output and per-item/per-link outcomes before assigning a code defect.
Do not treat all 355 proposed links as approved additions.

Extend `backend/app/pipeline/wikidata_gila_audit.py` and `backend/scripts/local_measure_verify.py` for reproducible evidence export.
Use the normal read-only database path. Keep production cache and verdict writes outside this measurement mode.

Exit criterion: every confirmed defect has a failing regression test or a documented historical-only cause.

## Step 2 Repair work identity and author trust

Primary files:
`backend/app/pipeline/wikidata_canonical_enrichment.py`, `backend/app/pipeline/hmo_canonical_wikidata.py`,
`backend/converter/wikidata/work_projection.py`, `backend/converter/wikidata/work_author_claims.py`,
`backend/converter/wikidata/reconciler.py`, and `backend/app/pipeline/wikidata_qid_ledger.py`.

- Prevent one legacy work from supplying incompatible identities to multiple canonical works.
- Validate an alias before it redirects a manuscript's local work reference.
- Use exact source and authority evidence for identity. A shared title or QID alone cannot override an author conflict.
- Use the same author checks before canonical merge, upload reconciliation, and deferred-link resolution.
- Preserve supported public and identifier-backed local author targets through final sanitation.
- Reject a mismatched authority heading or a failed guard, even when an authority row has approval.
- Retain the supported sole-work author fallback. Do not assign the main author to an unrelated supplement.
- Separate explicit rejection from a pending approval. `approved=false` alone does not establish rejection.
- Reuse existing curator events or explicit rejection metadata where they establish that distinction.
- Add a narrow status migration only if the existing evidence cannot express the distinction.

Test Cantarini against Abraham, identical titles with different authors, a valid shared work across manuscripts, and conflicting alias targets.
Test a mismatched approved author, a valid local author target, and the sole-work fallback through the complete canonical build.
Test unresolved and explicitly rejected identities separately.

Exit criterion: no ambiguous identity can enter `P50`, `P1574`, an alias map, or the global ledger without a review result.

## Step 3 Preserve components folios and roles

Primary files:
`backend/app/pipeline/marc_ingest.py`, `backend/converter/wikidata/content_projection.py`,
`backend/converter/wikidata/person_linking.py`, and `backend/app/pipeline/wikidata_canonical_enrichment.py`.
Apply the same scope contract in `backend/app/pipeline/hmo_canonical_wikidata.py`.

The structured contents path currently uses a title/folio occurrence key.
The NER path still suppresses later occurrences by title.
Use a common occurrence identity with source record, supported work identity, author evidence, folio range, and source occurrence.
Keep work identity separate from occurrence identity: one work can appear in several passages.
Merge repeated evidence for the same occurrence without losing a distinct source.

- Preserve repeated titles with different folios or authors through every projection path.
- Preserve the main work alongside accepted 500/505/740 components.
- Keep uncertain supplements as explicit source evidence or deferred claims until their identity is sufficient.
- Do not create a public work solely to make a coverage count larger.
- Put the letter's sender, recipient, and genre on the supported component or a validated occurrence representation.
- Do not classify the whole Hizzuk Emunah manuscript as a letter because it contains one letter.
- Keep a sermon genre on the sermon component.
- Do not infer physical units from a change of content.
- Preserve folio side and range exactly. Do not convert a folio count into a page count.
- Record a reason for each unresolved deferred target. Do not report a silent pass-2 omission as success.

Use the Hizzuk Emunah letter on `51א–52א` and the Sukkah selections on `21ב–22ב` as end-to-end cases.
Review the two chronicle titles before the code treats them as distinct literary works.
Include same-title/different-folio, same-title/different-author, mixed MARC/NER evidence, and repeated preparation tests.

Exit criterion: every accepted source occurrence survives the final native payload with its correct scope and source references.

## Step 4 Preserve references and prepare historical repairs

Primary files:
`backend/converter/wikidata/item_models.py`, `backend/converter/wikidata/uploader.py`,
`backend/app/pipeline/wikidata_canonical_enrichment.py`, and `backend/app/pipeline/wikidata_upload_job.py`.
Check the separate Publication payload and gateway paths when they consume the same statements.

- Preserve explicit `reference_blocks` through merge, cache, native conversion, and the actual write payload.
- Deduplicate equal blocks within one claim. Preserve every distinct source, retrieval date, qualifier, rank, and value.
- Confirm that a reference-only correction causes an update through each applicable upload path.
- Preserve valid unknown-value claims when the uploader compares existing claims.
- Reject old flat data when its original reference groups cannot be proved.
- Rebuild ambiguous cached data from source. Do not attach a fresh-source marker to old data.
- Keep title-page text and colophon text separate through the canonical merge.
- Preserve Hebrew punctuation. Limit any duplicate comparison to source-verified equivalent inscriptions.

Extend the existing repair proposal format instead of adding an unrestricted cleanup uploader.
Each proposal must bind the QID, expected revision, source records, exact claim IDs, and proposed additions or removals.
Include the before-state, the reason, preserved references, and the expected after-state.
Reject stale revisions or an uncertain previous write outcome. A second run must not repeat an applied change.

Prepare separate proposals for the 55 reference cases, the mixed Derushim work, and the defective Sukkah colophon.
The mixed-work proposal must retain Abraham's supported evidence and remove only reviewed Cantarini-specific contamination.
The colophon proposal must preserve the complete qualified text and folio `21א` before it removes the defective duplicate.
An omitted builder property is not an authorization to delete a historical claim.

Use the normal ownership and exact-QID approval checks for every repair.
Owner-only manuscript mode cannot repair work entities or bypass its restrictions on local references and property replacement.

Exit criterion: all repair proposals preserve distinct facts and sources; no test authorizes a deletion from absence alone.

## Step 5 Preserve script detail and other source meanings

Primary files:
`backend/app/pipeline/marc_ingest.py`, `backend/converter/wikidata/manuscript_projection.py`,
`backend/converter/wikidata/property_mapping.py`, and `backend/app/pipeline/hmo_canonical_wikidata.py`.
Check `backend/converter/config/vocabularies.py` and the RDF/HMO path when they supply the same source terms.

Preserve the complete `958$a` source term alongside its broad normalized class.
For `קראית-אשכנזית`, retain a verified broad script target and qualify it with the original source term through `P1932`.
Do not invent a specific script QID. Verify the property constraints before implementation.
[P1932](https://www.wikidata.org/wiki/Property:P1932) preserves the object's source wording as a qualifier.
Retain this qualified statement through both build paths, cache serialization, export, and upload.

Separate a catalogue code from a prose bibliographic citation.
[P528](https://www.wikidata.org/wiki/Property:P528) represents a catalogue code, normally qualified by its catalogue through `P972`.
Use [P1343](https://www.wikidata.org/wiki/Property:P1343) only when a verified source work describes the item.
Preserve unresolved citation text in HMO and the source report until its public representation is supported.
Classify the 532 current bibliography sets before any migration. Do not treat every `P528` as invalid.
Keep scans, source URLs, catalogue pages, and field-level evidence with their applicable claims.

Review Buchach as a place related through a person or passage.
Do not assert manuscript custody, production, or physical presence from that source alone.
Keep Panevėžys as the explicit Sukkah copy place.
Keep the copy date separate from later events in historical contents.
Retain conflicting source dates for review. Do not select one date automatically.
Preserve the detailed Hebrew colophon date without an unsupported Gregorian day conversion.
Keep Hebrew and Italian language evidence separate from alphabet and script style.
Preserve nonnumeric extent text and the provisional catalogue note without an unsupported physical class or uncertainty claim.

Exit criterion: every specific source detail survives in a supported public claim or explicit source evidence with a review reason.

## Step 6 Extend the audit and review report

Primary files:
`backend/app/pipeline/wikidata_gila_audit.py`, `backend/scripts/local_measure_verify.py`,
and the existing Studio source/evidence export paths.

Compare expected source facts with the final canonical payload and the live claims.
Do not use the legacy proposal as the only expected result.
Check qualified identity, author and role scope, content occurrences, exact folios, script detail, and complete source blocks.
Check descriptions and physical units where the first audit checked only selected fields.
Add explicit historical-defect checks for the mixed work and duplicate inscriptions.

Retain the three source review classes:
`absent_in_source`, `present_not_transferred`, and `identity_or_scope_uncertain`.
Track a historical live defect separately from those source classes.
Track resolved, deferred, blocked, failed, and verified outcomes separately from case classification.
Every deferred case must have source evidence and a specific reason.
Keep pending approval separate from uncertain identity and explicit rejection.

Export one row per manuscript and per affected work, plus a per-fact difference report.
Include source field, scope, expected fact, proposed fact, live fact, QID, revision, claim ID, and review outcome.
Keep `corrected_qids` empty until a separate write receipt and live comparison confirm the correction.
Report the missing ledger entry as a local identity case; do not restore it from a title match.

Exit criterion: zero unexplained fact losses; unresolved source questions remain visible instead of producing a false completion result.

## Step 7 Complete integration and documentation

Run focused regressions after each code change. Then run the affected backend integration suites.
Use real WikibaseIntegrator claim objects for snak, qualifier, reference, and payload tests.
Mock the remote write in unit tests. Do not describe those tests as live verification.

Extend the existing suites where applicable:

- `backend/tests/unit/test_gila_prebor_9_points_audit.py`
- `backend/tests/unit/test_wikidata_studio_works.py`
- `backend/tests/unit/test_wikidata_canonical_enrichment.py`
- `backend/tests/unit/test_hmo_canonical_wikidata.py`
- `backend/tests/unit/test_work_identity_one_record.py`
- `backend/tests/unit/test_marc_script_and_colophon.py`
- `backend/tests/unit/test_wikidata_reference_blocks.py`
- `backend/tests/unit/test_wikidata_uploader_reference_blocks.py`
- `backend/tests/unit/test_wikidata_upload_claim_complete_w191.py`
- `backend/tests/unit/test_wikidata_upload_guards.py`
- `backend/tests/unit/test_wikidata_upload_job_progress.py`

Use the Publication and owner-only suites if a shared change affects those paths.
Test cache and native serialization round trips, two-pass links, and a second execution with no additional changes.
Retain the current 854 shelfmark matches, 897 date matches, 779 Hebrew date texts, and 897 qualified Ktiv memberships.
Retain all 413 named scribe claims on 400 manuscripts, including the uncertainty qualifier on `Q141748424`.
Do not use those counts as substitutes for per-fact checks.

Bump the applicable legacy and canonical cache versions after the output contract changes.
Rebuild from source when old reference groups or occurrence identity are ambiguous.
Confirm the same behavior in the Modal job payload and the local path.
Measure the full 897-record run locally without production writes.

Update the affected architecture pages in the same change as the code.
Apply `docs-on-code-change` and `docs-architecture-sync` before each implementation slice is complete.
Correct the audit-risk and test-limit pages after the new full-path tests establish the result.
Port shared converter fixes upstream before a desktop sync can overwrite them.
Update the upstream design documents when their shared mapping or interface changes.

Exit criterion: applicable checks pass; documentation, cache versions, and upstream compatibility match the implemented behavior.

## Step 8 Prepare separate live correction batches

First prepare an exact diff for the three sample manuscripts and the historical work repair.
Prepare later batches by proven defect, source scope, and authorization type.
Keep pure reference repairs separate from work identity changes and newly resolved local targets.
Recheck historical upload failures before a retry; the older job receipt does not establish their present state.

Run the pre-deploy documentation gate before a deploy or GitHub action.
Obtain the required permission for each GitHub action.
Obtain explicit authorization for the concrete live correction scope before any Wikidata write.
This plan does not authorize those actions.

After each authorized batch, fetch the affected live entities again.
Verify the exact intended claims, qualifiers, references, and unchanged manuscript QIDs.
Stop on a stale revision, identity conflict, missing authorization, or uncertain write outcome.
Produce checked-QID and corrected-QID receipts with unresolved cases and reasons.

Live completion requires zero remaining confirmed defects in the authorized repair scope.
Source uncertainty can remain only as an explicit, reviewed case with its evidence preserved.

## Proposed implementation slices

| Order | Conventional commit subject | Scope |
|---|---|---|
| 1 | `test: reproduce Gila source and payload defects` | Independent fixtures and full-path failures. |
| 2 | `fix: preserve source identity across work merges` | Author trust, canonical aliases, reconciliation, and local targets. |
| 3 | `fix: preserve manuscript components and folio scope` | Occurrence identity, component roles, and deferred-link outcomes. |
| 4 | `fix: preserve reference blocks and explicit repair intent` | Proven remaining reference defects and guarded repair proposals. |
| 5 | `fix: preserve script and catalogue source detail` | Script wording, citations, places, dates, and source qualification. |
| 6 | `feat: report complete Gila source and live differences` | Full per-fact audit, review classes, and correction receipts. |
| 7 | `fix: align Gila projection caches and runtime paths` | Integration, cache changes, documentation, and shared-code parity. |

Keep each slice narrow enough to review and test independently.
Omit a repair from a slice when the regression proves that current code already handles it correctly.
Attach the applicable documentation changes to each slice.

## Acceptance against the original letter

| Gila point | Code acceptance | Separate review or live evidence |
|---|---|---|
| 1 | Preserve current 942 identity, descriptions, and former/microfilm distinctions. | Resolve ambiguous current institutions; verify unchanged QIDs after updates. |
| 2 | Preserve physical evidence and the accepted separate manuscript/work model. | Resolve uncertain physical forms without image-only inference. |
| 3 | Prevent mixed works and preserve valid shared work identity. | Repair the historical Derushim source partition. |
| 4 | Preserve all qualified Ktiv memberships and exact source IDs. | Verify the memberships after each affected update. |
| 5 | Preserve correct roles, names, authority evidence, and uncertainty. | Resolve identities only from adequate authority evidence. |
| 6 | Preserve every accepted content occurrence and source folio range. | Review unresolved literary identities and possible chronicle aliases. |
| 7 | Preserve dates, place roles, script wording, languages, and physical units. | Review contradictory dates, language scope, and uncertain physical detail. |
| 8 | Preserve claim-specific references, valid citations, scans, and distinct inscriptions. | Apply approved reference and colophon repairs; validate bibliography identities. |
| 9 | Export all expected facts, outcomes, QIDs, and review cases. | Confirm corrections from actual write receipts and fresh live evidence. |
