# Code changes for Gila's requirements, 13 November 2026

The target is complete conversion with evidence for every public claim.
The code must preserve unknown facts and expose unresolved decisions.
This document recommends changes. It does not record a product release or a public edit.

The [current audit](../audits/gila-open-requests-2026-11-13.md) compares 897 saved source records with 2,167 fresh public responses.
The code assessment uses the current local files, including the existing uncommitted changes.
The source export dates from 12 November. The public read dates from 13 November.

## First: correct field associations

### Reproduced holding-location error

`marc_ingest._extract_942_holdings` can select the current library and shelfmark, then borrow another occurrence's city and country.
The function falls back to the first city or country when the selected index has no value.
This creates an association that the source columns do not prove.

The synthetic fixture has two libraries, two shelfmarks, and only one city and country.
The 710 roles identify the second library as current and the first as former.
The actual result is:

```json
{
  "holding_942_name": "Current Library",
  "holding_942_shelfmark": "Current 2",
  "holding_942_city": "Former City",
  "holding_942_country": "Former Country"
}
```

The expected result retains the current library and shelfmark, with an unresolved city and country.
The reproduction fails on that expectation. It performs no network request or production write.

Run it from the repository root:

```bash
backend/.venv/bin/python state/gila-code-plan-2026-11-13/holding_occurrence_repro.py
```

The result is also saved in `state/gila-code-plan-2026-11-13/repro-result.json`.
The fixture proves a code defect. It does not establish a count of affected production records.
The earlier 854 label passes compare the parser's interpretation with public values.
They do not independently establish every original 942 association.

### Required implementation

- Preserve each original MARC field occurrence, its indicators, and its subfields at import.
- Retain field and subfield order where the original source provides it.
- Reuse the existing `MarcRecord` and `MarcField` structure for repeated fields.
- Keep flattened columns as a compatibility view.
- Build each holding from one proven 942 occurrence.
- Associate 710 names and roles only when the source proves their pairing.
- Retain incomplete flattened associations as review cases.
- Recover original MARC evidence for old records when the source permits it.

`_fill_from_desktop_field_handlers` currently constructs one `MarcField` per tag from flattened columns.
That bridge cannot recover field boundaries that the source export already lost.
The person-date guard prevents unsafe assignments, but it cannot reconstruct the 187 ambiguous groups across 185 records.

Code: `backend/app/pipeline/marc_ingest.py` and `backend/converter/parser/marc_reader.py`.
Tests must cover sparse 942 fields, empty slots, former/current owners, and repeated 100/700/710/880 occurrences.
The corpus comparison must report every changed association before any public update.
Derive the expected holding tuples from the original field occurrences for that comparison.
The [Library of Congress MARCXML documentation](https://www.loc.gov/standards/marcxml/) defines the structured source format.

## Existing local fixes: verify and release them

These changes already exist in the local code. They should not become duplicate implementations.

| Existing change | Current code | Required next check |
|---|---|---|
| Sparse person-date guard | `marc_ingest.py` | Recover source associations where possible. Keep unsupported groups unresolved. |
| Exact Troki fallback | `person_projection.py` and its authority path | Confirm the source name, dates, and authority ID in the final canonical P50 proposal. |
| Repeated-reference selection | `reference_retrieval.py`, `uploader.py`, and `wikidata_gila_repairs.py` | Verify an idempotent update through the actual publication path. |
| Concise person descriptions | `hmo_canonical_wikidata.py` | Rebuild with `hmo-wikidata-v26` and verify the final payload. |
| Creator-only writes | The uploader, Publication gateway, upload preparation, and UI | Verify every existing-item operation under the creator restriction. |

The earlier test pass retained one known P50 reference-block regression.
Resolve `test_merge_prefers_safe_p50_over_legacy_p2093` before the release gate closes.
The author upgrade must retain both distinct source blocks and the source qualifiers.
Full frontend verification also needs the frontend dependencies.

Confirm the deployed code version and rebuild through the normal workflow.
A local code change or stale cached proposal does not establish a public correction.

## Add source-specific resolution workflows

The existing person identity-choice endpoint provides a useful pattern.
It binds the choice to a source fingerprint, exact records, identity evidence, and a public target.
Extend that pattern to work occurrences, holdings, and catalogue references.

### Work occurrences

The current 107 work-link flags require a proven identity or scope decision.
The code should show the source passage, title, responsibility, folio, current target, and candidate targets together.
A curator should resolve one occurrence, with a recorded reason and evidence.
The choice must survive a rebuild and become stale when its source changes.
An occurrence may remain unresolved without a new public work item.
The project graph can retain the occurrence and its evidence in that state.

Use the existing item override and project-event mechanisms.
Add a dedicated validated choice for work occurrences where the generic statement editor cannot retain this proof.
Bind the choice to the record, field occurrence, folio, source hash, and exact work target.
Keep author checks and uncertainty marks in the validation.
Retain foreign work QIDs as relation targets without a write to those work items.

Code: `content_projection.py`, `work_projection.py`, `hmo_canonical_wikidata.py`, `item_override.py`, and the Wikidata Studio router.
Use `wikidata_person_identity_choice.py` as a reference for the proof contract.
Tests must distinguish homonymous works, repeated folios, source changes, and an explicitly rejected match.
Include Hizzuk's six unresolved occurrences and Sukkah's Etz Chayim selection as fixtures.

### Holdings and people

Show all 942 candidates and their source associations for the 36 holding-choice cases.
Retain the seven absent shelfmarks as source gaps until new evidence supplies them.
Reuse the current person-choice workflow for exact authority identities.
Verify the Troki P50 proposal through both the legacy and canonical paths.
Keep an uncertain name such as `שמואל(?)` uncertain after any authority lookup.

## Structure bibliography and expose catalogue notes

`_extract_581_catalog_references` currently retains citation text.
`catalog_references.py` accepts a public catalogue QID only when an explicit `catalog_qid` exists.
This preserves evidence, but the Weisz 1906 case still lacks a resolved public bibliography representation.

Add separate fields for the original citation, author, title, year, page, identifier, source URL, and review state.
Each parsed detail needs a link to its source passage.
Resolve existing catalogue identities through verified identifiers and compatible metadata.
Map only supported values to the public payload.
Retain the complete citation when no safe public mapping exists.
Never treat a bibliographic sentence as a catalogue number or a manuscript inscription.

Expose `record_status_notes` beside the source and in the report.
A note such as `רשומה זמנית` describes the catalogue record.
It must not make every manuscript date approximate.

Code: `marc_ingest.py`, `catalog_references.py`, `manuscript_projection.py`, the canonical adapter, and the item detail/export surfaces.
Tests must cover the Weisz citation, explicit catalogue codes, source pages, and catalogue-status text.

## Make source coverage visible for all nine requests

The existing 24 sample checks cover important examples. They do not establish complete extraction from every note.
The current completion packet marks point 9 as verified when all mapped manuscripts have a public read.
Its point 2 checks the accepted project model, rather than the literal request for both classes on one item.

Add explicit requirement coverage beside the existing fact comparison.
Each relevant source passage needs a conversion result or a documented unresolved decision.
Expose unconsumed passages from fields such as 500, 505, 581, 590, 740, and 942.
Keep record-level, work-level, person-level, and material-unit scopes separate.
Retain date precision, uncertainty, script wording, and leaf/page units in those results.

Show separate counts for public reads, verified claims, source gaps, identity reviews, pending repairs, and unchecked passages.
Retain the explicit distinction between the accepted manuscript/work model and Gila's wording.
Do not convert a documented source gap into a fictitious public fact.
Only an exact current public read and the required evidence can close a correction.

Code: `wikidata_gila_facts.py`, `wikidata_gila_audit.py`, `wikidata_gila_completion.py`, `export_gila_completion.py`, and `frontend/src/routes/WikidataStudio.tsx`.
Reuse the existing background jobs and review table for the curator surface.
Tests must detect an omitted source passage, stale review, wrong scope, missing reference, and an unresolved requirement.
Cache fingerprints must include every new field that affects the projection or verdict.
Use original MARC occurrences and independently specified fixtures as the audit's expected values.

## Finish the guarded repair workflow

The latest owned queue contains 44,709 repeated reference blocks across 1,600 items.
The reference-selection code already exists. The operational gap is a durable, verified execution under server lag.

Reuse Publication intents, checkpoints, and uncertain-outcome recovery for the historical repair path.
Carry the server's retry delay as structured data through `WriteOutcome` and the execution state.
Use the existing `next_attempt_at` field for eligible retries.
The gateway currently retains the delay in an exception, then reduces it to a detail string.
The historical repair transport currently hardcodes `maxlag=20`.
Use one explicit bulk policy with `maxlag=5` and respect `Retry-After`.
The [MediaWiki guidance](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter) documents that policy.

A confirmed pre-edit rejection may enter a delayed retry.
An uncertain result needs exact public recovery before another write.
Each successful repair needs a full read-back and a current-revision receipt.
The second run must produce no repeated-reference addition.

Preserve the creator restriction throughout this workflow.
Strengthen creator proof with the immutable user ID and the first revision, alongside the canonical name.
Bind the current cleanup scope to user ID `6935067`.
Keep authentication proof, the planned target, and the actual write target consistent.
The existing generic creator helper currently returns a cached username rather than a user-ID proof.
All 17 foreign targets remain excluded from writes and merges.

Code: `wikidata_gila_execution.py`, `publication/gateway.py`, `publication/wikidata_gateway.py`, `publication/core.py`, and `uploader.py`.
Tests must cover maxlag, Retry-After, interrupted jobs, changed revisions, hidden creators, user-ID mismatches, and uncertain outcomes.

## Suggested implementation order

1. Fix the 942 association defect and preserve source occurrences.
2. Close the regression and deployment checks for the existing local fixes.
3. Add validated source choices for work occurrences and holdings.
4. Add structured bibliography and visible catalogue-status evidence.
5. Add explicit coverage and review states for all nine requirements.
6. Run the remaining repairs through the durable creator-guarded execution path.

Measure progress as verified supported facts and resolved evidence cases.
Missing source information still needs catalogue or scan evidence.
The code cannot establish the identity of an ambiguous work or invent an absent shelfmark.

Trellis capture `19e848fd-2f72-4507-b1fa-dad0996c9ace` proposes an improvement to
`doctorat/flattened-marc-dates-require-intact-occurrence-slots`.
Review remains pending.
