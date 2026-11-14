# Gila email: live Wikidata verification, 12 November 2026

The main corrections are present, but the complete email request remains open.
The audit checked all 897 manuscript records and 2,162 public entity responses.
It found 62 absent content links and 50 existing links that require identity or scope review.
These 112 cases affect 77 manuscripts. They are audit flags, not 112 proven conversion defects.
This audit made no Wikidata, source-record, ledger, or curator changes.

## Evidence and time

The public Action API read took place from 10:15:08 to 10:19:39, Israel time.
The API responses retain revision IDs, qualifiers, ranks, complete references, and request timestamps.
The source export used production Postgres with `transaction_read_only=on`.
It contains 897 MARC records and 2,407 native entities: 897 manuscripts, 1,406 works, and 104 people.
The public read includes 828 linked work QIDs and 29 linked person QIDs.
It also includes historical targets and redirects that no current manuscript links to.

Run: `47e03437-09f2-4d6b-aea7-89fe79d76198`.
Evidence: `state/gila-verification-2026-11-12/`.
The files `source-records.json`, `native-items.json`, and `live-snapshot.json` contain the inputs.
The file `summary.json` records their SHA256 hashes and the aggregate results.
The file `full-audit.json` preserves the complete comparison and its review cases.

Source edits continued during this audit.
The native export ended at 10:15:32. The separate freshness check ended at 10:19:33.
The later check found a different source fingerprint and later person choices.
The app also marked 46 manuscript rows as due for a rebuild.
These facts prevent certification of the current source cache as a final, unchanged source.
They do not prove 46 incorrect public entities.

## Results against the nine requests

| Request | Verified result | Work that remains |
|---|---|---|
| 1. Labels, descriptions, and shelfmarks | All 854 resolved current 942 shelfmarks and English labels match. All 897 public source IDs match their manuscript records. The description check passes for all 897 records. | Seven records lack a current 942 shelfmark. Another 36 require a source choice. The description check does not independently certify every descriptive phrase. |
| 2. Physical class and written work | Every proposed physical class occurs on the corresponding public manuscript. | The accepted model keeps physical manuscripts and written works separate. Unproven codex form still requires source review. |
| 3. Work identity and links | Sukkah links to Q141754992. Cantarini links to Q141755239, which has P50=Q6076130. Q141175543 now redirects to Q141755239. | The audit flags 62 absent links and 50 existing links for identity or scope review across 77 manuscripts. One manuscript identity also has two public QIDs. |
| 4. Ktiv membership | All 897 manuscripts have P361=Q118384267 and the digital-collection role. | No gap appears in this check. |
| 5. People and roles | All 413 proposed named scribe claims occur on 400 public manuscripts. All 659 compared person/place claims retain their proposed values, ranks, and qualifiers. | All 413 scribe claims use an unknown value with the source name. They do not identify 413 linked people. Some author authority links remain unresolved. |
| 6. Contents and folios | Hizzuk has a separate Vayigash sermon. Its letter sender and recipient retain folios 51א–52א. | Hizzuk still lacks six source-specific content links. Sukkah still lacks the selection on 21ב–22ב. A content difference does not prove a separate physical unit. |
| 7. Dates, places, languages, script, and extent | The comparison finds 888 date facts, 1,097 language facts, 804 script facts, and 793 extent facts on the public entities. The three example years have Hebrew text and no circa qualifier. | Nine source date conflicts or boundaries remain source evidence. Derushim's language scope and physical extent still require source or scan review. |
| 8. Sources, bibliography, and inscriptions | The comparison finds 564 inscription facts and 278 explicit catalogue-code facts. All 897 mapped manuscripts have zero exact repeated reference blocks. Sukkah retains one scoped colophon with its uncertain scribe. | Bibliographic prose and catalogue-status notes remain source evidence. Another public item has ten exact repeated reference blocks. The Weisz 1906 citation is not a dedicated public statement. |
| 9. Complete corpus audit and report | The linked files list every checked manuscript, every public response, the source facts, and all review cases. | Full acceptance remains incomplete. A successful prior job or a local source pass does not close unresolved source identities. |

The accepted model follows the user's recorded decision of 5 November 2026.
It places `P31=Q47461344` on work entities and physical classes on manuscripts.
See [the accepted model](../architecture/blocks/wikidata-studio/gila-audit-completion.md).
This differs from the email's request for both classes on a manuscript.

## The three examples

All 24 explicit sample checks pass. This result covers those checks, not every request in the email.

| Manuscript | Corrections that the public read confirms | Open details |
|---|---|---|
| [Hizzuk Emunah, Q141747170](https://www.wikidata.org/w/index.php?title=Q141747170&oldid=2568087865) | Ms. 2473; 1681 with Hebrew year; named scribe; Adler as former owner with Ms. 4088; separate sermon; scoped letter roles; no unsupported Buchach manuscript-place claim. | Six content links remain absent. The work author has a complete P2093 name, but no P50 authority link. |
| [Iggeret Sukkah, Q141747098](https://www.wikidata.org/w/index.php?title=Q141747098&oldid=2568116221) | Ms. EVR I 726; correct work; 1650 without circa; copy place; exact Karaite-Ashkenazi term; one colophon on 21א with the Hebrew date and שמואל(?). | The selection from Etz Chayim on 21ב–22ב remains unresolved and absent as P1574. The retained doubt does not identify a certain scribe. |
| [Derushim, Q141175895](https://www.wikidata.org/w/index.php?title=Q141175895&oldid=2568071739) | Ms. Kaufmann A 315; correct Cantarini work and authority link; 1675 without circa; Latin alphabet; both catalogue languages; manuscript scan URL. | The source does not supply physical extent. The catalogue still requires language-scope review. The preliminary-record note and Weisz citation remain source evidence. |

The old mixed Derushim entity no longer mixes the two author identities.
[Q141175543](https://www.wikidata.org/wiki/Q141175543) redirects to the Cantarini work.
[Q141990285](https://www.wikidata.org/w/index.php?title=Q141990285&oldid=2568099849) retains the separate Abraham ha-Levi work.
The public Hizzuk work retains `יצחק בן אברהם מטרוקי` as an author string.
[Q141175530](https://www.wikidata.org/w/index.php?title=Q141175530&oldid=2568059725) still has no P50 author link.

## Remaining cases and limits

The comparison examines 10,518 source facts.
It finds 8,619 present facts, 1,787 facts retained as source evidence, 62 absent links, and 50 links that require identity review.
The 659 separate person/place checks compare native claims with public claims.
Those checks establish transfer; they do not independently prove every source interpretation.
The comparison does not prove that the parser extracts every fact in every MARC note.
The audit did not inspect every manuscript scan.

The source-case inventory has 90 absent-source cases and 1,979 identity or scope cases.
These are case counts, not counts of distinct manuscripts or proven public errors.
The fact inventory retains 2,549 review facts, including evidence-only catalogue notes.
The smaller 112-row work-link file isolates the current public-link flags.
The audit does not convert an unresolved title or a source note into a new public work.

Two public manuscripts have source ID `990001680850205171`:
[Q134926101](https://www.wikidata.org/w/index.php?title=Q134926101&oldid=2565038375) and
[Q141754492](https://www.wikidata.org/w/index.php?title=Q141754492&oldid=2568072613).
The current corpus maps to Q141754492.
Q134926101 has the ten exact repeated reference blocks found in this audit.
The identity conflict requires review before any merge or ledger change.

The exact-reference check ignores reference hashes and order. It compares every source snak, including retrieval dates.
References that cite the same source on different retrieval dates do not count as exact duplicates.
The [later retrieval audit](gila-reference-retrievals-2026-11-12.md) finds these repeated sources and records the local fix.
Redirect rows record the recipient's entity revision. They do not claim that revision for the redirect page.

## Files for review

- [All 897 manuscripts and field checks](data/gila-verification-2026-11-12-manuscripts.csv)
- [All 2,162 checked public responses](data/gila-verification-2026-11-12-checked-qids.csv)
- [The 112 open work-link flags](data/gila-verification-2026-11-12-open-work-links.csv)
- [Every source fact](data/gila-verification-2026-11-12-source-facts.csv)
- [Review facts, with source and reason](data/gila-verification-2026-11-12-review-facts.csv)
- [Absent-source and source-identity cases](data/gila-verification-2026-11-12-source-cases.csv)
- [Person and copy-place transfer checks](data/gila-verification-2026-11-12-person-and-place-claims.csv)
- [Exact repeated references and claim IDs](data/gila-verification-2026-11-12-duplicate-references.csv)
- [The 24 sample checks](data/gila-verification-2026-11-12-sample-checks.csv)
- [Revision differences since the previous snapshot](data/gila-verification-2026-11-12-changes-since-prior-snapshot.csv)

The last file records revision differences. It does not identify an editor or certify a correction.
This audit has no newly corrected QIDs because it made no public edits.
The earlier [repair receipts](data/gila-after-2026-11-11-exact-repairs.csv) retain the prior operator's exact corrections.
The fresh public read establishes the state described above.

Trellis capture: `b086d779-a297-4cca-9ebf-29e55ef1a772`.
The server classified the proposal as `ambiguous`, with no target concept.
Review status: pending; the candidate is not stable.
