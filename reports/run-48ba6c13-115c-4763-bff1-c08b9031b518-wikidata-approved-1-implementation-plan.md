# Wikidata verification remediation implementation plan

Source analysis: [issue classification report](</Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/reports/run-48ba6c13-115c-4763-bff1-c08b9031b518-wikidata-approved-1-issue-classification.md>)  
Baseline: **71 non-passing entities — 70 partial, 1 fail**.

## Guiding rule

Implement in ROI order, not in file order:

1. Remove deterministic false claims first.
2. Then repair identity and role resolution.
3. Then make HMO Wikibase the reliable staging layer.
4. Only after pass/fail quality is stable, spend effort on deep enrichment and folio-level completeness.

A claim that is unsupported should be omitted from the public Wikidata projection but retained as evidence in HMO staging and the curator report. Do not weaken the evaluator to make an unsupported item pass.

## Phase 1 — easiest wins with the widest effect

**Goal:** eliminate deterministic noise and semantic overreach with small, local changes.

| Work item | Current evidence | Difficulty | Expected effect | Primary code area |
|---|---:|---|---|---|
| Normalize doubled/escaped ISBD quotes | 6 work labels | Low | High; directly addresses all work `name_ok=partial` rows | `converter/wikidata/work_candidates.py`, `item_builder.py`, `rdf_helpers.py` |
| Suppress catalog/workflow text from P1684 | 10 entities | Low–Medium | High; removes statements the judge treats as fake inscriptions | `manuscript_metadata.py`, `ner_post_filters.py` |
| Add fail-closed guards for unsupported P136 | 18 entities | Low–Medium | Very high; prevents illustrated→illuminated, autograph-response→autograph, license→semikhah errors | `content_projection.py`, genre mappings |
| Add fail-closed guards for secondary P921 | 24 entities | Low–Medium | Very high; stops “additional subject” and generic topics becoming main subjects | `content_projection.py`, `marc_subject_resolve.py` |
| Correct P127 role mapping | 21 entities | Low–Medium | Very high; stops former owner/censor/seller claims being emitted as current ownership | `person_linking.py`, `manuscript_metadata.py` |
| Add current-institution extraction from MARC 710 | 14 entities | Low–Medium | Medium–High; removes wrong/default NLI collection claims | `manuscript_metadata.py` |
| Add regression fixtures for Hebrew abbreviation marks | 6 known labels | Low | High; prevents quote cleanup from damaging רס"ג, רש"י, האר"י, רמב"ם, חז"ל, תשב"ץ | validator + work candidate tests |

### Phase 1 implementation steps

1. Introduce pure normalization helpers with table-driven tests.
2. Add source-field/role predicates before each P136, P921, P127, and P1684 append.
3. Preserve rejected candidates in `work_candidate_evidence`/MARC evidence instead of silently deleting them.
4. Add one regression fixture per observed failure family.
5. Rebuild the run locally and run the measurement-only verifier with a fresh cache.

### Phase 1 exit gate

- No `LABEL_QUOTE_NOISE` for the six known works.
- No unsupported P136/P921/P127/P1684 claim in the rebuilt projection.
- P195 reflects the record’s current institution when present.
- No regression in passing/full rows.
- Fresh AI verification shows a material reduction in partials before Phase 2 begins.

## Phase 2 — medium effort, still high-impact

**Goal:** correct deterministic metadata that can invalidate otherwise good items.

| Work item | Current evidence | Difficulty | Expected effect |
|---|---:|---|---|
| Detect facsimile/photographic print records | 1 fail (`990019020880205171`) | Medium | Very high; fixes the only `type_ok=no` and prevents future type failures |
| Separate copy/publication date from catalog date | 12 date concerns | Medium | High; removes wrong years and malformed dates |
| Normalize BCE Wikibase time syntax and precision | 1 explicit malformed BCE date | Low–Medium | High for date-bearing items |
| Replace HMO self/mirror links with correct source-link semantics | 6 entities | Medium | High; prevents P2888/P973 from being treated as authoritative evidence |
| Restore missing author/work chain | 2 entities, plus future MARC-100 cases | Medium | Medium–High; preserves the P1574→work→P50 constraint-safe model |
| Add resolved-label checks for item-valued statements | Multiple P11603/P195 rows | Medium | High; makes unresolved values fail before AI verification |
| Make descriptions derive from verified facts only | Several wrong/default descriptions | Medium | Medium; removes description contradictions without inventing content |

### Phase 2 exit gate

- No type failure for print/facsimile records.
- Every emitted date has a source field, valid Wikibase syntax, and explicit precision.
- Every P2888/P973 URL has a defined semantic role.
- Every known author is represented through a work item, never a direct manuscript P50.
- Export quality checks fail locally before a bad item reaches the UI.

## Phase 3 — hardest high-impact work

**Goal:** make identity and role claims trustworthy instead of merely syntactically valid.

| Work item | Current evidence | Difficulty | Expected effect |
|---|---:|---|---|
| Authority-target acceptance gate | 23 explicit wrong/ambiguous authority concerns | High | Very high; wrong people are more damaging than missing people |
| Scribe/transcriber reconciliation | 28 P11603 concerns | Medium–High | Very high; fixes wrong local IDs, missing labels, and role/name mismatches |
| Shared resolver for owner/scribe/commentator | P127/P11603/P9046 conflicts | High | Very high; one identity policy across all roles |
| Chronology-aware matching | Names attached to implausible manuscript dates | High | High; catches modern/ancient identity collisions |
| Two-pass local-reference resolution | `__LOCAL` targets not always final-QID safe | High | High; prevents unresolved or wrong targets from public export |
| Curator review queue for unresolved authority matches | Source authority records can be contradictory | Medium–High | High quality, but some records require human decisions |

### Required authority invariant

An item-valued person/work claim is publishable only when:

- the normalized MARC name matches an authority preferred name or an accepted alias;
- the role matches (`scribe`, `former owner`, `censor`, `commentator`, etc.);
- chronology is plausible for the record;
- the target has a stable local ID and, for public Wikidata, a verified QID;
- the catalog spelling remains available as `P1932`/evidence without disguising a target mismatch.

If any check fails, retain the source text and evidence in HMO staging and omit the public item-valued claim.

### Phase 3 exit gate

- No P11603/P127/P9046 target has a name/role mismatch.
- No unresolved `__LOCAL` target is exported as if it were a final Wikidata entity.
- Every authority rejection is visible in the curator report with a reason.
- AI verification is rerun after cache invalidation and rebuilt items, not against stale JSON.

## Phase 4 — Wikibase-first staging and publish projection

**Goal:** prevent the same problems from recurring across runs and make the data model explicit.

### Design

1. Store rich candidate entities and evidence in HMO Wikibase.
2. Normalize person/work/manuscript identities there before public export.
3. Mark each statement as `supported`, `presumed`, `rejected`, or `needs_review`.
4. Build Wikidata items from an explicit publish projection, not from the raw HMO statement dump.
5. Use HMO links as staging/source links unless exact identity is proven; reserve P2888 for true exact matches.
6. Keep stable crosswalks from local IDs to existing Wikidata QIDs and never silently remap a local target.

### Difficulty and effect

- **Difficulty:** High.
- **Expected effect:** High long-term; it reduces recurring identity drift and makes rebuilds reproducible.
- **Immediate pass-rate effect:** Medium. It is infrastructure that enables the Phase 3 fixes rather than a single quick verdict flip.

### Phase 4 exit gate

- Rebuilding the same run produces stable local IDs and deterministic statements.
- The public projection can be regenerated without copying rejected/uncertain HMO statements.
- Existing QID reconciliation is repeatable and auditable.
- Every public claim has source evidence and a projection decision.

## Phase N — hardest work with the smallest immediate pass-rate effect

**Goal:** improve richness and scholarly completeness after the blocking quality issues are gone.

These items are valuable, but they should not delay the earlier phases because they affect relatively few rows or improve richness more than pass/fail status.

| Work item | Current evidence | Difficulty | Immediate effect |
|---|---:|---|---|
| Validate and repair overlapping P1574/P958 folio ranges | 4 entities | High | Low–Medium pass-rate effect; high scholarly value |
| Reconstruct complex 505 anthology boundaries | 4 entities, especially `990001801390205171` | High | Low immediate effect; improves structural richness |
| Add every non-blocking contributor/author/institution relation | A handful of omissions | Medium–High | Low–Medium; improves completeness after core claims pass |
| Improve English labels/transliterations beyond authority minimum | 6 person labels | Medium | Low once authority identity is correct |
| Add richer subject coverage without over-promoting to P921 | Many records | High | Low immediate effect; improves discovery only after primary subjects are safe |
| Human adjudication of corrupted historical authority records | Several contradictory records | High | Low predictable effect; necessary for scholarly correctness, not automation |
| Expand validation to rare properties and qualifier semantics | Sparse cases such as P9046/P1684 roles | Medium–High | Low immediate effect; prevents edge-case regressions |

### Phase N exit gate

- All core claims are already passing or explicitly held for curator review.
- Richness changes never reintroduce unsupported statements.
- Folio and contributor additions are backed by source snippets and regression fixtures.

## Verification and release loop for every phase

1. Rebuild the run with current code; do not edit the exported JSON manually.
2. Run deterministic export-quality checks.
3. Run the measurement-only verifier on the previous non-passing scope with cache override.
4. Compare verdict counts and inspect every new partial/fail.
5. Update the issue classification report with newly discovered patterns.
6. Only after the local baseline improves, use the UI to rebuild and verify again.
7. Deploy/push only after code and architecture docs are synchronized and the phase exit gate is satisfied.

## Recommended first implementation slice

Start with Phase 1 in this order:

1. Quote normalization tests and fix.
2. P1684 catalog-note filter.
3. P921 primary-subject gate.
4. P136 exact-genre gate.
5. P127 role semantics.
6. P195 current-institution extraction.
7. Fresh local measurement run.

This sequence is intentionally conservative: it should remove false claims before adding more fields, preserving the “rich but evidence-backed” goal.
