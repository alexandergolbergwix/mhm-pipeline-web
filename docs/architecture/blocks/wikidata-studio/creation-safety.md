# Public CREATE guards

> Up: [Wikidata Studio](README.md) · W-335–W-337, W-382–W-384 · R185–R187, R239–R241

## Final boundary

`WikidataUploader.upload_item` checks each new live item after local references resolve and before login.
`wikidata_creation_safety.creation_errors` reuses every hard error from the shared export-quality gate.
It also requires a supported entity type, active P31, and an English description.
A new manuscript needs an exact P3959 and a proven current P217.
Several active values on one strong identifier property require identity review.
`validate_item` rejects generated person-role and manuscript lists as public descriptions.
The canonical converter uses the same predicate before it selects a public description.
This predicate covers named role lists with a truncated final fragment.
The normal concise person description remains valid.

Immediately before the sole write, a new reconciler checks duplicate absence.
Publication uses the same `reconcile_creation_identity` helper for its fresh check.
The lookup excludes deprecated identifiers without a change to the original payload.
A found target blocks CREATE and identifies the QID for review.
A failed lookup or inconclusive work identity also blocks CREATE.
The code does not convert this result to an UPDATE.

`reconcile_person_by_identifiers` returns absence only when each identifier lookup returns absence.
Distinct candidate QIDs or a conflicting authority value raise `PersonIdentityUncertainError`.
The exception requires review and cannot authorize a new item or adoption.
A safe single identity still returns its QID.
When the authority checks return no match, search the proposed labels and aliases in their languages.
The search checks labels and aliases on human items and items without an instance-of claim.
A match requires identity review. A failed or invalid search blocks CREATE.
The reviewed scribe registry also supports a canonical catalogue URL in `P973`.
It requires the exact Hebrew label, manuscript controls, human class, and scribe role.
The final check searches the URL variants in `P973`, `P2888`, and references on human items.
A matching URL or an unavailable query blocks CREATE.
See the [reviewed catalogue evidence](../../../audits/2026-11-23-yemenite-scribe-authority.md).


Existing partial updates retain their separate creator and claim guards.
Public updates still require fresh proof for creator user ID `6935067`.
Test-wiki stubs retain the test-specific namespace and transport guards.
Maxlag and unknown-outcome guards remain mandatory.
A P528 value that consists of a public QID raises `CATALOG_CODE_IS_QID`.
This guard catches old positional work edits that now target catalogue codes.
The [six source-audit cases](creation-review-cases.md) retain their source statements and require review of their proposed work targets.
A duplicate check uses the current service response; index delay remains a limit.

## Current shelfmark

`current_shelfmark_requires_review` detects unresolved current 942 evidence.
Field 090 also cannot supply P217 when field 942 is absent.
The legacy projection and metadata modules withhold P217 and the field 090 value in labels.
The canonical projection also removes a stale HMO P217 and withholds that designation component.
Both paths retain raw MARC and source review evidence.
A scoped, reviewed source choice can supply the proven current value.
The final CREATE check blocks a new manuscript until it has a current P217.
Current versions: `source-aware-works-v26` and `hmo-wikidata-v29`.
Deploy the same commit on Heroku and Modal before a new build.

## Nine-point prevention matrix

These protections address the code. A test pass does not prove a fact absent from the source.
Uncertain source data remains an explicit review case.

| Gila point | Code rule | Regression evidence |
|---|---|---|
| 1. Current 942 designation and shelfmark | Preserve original tuples; withhold unresolved current values; distinguish microfilm | `test_gila_prebor_9_points_audit.py`; new legacy and canonical shelfmark cases |
| 2. Physical form and work distinction | Separate manuscript/work items; no image-only codex inference | Nine-point audit, point 2 and scan case |
| 3. Work identity | Author/source identity; no title-only adoption; retain distinct occurrences | `test_gila_full_path_regressions.py`; `test_wikidata_upload_guards.py` |
| 4. Ktiv and source identity | Qualified P361; exact P3959 and source citations | Nine-point audit, point 4; CREATE source-ID check |
| 5. People and roles | Authority evidence; preserve uncertainty and roles; block person identifier conflicts | Nine-point audit, point 5; CREATE identity cases |
| 6. Contents and folios | Source-aware 500/505/740; author and folio occurrence identity | Full-path cases; final P1574 test in `test_wikidata_studio_works.py` |
| 7. Dates, places, scripts, extent | No invented circa or place role; preserve Hebrew dates and script wording | Nine-point audit, point 7; full-path cases; `test_marc_extent_and_digital_access.py` |
| 8. Subjects and sources | Scope genre; retain source quotes and bibliography; coalesce identical retrieval variants | Nine-point audit, point 8; `test_wikidata_reference_retrieval.py` |
| 9. Complete evidence | Separate verified facts, omissions, absent source data, review cases, and live receipts | `test_wikidata_gila_completion.py`; `test_wikidata_gila_execution.py` |

The existing citation code already coalesces repeated retrieval dates on new claims and normal owned updates.
It preserves distinct pages, quotes, and source metadata.
This change retains that behavior.
Historical public repairs still require exact source proof and durable receipts.

The registry can approve an exact entry from the Center for Jewish Art, an encyclopedia, or a scholarly reference.
The duplicate check accepts safe URL queries and fragments. It checks at most 64 variants in one Wikidata query.

## Tests

- `backend/tests/unit/test_wikidata_creation_safety.py`: direct semantic bypass, valid CREATE, fresh duplicate and failure, exact person names, deprecated identity, person conflicts, current shelfmark, and exact scholarly entries with duplicate and service failure checks.
- `backend/tests/unit/test_wikidata_reconciler_prewarm.py`: multilingual labels and aliases, and invalid name lookup results.
- `backend/tests/unit/test_hmo_canonical_wikidata.py`: final canonical payload cannot restore a microfilm P217, label, or named role-list description.
- `backend/tests/unit/test_wikidata_upload_guards.py`: person conflicts require review; safe identity remains valid.
- `backend/tests/unit/test_wikidata_publication_gateway.py`: shared fresh Publication checks.
- `backend/tests/unit/test_wikidata_uploader_ownership_policy.py`: creator, retry, and uncertain-outcome guards with valid CREATE fixtures.
- `backend/tests/unit/test_wikidata_test_wiki_compat.py`: live payload retention with a valid CREATE fixture.
- `backend/tests/unit/test_wikidata_upload_claim_complete_w191.py`: complete claim and one-attempt error behavior with valid CREATE fixtures.

A release needs the full backend suite and a fresh source-derived measurement.
Keep that measurement read-only until the curator reviews its unresolved cases.
The [completion packet](gila-completion-packet.md) defines the corpus evidence and correction receipts.
