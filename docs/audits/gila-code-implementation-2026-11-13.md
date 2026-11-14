# Gila code implementation receipt, 13 November 2026

The local code implements the six workstreams in the [accepted plan](../plans/gila-code-improvements-2026-11-13.md).
This receipt does not establish deployment or a new public correction.
The earlier uncommitted changes remain in the workspace.
This implementation made no public edit, production migration, deployment, commit, or push.

## Delivered behavior

| Workstream | Result |
|---|---|
| Original MARC evidence | Binary MARC, MARCXML, and structured JSON retain ordered occurrences, subfields, indicators, and source keys. |
| Safe source associations | Sparse flat dates, roles, locations, and shelfmarks remain review cases. Rebuilds remove stale derived values. |
| Source choices | Work, library, and publication choices bind exact source proof, current target evidence, and a curator reason. |
| Bibliography and notes | Citations retain source text and structured metadata. Reviewed publications use P1343, with P304 for proven pages. |
| Nine requirements | The drawer and completion packet separate supported facts, source gaps, identity reviews, pending repairs, and unchecked passages. |
| Durable repairs | Publication retains exact snapshots, intents, server delays, checkpoints, and receipts. Uncertain outcomes require exact recovery. |

The creator guard requires fresh authenticated and first-revision user IDs.
The Gila repair queue additionally requires `Alexander Goldberg IL`, user ID `6935067`.
A foreign item may serve as a verified relation target; the code cannot edit or merge into it.
A source choice never grants publication approval.
The P50 regression now retains both distinct source blocks and the original scope qualifiers.
Current build versions are `source-aware-works-v23` and `hmo-wikidata-v27`.

## Validation

| Check | Result | Evidence |
|---|---|---|
| Broad backend suite | 2,880 passed; 2 skipped; 8 warnings | `/tmp/gila-delivery-backend.log` |
| Final targeted suite | 102 passed after the last audit safety change | `/tmp/gila-delivery-targeted.log` |
| Focused TypeScript check | Passed with actual React and TypeScript types | `/tmp/gila-delivery-types.log` |
| Ruff | Passed on 13 new Python files | `/tmp/gila-delivery-lint.log` |
| Whitespace | `git diff --check` passed | `/tmp/gila-delivery-whitespace.log` |

The broad suite includes Gila's nine examples, author promotion, creator guards, source proof, imports, history, and delayed repair recovery.
The final suite checks the source review, facts, completion packet, and full-path Gila regressions.
It includes the final rule that an uncertain 942 association cannot authorize a public label repair.
The focused TypeScript check covers the new API and component with the real API client and Vite declarations.
It does not replace the full frontend checks.

The frontend dependency install failed with `AggregateError [EBADF]`.
The full build, Vitest, and Playwright did not run.
The new frontend specs remain available for the release checks.
No package manifest or lockfile changed.

## Source comparison and remaining evidence

The read-only comparison examined 897 saved source records.
All 897 lacked the original field occurrences, and none had a saved projection baseline.
The parser derived 659 tuples; this count does not establish 659 production defects or corrections.
The report records zero public writes and `production_effect_count: not_established`.
Evidence: `state/gila-implementation-2026-11-13/marc-association-comparison.json`.

Old flat records still need original MARC evidence where associations remain uncertain.
The existing record editor can accept complete original `fields` or canonical `marc_occurrences` with the same control number.
Curators must resolve work identities, library choices, absent shelfmarks, and incomplete citations from source evidence.
The audit leaves documented unresolved passages unresolved.
The code cannot establish a missing source fact.

## Release boundary

The release still requires migration `0055_source_review_choices`, the reviewed backend and frontend, and the same code on each executor.
The release must complete the full frontend checks and a normal rebuild before publication review.
The historical repair queue needs a fresh preflight and current public proof through Publication.
This implementation did not execute that queue.
The earlier application-origin refusal remains in force; no alternate session or secret path bypassed it.

## Documentation and durable knowledge

The extraction, Wikidata Studio, frontend, versioning/export, and job-service docs describe the new behavior.
Rules W-326–W-329 cover source associations, source proof, durable repairs, and P50 source preservation.
The [implementation file index](../architecture/blocks/wikidata-studio/gila-key-files.md) maps the source files to those rules.
The documentation gate checked routes, model fields, fingerprints, exports, and shared retry behavior.
The code adds no job kind or second retry queue.

Trellis capture `2c97b1dc-7479-46a2-9b87-42129f664685` is an improvement proposal.
Its target is `doctorat/capture-b723b95d-5c9e-4d20-bec3-97f3b4bac77c`.
Review remains pending; the capture did not replace stable knowledge.

The durable local evidence is `state/gila-implementation-2026-11-13/validation-receipt.json`.

The [source-review follow-up](gila-source-review-followup-2026-11-13.md) records supplemental component checks and a later UI scope fix.
