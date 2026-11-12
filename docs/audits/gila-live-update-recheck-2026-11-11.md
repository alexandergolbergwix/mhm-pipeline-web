# Gila: live Wikidata audit and update

Audit dates: 11–12 November 2026, Israel time.
Execution timestamps use UTC.
Run: `47e03437-09f2-4d6b-aea7-89fe79d76198`.

The operator completed the source-supported updates and deployed the code fixes.
The final comparison covers all 897 source manuscript records and 2,135 public entities.
The source now contains 897 manuscripts, 104 people, and 1,406 works.
Two false historical person matches remain outside live upload.
The complete email acceptance remains incomplete because some source identities and scopes remain unresolved.
The report does not classify those cases as successful repairs.

## Verified results

| Result | Count |
|---|---:|
| Source manuscript records checked | 897 |
| Current source items | 2,407 |
| Public entities in the final read | 2,135 |
| QIDs with a changed revision against the recorded baseline | 1,590 |
| Verified exact revision receipts | 121 |
| Distinct QIDs in those exact receipts | 112 |
| Complete author heading repairs | 24 |
| Additional public author and historical-source repairs | 19 |
| Source-proven duplicate work redirects, with Cantarini | 7 |
| Versioned source edits, with additional occurrences | 129 |
| Confirmed refusals checked after the deploy, with the related manuscript | 14 |

These counts overlap.
A changed revision can include another editor's work.
The exact receipt CSV identifies the operator's verified revisions.
A receipt can describe an intermediate correction that a later source upload restored.
The final read establishes the current public state.

The complete public read began at `2026-11-12T00:42:37.043471+00:00`.
The final public refresh finished at `2026-11-12T01:05:28.662508+00:00`.
The API requests include `maxage=0`, `smaxage=0`, and `curtimestamp=1`.
The snapshot retains complete claims, qualifiers, references, and revisions.

## The nine email points

| Point | Current result and limit |
|---|---|
| 1. Labels and shelfmarks | The audit compares current 942 shelfmarks for every resolved source fact. Existing QIDs remain the targets. Foreign additive updates preserve community metadata. The receipt table exposes remaining metadata differences. |
| 2. Physical and work classes | The accepted project model separates physical manuscripts from written works. It does not add written work P31 to every manuscript. Codex classification still requires physical-form evidence. This differs from the email's literal dual-class request. |
| 3. Manuscript to work identity | Sukkah now links to Q141754992. Cantarini's duplicate Q141175543 redirects to Q141755239, with author Q6076130. Seven proven duplicate work donors now redirect. Ambiguous titles remain separate. |
| 4. Ktiv membership | The source and public comparison check P361, the exact P3959 record ID, and source-bound references. The item and fact CSV files retain the relevant results. |
| 5. People and roles | Complete author headings, authority identifiers, and source-specific roles were repaired. Hizzuk sender and recipient roles retain folios 51א–52א. Adler's previous shelfmark remains separate. Uncertain names such as שמואל(?) remain uncertain. |
| 6. Contents and folios | Six additional source edits retain proven work occurrences. The Gabirol occurrence now targets Q18387842 and retains all three archived source blocks. Unresolved component titles and physical scope remain review cases. |
| 7. Dates, places, languages, and material | The full source comparison checks exact values and qualifiers. Source script wording uses P9302. Hizzuk has no unsupported whole-manuscript Buchach claim. Remaining fact and scope gaps appear below. |
| 8. Subjects, genres, sources, and quotations | Hizzuk has no whole-manuscript letter genre. Exact duplicate references were removed without loss of distinct blocks. The source code now prevents all 47 proven malformed colophon wrappers from return. Ambiguous quotations remain review cases. |
| 9. Complete audit trail | The CSV files list source facts, item receipts, link receipts, changed QIDs, and exact revisions. The report distinguishes source evidence, transfer gaps, and uncertain identity. It does not certify unresolved cases. |

## Source facts after the update

| Property | Present on public Wikidata | Missing exact fact | Identity or scope review |
|---|---:|---:|---:|
| P217 | 854 | 0 | 0 |
| P3959 | 897 | 0 | 0 |
| P9302 | 804 | 0 | 0 |
| P528 | 278 | 0 | 0 |
| P1574 | 603 | 106 | 76 |
| P571 | 888 | 0 | 0 |
| P11603 | 0 | 0 | 0 |
| P3342 | 0 | 0 | 0 |
| P7153 | 0 | 0 | 0 |
| P407 | 1097 | 0 | 0 |

The comparison finds 8,572 present facts, 106 missing exact facts, and 76 facts with uncertain identity or scope.
It also retains 1,764 facts as source evidence without an automatic public assertion.
All 106 missing exact facts in this final comparison are work links with unresolved identity or scope.
These counts describe facts, not distinct entities.
The source fact CSV records the source field, scope, expected value, qualifiers, and reason.
The classification `absent_in_source` describes the supplied MARC only.
It does not establish absence from every external catalogue or scan.
A missing exact qualified claim can coexist with a broader public claim.

## Upload receipts and recovery

The original full job covered all 2,409 proposals.
Modal preempted that executor and replayed its scope.
The operator stopped the replay through the normal cancel API and recovered 1,600 latest durable item receipts.
No direct database change altered a job status or a target ledger.

Twelve ordinary jobs then covered all 897 manuscripts with at most 80 items per job.
Those jobs recorded 704 update messages, 168 unchanged manuscripts, and 25 exact-QID approval refusals.
Later source-specific approvals and upload jobs superseded those initial refusals where the identity proof passed.
The initial count of 25 is not the final count of unresolved manuscripts.
The API status `adopted` can accompany an update message.
The receipt message and public read establish whether an update occurred.

The source-supported scopes used three dry-run jobs and three short live jobs.
The live scopes recorded 102 update messages across 44 non-manuscripts and 108 manuscript items.
Thirteen conclusive pre-write refusals remained: one author-name conversion and twelve P31 rank conflicts.
The code fixes addressed those causes without a target override or a community rank change.
The post-deploy jobs checked those thirteen items and the related Pancieri manuscript.
The post-deploy live job contains thirteen successful entity outcomes.
The work guard retains Q141991984 as an existing target for references.
A separate two-item job then resolves its manuscript occurrence at folios 9א–15א.
The job preserves the work guard.
The earlier link to Q142031906 concerns a separate fragment, not the main work.
That relation is absent after the normal owned-item update and requires a separate source identity decision.
The complete post-deploy receipts have no failed, blocked, or uncertain entity outcome.

- `source-build`: `edb0540b-2cac-42b9-9c84-70d9894a608e`.
- `dry-confirmed-refusals`: `edd9b35f-ba3a-46eb-87bc-f87d69169834`.
- `live-confirmed-refusals`: `8d4645d8-a5bf-4f1a-814d-fc5c65afa897`.
- `dry-pancieri-link`: `432a23a9-ed96-49f5-924b-54488abc8e61`.
- `live-pancieri-link`: `f325cd1c-9bcf-4937-8437-e5bf11294fc7`.

The latest manuscript receipt statuses are `{'adopted': 712, 'exists': 163, 'updated': 22}`.
These statuses describe the last normal job for each item.
A matching proposal alone does not prove source identity.
An additive foreign update can preserve a community label or rank that differs from the proposal.
The separate public preservation check verifies all twelve foreign manuscripts.
Every previous claim, rank, qualifier, complete source block, and metadata value survives.

## Remaining review cases

- The source review has 245 authorless rows without sufficient author proof. The audit does not assert that their authors are absent.
- Eight dependent native work rows need source identity resolution before a target correction.
- Six person rows have wrong global keys. No supported target PATCH can safely replace those keys in this task.
- The unsigned Keter Malkhut source remains unassigned. The Lonzano source remains separate from the proven Tzemah work.
- Q141990660 still combines three native work identities. The exact false author was removed, but the work partition remains unresolved.
- Q141990951 remains a separate Gabirol duplicate. The current native row cannot supply valid approval for a different recipient QID.
- The earlier manuscript relation to Q142031906, שער המצוות (קטע), needs a separate fragment identity decision. The archived before claim retains its complete source blocks.
- Q134926101 has conflicting catalogue identity evidence. Its duplicate references remain outside the approved exact repair scope.
- The source fact CSV retains all other missing values, uncertain identities, and scope decisions.

The normal guards continue to refuse these unsupported changes.
The [source review](gila-blocked-source-review-2026-11-12.md) records the exact supporting and excluded evidence.
The [person partition report](gila-historical-modern-authority-partition-2026-11-12.md) records the modern and historical identity conflicts.

## Code and deployment

Code commit: `eb76b49e`.
Heroku release: `v793`.
Modal app: `mhm-jobs`.
Both targets use the same committed backend.
The health, readiness, and Modal health endpoints returned HTTP 200.
The regression suites passed 456 tests.
Ruff found no error on changed source lines.
The documentation gate passed for Wikidata Studio, Job Service, and Deployment.

The code preserves P1932 on a new author link.
It removes only source-exact colophon wrappers after curator overrides and retains complete source blocks.
It preserves the exact existing active P31 rank on an approved foreign manuscript.
It keeps the progress publisher and heartbeat active through the complete build.
The optional boolean `extract_provenance_prose=false` skips only the optional AI phase.
The canonical cache salt is `hmo-wikidata-v24`.

The read-only full-corpus measurement preserved all 2,407 source items.
It omitted the 47 manual colophon removals and still produced the corrected source.
Its only other source fact change was the intended Pancieri P1932 qualifier.
The normal production source build then succeeded with the same item count.
Its job ID is `edb0540b-2cac-42b9-9c84-70d9894a608e`.

## Evidence files

- [Source facts](data/gila-after-2026-11-11-source-facts.csv).
- [Item receipts and public comparison](data/gila-after-2026-11-11-item-receipts.csv).
- [Resolved connection receipts](data/gila-after-2026-11-11-link-receipts.csv).
- [All checked QIDs](data/gila-after-2026-11-11-checked-qids.csv).
- [Changed QIDs and revisions](data/gila-after-2026-11-11-changed-qids.csv).
- [Exact repair receipts](data/gila-after-2026-11-11-exact-repairs.csv).
- [Original twelve manuscript batches](data/gila-after-2026-11-11-manuscript-batches.csv).
- [Initial foreign manuscript scope with final outcomes](data/gila-after-2026-11-11-foreign-review.csv).
- [Architecture and repeatable checks](../architecture/blocks/wikidata-studio/source-repair-safeguards.md).

Raw public snapshots and complete receipts remain under `state/gila-recheck-2026-11-11/`.
The source records and private session credentials are not part of the committed report.
