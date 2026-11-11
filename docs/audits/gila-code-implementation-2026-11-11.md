# Gila code implementation receipt

Date: 11 November 2026.
Branch: `codex/gila-code-repair`.
Baseline: `29b2cf632ec2aae02682bb00d697b055d1a19a20`.

The local implementation preserves source identity, occurrence scope, wording, and references.
It produces explicit review cases and guarded historical proposals.
The task performs no production database, cache, or Wikidata writes.

## Implemented behavior

- Work matches require compatible public QIDs, source authors, and source scope.
- Shared public works retain distinct source references and one deterministic local alias.
- Repeated folios and different authors retain separate occurrences.
- The safe NER author fallback retains an exact source work QID.
- MARC 500/505 retains `source_title` separately from the normalized title.
- P1932 preserves meaningful quotation marks and terminal Hebrew geresh.
- Named commentaries and supported Latin main works survive the title gates.
- The citation abbreviation check no longer rejects `אלפסי זוטא`.
- Explicit place lists, testimony, drawing subjects, and entry endpoints remain source evidence.
- Deferred unknown-text occurrences retain a reason after final native cleanup.
- Script claims retain the complete 958 term in P1932.
- Bibliographic prose remains source evidence; explicit catalogue codes can supply P528.
- Contained letters retain their genre and role scope.
- Generic related places do not establish manuscript attestation.
- Explicit extraction rejection survives the API, events, fingerprints, import, export, and history.
- Auto-approve excludes rejected entities, including a concurrent rejection.

The cache versions are `source-aware-works-v21` and `hmo-wikidata-v22`.
Migration `0053_extraction_rejection` adds the durable rejection field and exclusive-state constraint.
Old unapproved rows remain pending.

## Corpus evidence

The measurement reads the complete 897-record run and durable canonical HMO snapshots.
It disables reconciliation and production cache writes.
The final build contains 2,421 proposed entities and zero validation-error items.

| Check | Result |
|---|---:|
| Expected source facts | 10,518 |
| Unexplained source-fact losses | 0 |
| Facts with explicit review required | 3,637 |
| Current manuscript QIDs in the ledger | 896 |
| Manuscripts with a date claim | 897 |
| Hebrew source date texts | 779 |
| Qualified Ktiv memberships | 897 |
| Named unknown scribe claims | 413 on 400 manuscripts |

Source review cases include nine century passages with a named year or boundary.
The report does not select an exact year from those passages.
An unresolved work target remains an identity case, including when the live link is absent.
An unresolved manuscript QID remains unchecked; it does not become a false live defect.
The missing ledger identity still requires separate review.

The full live snapshot retains its initial audit date.
A fresh API read covers 81 entities for historical repair review at `2026-11-11T15:14:23Z`.
The report records both snapshot sources.

The fresh subset supports 52 reference repair proposals with 811 redundant blocks.
It also supports 13 colophon review proposals and 11 work partition reviews.
These proposals preserve exact claim IDs, revisions, digests, before-states, and after-states.
They remain non-executable and require a fresh read before any authorized write.
The mixed Derushim work remains a source-partition review.
The corrected-QID receipt remains empty.

## Validation

- The final focused backend unit suite passed 2,240 tests.
- The source, parser, RDF, fact, and repair checks passed 96 tests.
- The integration and extraction cache suites passed 92 tests.
- Two frontend request and poll tests passed with cached local dependencies.
- The migration has one Alembic head and produces valid offline PostgreSQL DDL.
- Ruff reports zero new findings against the baseline.
- `git diff --check` passes.

The exact frontend dependency build remains unverified.
The checkout has no frontend dependencies, and registry access fails in this environment.
The fallback used cached Vitest 4.1.7 rather than the manifest's Vitest 2 range.
It verifies the two behavior tests, not the full application build.

Several old tests failed on the unchanged baseline.
The implementation updates their fixtures for current source semantics, the judge model, and the paginated reader.
It also removes mocks for a deleted helper and isolates external reads in those unit tests.

## Standards

The review found faults in rejection transport, import persistence, fingerprints, colophon scope, and NER target reuse.
The code now has a regression test for each fault.
The final colophon guard checks value snaks, folios, language, datatype, rank, and retained source references.

## Spec

The review traced all 100 initial content gaps.
It separated audit-format differences, confirmed code defects, policy exclusions, and unresolved identities.
The final report has zero unexplained source-fact losses.
Source uncertainty and historical live defects remain separate from implementation completion.

## Remaining operations

The production migration, deployment, and live corrections remain unapplied.
The plan reserves those actions for separate authorization.
The exact frontend build must pass before deployment readiness.
The desktop checkout contains separate uncommitted edits and lacks the corresponding split projection modules.
The documented sync gate requires an explicit desktop port before a future shared-source sync.

Architecture documentation covers Wikidata Studio, extraction, RDF, frontend, export/history, and deployment.
Rule W-305 records the source and repair contracts.

## Local artifacts

- [Detailed fact report](/Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/state/gila-live-audit-2026-11-11/implemented/wikidata-47e03437-09f2-4d6b-aea7-89fe79d76198/gila_source_facts.json)
- [Repair receipt](/Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/state/gila-live-audit-2026-11-11/review-batches/receipt.json)
- [Reference repair batch](/Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/state/gila-live-audit-2026-11-11/review-batches/reference-repairs.json)
- [Sukkah colophon proposal](/Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/state/gila-live-audit-2026-11-11/review-batches/sukkah-colophon.json)
- [Mixed Derushim review](/Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/state/gila-live-audit-2026-11-11/review-batches/mixed-derushim-review.json)

Trellis capture: `f06b7554-8923-4f03-a59a-d31c5cbb9736`.
Proposal kind: `ambiguous`. Target: none. Review: pending.
