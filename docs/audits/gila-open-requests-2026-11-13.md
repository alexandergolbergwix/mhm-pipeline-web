# Gila: requests that remain open, 13 November 2026

Gila's full request remains open. This report checks each of her nine requests.
The audit made no public edit, application write, or database request.
The user permits future edits only to items that `Alexander Goldberg IL`, user ID `6935067`, created.

## Evidence and scope

The fresh public read covers 897 mapped manuscripts and 2,167 public responses, including 831 linked works.
The read ran on 13 November, from 09:23:06 to 09:24:34, Israel time.
The comparison uses the saved MARC and native-item export from 12 November at 10:42, Israel time.
The source export contains 897 records and 2,407 native items.
This audit does not establish whether the application source or deployed build changed after that export.

All 24 existing sample checks pass. Those checks do not cover every sentence in the email.
The audit compares source facts and native proposals with public statements.
It does not establish that the parser extracts every fact from every note.
It does not replace a review of the manuscript scans.
The later [code assessment](../plans/gila-code-improvements-2026-11-13.md) reproduces a cross-occurrence holding-location fallback in a synthetic fixture.
The 854 label passes compare the parser's interpretation with public values.
They do not independently prove every original 942 association.
The fixture does not establish a production impact count.

## Nine-request comparison

| Email point | What the current evidence confirms | What remains open |
|---|---|---|
| 1. Labels, descriptions, and current shelfmarks | All 854 resolved current shelfmarks and English labels match. All 897 source IDs and compared English descriptions match. | Seven records lack a current shelfmark in the saved source. Another 36 require a choice of the current holding. |
| 2. Physical class and written work | Every proposed physical class occurs on its manuscript. The project uses separate physical manuscript and written-work items. | Gila requested both classes on the manuscript. The later project decision differs and needs an explicit explanation. Another 97 form cases lack sufficient source proof. |
| 3. Work identities and links | Sukkah targets Q141754992. Derushim targets Q141755239, with Cantarini as Q6076130. Q141175543 redirects to that work. | The audit flags 107 occurrences across 74 manuscripts: 57 absent links and 50 identity or scope cases. All 57 absent links lack a proven public target. |
| 4. Ktiv membership | All 897 manuscripts have Ktiv membership and the digital-collection role. | The compared membership requirement has no detected gap. |
| 5. People, roles, and authorities | All 413 compared named scribe claims occur on 400 manuscripts. All 659 compared person and copy-place claims match. | Authority identities remain unresolved in many cases. The Hizzuk work still lacks the Troki P50 link. Another 187 name/date groups across 185 records need occurrence-level proof. |
| 6. Contents, related texts, and folios | The comparison verifies 655 content-occurrence facts. Supported folios remain attached to their occurrences. | The open work links also affect this request. Hizzuk has six absent content links. Sukkah has an unresolved selection from Etz Chayim on 21ב–22ב. |
| 7. Dates, places, languages, script, and extent | The comparison verifies 888 date facts, 1,097 language facts, 804 script facts, and 793 extent facts. The three example years have Hebrew text and no circa qualifier. | Nine date conflicts or boundaries need review. The source cases also include 107 unresolved copy places, eight language-scope cases, and 21 non-numeric extents. Another 83 records lack extent in the saved source. |
| 8. Subjects, genres, references, bibliography, scans, and inscriptions | The comparison verifies 564 inscription facts and 278 catalogue-code facts. The Sukkah colophon retains its folio, date, place, and uncertain scribe. | Repeated references remain. The Weisz 1906 citation and preliminary-record note remain source evidence. Bibliographic identities and the scope of some note content still need review. |
| 9. Corpus-wide checks and a final report | All 897 mapped manuscripts have a fresh public read. The report and CSV inventories separate source gaps, uncertain identities, and public repairs. | Full correction acceptance remains false. The latest local conversion fixes need deployment proof and a fresh normal build. The report cannot certify every free-text detail or scan. |

The 107 work-link flags do not establish 107 incorrect public statements.
A missing link without a proven target requires identity review before a public edit.
A named unknown scribe retains the source name. That representation does not establish an authority identity.
The counts above overlap and must not be added as distinct manuscript totals.

The generated packet marks point 9 as verified because every mapped manuscript has a current public read.
That result does not mean that every correction in the email is complete.
Its point 2 checks the accepted project model, rather than Gila's literal request for both classes on one item.

## Gaps in the three examples

- [Hizzuk Emunah, Q141747170](https://www.wikidata.org/wiki/Q141747170): six content occurrences remain absent as public work links. Their source folios are 69א–69ב, 51א–52א, 70א, 70ב–71א, 71ב, and 72א–72ב.
- [Hizzuk work, Q141175530](https://www.wikidata.org/wiki/Q141175530): P2093 retains `יצחק בן אברהם מטרוקי`, but P50 remains absent. The exact Troki fallback to Q168171 exists in local code. This audit does not prove its deployment.
- [Iggeret Sukkah, Q141747098](https://www.wikidata.org/wiki/Q141747098): the Etz Chayim selection on 21ב–22ב still lacks a proven work target. The copy place is Panevėžys, Q1719466. The colophon on 21א retains כ״א בתמוז and `שמואל(?)`.
- [Derushim, Q141175895](https://www.wikidata.org/wiki/Q141175895): the catalogue's Hebrew and Italian language scopes need review against the scan. The saved source lacks physical extent. The Weisz citation and preliminary-record note remain source evidence.

The sample labels and shelfmarks now match Gila's examples.
The Sukkah wrong-work link and the mixed Derushim link no longer appear in the sample checks.
The audit does not classify those resolved cases as open.

## Repeated references and the creator restriction

The fresh audit supports removal of 44,709 repeated reference blocks from 16,339 statements on 1,600 owned items.
It also finds 457 repeated blocks on 277 statements across 17 foreign items.
The creator restriction excludes all 17 foreign targets from edits.
Ten exact repeated blocks occur on the foreign item Q134926101.
That item also shares its catalogue ID with Q141754492 and requires identity review.

These totals include exact repeats and otherwise identical citations with different supported retrieval dates.
The audit preserves distinct sources and ambiguous dates.
The earlier individual corrections removed 46 repeated blocks and corrected two person descriptions.
The four correction receipts still match the current public revisions.
No generated role-list description remains in the separately checked owned scope.

The latest bulk attempt failed the `maxlag=5` check before an edit.
Its read-only probes also failed. The bulk cleanup remains unapplied.
No new lag check or write occurred as part of this nine-request audit.

## Actions that can close the open requests

1. Explain the separate manuscript/work model in the answer to Gila.
2. Resolve the 36 current-holding choices. Retain the seven absent shelfmarks as source gaps until evidence supplies them.
3. Review the 107 content links by source text, author, and folio. Do not select a work by title alone.
4. Verify the Troki authority link and the remaining person identities. Recover occurrence-level evidence for the 187 ambiguous name/date groups.
5. Review the date conflicts, copy places, language scopes, non-numeric extents, and bibliography against the catalogue or scans.
6. Confirm deployment of the latest local conversion and creator guards. Rebuild through the normal application workflow and retain its proof.
7. Apply only supported corrections to creator-verified targets. Keep the normal lag limit for the bulk process.
8. Read each changed item again. Attach an exact current-revision receipt and regenerate the acceptance packet.

Missing source data and uncertain identity require different actions from a failed transfer.
The current selected source comparison finds no `present_not_transferred` source case.
Its 57 absent work links remain identity reviews, rather than automatic upload instructions.

## Evidence files

- [All checked public QIDs and revisions](data/gila-recheck-2026-11-13-checked-qids.csv).
- [All 897 manuscript checks](data/gila-recheck-2026-11-13-manuscripts.csv).
- [The 107 open work-link flags](data/gila-recheck-2026-11-13-open-work-links.csv).
- [Source gaps and review cases](data/gila-recheck-2026-11-13-source-cases.csv).
- [All compared source facts](data/gila-recheck-2026-11-13-source-facts.csv).
- [The 24 sample checks](data/gila-recheck-2026-11-13-sample-checks.csv).
- [The current repeated-reference queue](data/creator-cleanup-2026-11-13-remaining.csv).
- [Verified creator-scoped corrections](creator-scoped-cleanup-2026-11-12.md).

The evidence directory is `state/gila-recheck-2026-11-13/`.
It contains the saved source export, fresh public snapshot, complete audit, acceptance packet, and SHA256 manifest.
The packet has 8,624 verified cases, 4,338 manual cases, and 1,617 reference-repair cases.
Case counts are not distinct entity counts or proven error counts.
The 1,617 repair targets include the 17 foreign exclusions.
The acceptance packet and every proposal remain non-executable.

Trellis capture `9407c8cc-6cf5-4437-97de-79b3db769077` is an ambiguous candidate.
It has no target concept. Review remains pending.
