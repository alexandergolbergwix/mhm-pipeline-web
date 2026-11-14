# Gila: live repairs and final UI counts, 12 November 2026

I completed the supported live repairs and deployed the counter fixes.
All 897 manuscript rows have successful upload receipts.
The complete email request remains open because some source identities and scopes lack proof.
An upload receipt does not certify every scholarly interpretation.
A manuscript upload can succeed while an unresolved work link remains absent.

## Current UI counts

These counts use all 25 production table pages and the deployed frontend count function.
The pages contain 2,407 distinct items and 1,691 item approvals.

| Upload state | Items |
|---|---:|
| Upload succeeded | 1,752 |
| Reference only | 292 |
| Needs source review | 356 |
| Upload outcome unknown | 7 |
| Not uploaded | 0 |
| Update not confirmed | 0 |
| Confirmed upload failure | 0 |

All 356 source-review rows are works.
Of those rows, 324 need a safe work identity before creation.
The other 32 have conflicting or unverified author or source evidence.
Reference-only rows comprise 277 works and 15 people.
The seven unknown outcomes concern older work attempts and six public QIDs.
Q141990660 still combines several native work identities.
I did not repeat those uncertain public writes.

AI review has a separate count: 2,183 rows need a current pass.
The table has 224 full verdicts, three fail verdicts, one partial verdict, and 2,179 unknown verdicts.
The streamed export retains historical verdicts, so its verdict count must not replace the table count.
Neither item approval nor a successful upload supplies an AI pass.

[Machine counts](data/gila-final-2026-11-12-upload-counts.json).

## Repairs completed in this phase

- I completed 31 exact public author headings. Each claim retains the old literal, rank, qualifiers, and complete source blocks.
- I verified 12 further manuscript occurrences through normal source edits and guarded live jobs.
- I restored Q141746927 → Q142031906 and its archived source blocks. The fragment has no inferred broad folio range.
- I saved 34 exact manuscript bridges. The final rebuild and live job preserve their verified work targets.
- The final snapshot verifies all 46 supported manuscript occurrences, with every complete source block.
- I resolved 26 old unknown outcomes through fresh evidence and normal guarded checks.
- I saved six source-scoped person choices. Saadia uses two exact source records; the other people use one each.
- The person-choice jobs made no public person write and no pooled global ledger change.
- A separate guarded cleanup removed two exact duplicate reference blocks from Q141175593 at revision 2568224766. It changed no identity claim.

The fourth person-choice POST returned503 after its transaction committed.
A read-only database check found its proof and event.
The normal API then recovered that choice without another POST.
A final read-only ledger check confirms that no selected QID replaced its old pooled key.

[Exact repair receipts](data/gila-final-2026-11-12-exact-receipts.csv).

## Verification against the email

The final public read covers 2,140 entities.
Its main read ran from 08:39:05 to 08:43:28 UTC.
A later exact read confirms the reference cleanup on Q141175593.
A final read-only database query covers all 897 MARC records.
Every source value matches the baseline; no source record changed during this phase.
The current source cache has no rebuild flag.

| Request | Current result | Limit |
|---|---|---|
| Labels and shelfmarks | All 854 resolved current 942 shelfmarks and labels match. All 897 source IDs match. | Seven records lack a current shelfmark; 36 need a source choice. |
| Physical class and written work | Every proposed physical class occurs on its manuscript. | The user accepted separate physical manuscripts and written works on 5 November. I retained that model. |
| Work identities and links | Sukkah targets Q141754992. Cantarini targets Q141755239, with P50 Q6076130. Q141175543 redirects to that Cantarini work. | 57 absent exact links and 50 identity or scope flags remain across 74 manuscripts. |
| Ktiv | All 897 manuscripts have Ktiv membership and the digital-collection role. | No gap appears in this check. |
| People and roles | All 659 compared person/place claims retain their values, ranks, and qualifiers. All 413 named scribe facts occur on 400 manuscripts. | Those scribe values remain unknown people with source names. Transfer does not establish authority identity. |
| Contents and folios | The supported occurrence repairs retain their exact source scope. The fragment and its archived references remain present. | Hizzuk retains six open content-link flags. Sukkah retains the unresolved selection on 21ב–22ב. |
| Dates, places, languages, script, and extent | The public entities contain 888 compared date facts, 1,097 language facts, 804 script facts, and 793 extent facts. All three sample years retain Hebrew text without circa. | Nine date conflicts or boundaries remain source evidence. Derushim still needs language-scope review; the source lacks physical extent. |
| Sources, bibliography, and inscriptions | The public comparison finds 564 inscription facts and 278 explicit catalogue-code facts. Current mapped manuscripts have no exact repeated reference block. | Q134926101 retains ten duplicate blocks and a conflicting manuscript identity. Bibliographic prose, including the Weisz citation, remains source evidence. |
| Complete audit and report | All 24 explicit sample checks pass. The CSV files retain every checked manuscript and review flag. | The parser does not prove that it extracts every fact from every note. I did not inspect every manuscript scan. |

The comparison finds 8,624 present public facts.
The 107 open work-link flags do not establish 107 conversion defects.
A P50 identity needs explicit source-specific proof.
A matching name, native QID, or empty authority field supplies no approval.
The Gabirol donor merge and the Q134926101 identity conflict remain outside the completed cleanup.

- [All checked manuscripts](data/gila-final-2026-11-12-manuscripts.csv).
- [All public responses and revisions](data/gila-final-2026-11-12-checked-qids.csv).
- [Open work links](data/gila-final-2026-11-12-open-work-links.csv).
- [Full source facts](data/gila-final-2026-11-12-source-facts.csv).
- [Source cases](data/gila-final-2026-11-12-source-cases.csv).
- [Person and place checks](data/gila-final-2026-11-12-person-and-place-claims.csv).
- [Sample checks](data/gila-final-2026-11-12-sample-checks.csv).
- [Remaining exact duplicate references](data/gila-final-2026-11-12-duplicate-references.csv).

## Code, deployment, and jobs

Heroku v799 and Modal use backend commit `ce1d9d84`.
The code preserves complete deferred payloads, applies HMO curator overrides, and retains supported claim metadata.
A validated endpoint binds person choices to source evidence and keeps them reference only.
Single-item reads now select an exact SQL row.
The table counts successful adopt receipts and refreshes after terminal jobs.
It stops obsolete cursor walks and keeps unknown outcomes separate from confirmed failures.

Backend checks: 356 tests passed.
Deployed frontend checks: 20 tests passed.
The browser count scenario passed.
Production TypeScript builds passed.
The Mac lock and a browser-control fault prevented a final live screenshot.
The production API pages and deployed frontend tests verify the current counts.
The final audit uses an isolated snapshot of deployed commit `ce1d9d84`.
Concurrent parser edits in the shared workspace remain outside this verified release.

- Source rebuild: `8298446d-2162-457e-ace8-aada539da6fc`.
- Final dry job: `54a3d146-23a4-46af-843c-6a3d568d5073` — 34 existing targets and six reference-only skips.
- Final live job: `a1fb037a-69a0-452c-be58-f8424810e9a1` — 34 successful manuscript updates and six reference-only skips.

Docs: Rules W-315–W-321 and the Wikidata, HMO, authority, frontend, deployment, and versioning pages describe the final contracts.

Trellis capture `d99b0063-308b-4e4b-88fd-47f71d392f9a` is an ambiguous candidate.
It has no target concept. Review remains pending. It is not stable knowledge.
