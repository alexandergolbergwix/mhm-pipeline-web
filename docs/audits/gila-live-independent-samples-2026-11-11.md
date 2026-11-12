# Gila: independent sample audit before the full update

The seven sample entities did not satisfy all requirements before the full update.
This audit made no Wikidata edits.
The parent task later started the full guarded upload.
The results below describe the state before that upload.
A later receipt must establish which defects the upload corrected.

## Evidence

The audit read the Wikidata `wbgetentities` API at 19:21:46 UTC on 11 November 2026.
The request disabled the shared response cache with `maxage=0` and `smaxage=0`.
The evidence resides in `state/gila-independent-samples-2026-11-11/entities.json`.
The API revisions match the earlier sample report.
The audit also read the existing export of 897 source records.
That export resides in `state/gila-live-audit-2026-11-11/source-records.json`.
It did not obtain a new independent NLI catalogue or scan response.

| Entity | Revision | Last change, UTC |
|---|---:|---|
| [Q141747170](https://www.wikidata.org/w/index.php?title=Q141747170&oldid=2567905882) | 2567905882 | 11 November 2026, 09:55:10 |
| [Q141747098](https://www.wikidata.org/w/index.php?title=Q141747098&oldid=2567890603) | 2567890603 | 11 November 2026, 08:58:59 |
| [Q141175895](https://www.wikidata.org/w/index.php?title=Q141175895&oldid=2567906072) | 2567906072 | 11 November 2026, 09:55:40 |
| [Q141175530](https://www.wikidata.org/w/index.php?title=Q141175530&oldid=2567879007) | 2567879007 | 11 November 2026, 08:10:54 |
| [Q141754992](https://www.wikidata.org/w/index.php?title=Q141754992&oldid=2567874828) | 2567874828 | 11 November 2026, 07:53:22 |
| [Q141175543](https://www.wikidata.org/w/index.php?title=Q141175543&oldid=2558074052) | 2558074052 | 17 October 2026, 19:23:38 |
| [Q141755239](https://www.wikidata.org/w/index.php?title=Q141755239&oldid=2567875209) | 2567875209 | 11 November 2026, 07:54:34 |

## Confirmed corrections

| Requirement | Result before the full update |
|---|---|
| Labels, descriptions, shelfmarks | All three manuscripts use the current institution and shelfmark from 942. |
| Manuscript and work classes | The first two manuscripts have the codex class. Derushim retains the base manuscript class. |
| Work links | Sukkah links to Q141754992. Cantarini's manuscript links to Q141755239. |
| Cantarini identity | Q141755239 has `P50=Q6076130`. |
| Ktiv | Each manuscript has `P361=Q118384267` with the digital collection role and its own source identifier. |
| Named scribe | Hizzuk Emunah has `P11603 somevalue` with the exact name in `P1932`. |
| Former owner | Hizzuk Emunah links to Adler with the former owner role and former shelfmark `Ms. 4088`. |
| Dates | The three years are 1681, 1650, and 1675. They have Hebrew year text and no circa qualifier. |
| Sukkah place and colophon | The entity has Panevėžys as its place of creation. The scoped colophon preserves the detailed date and `שמואל(?)`. |
| Derushim alphabet | The entity has Latin script and no Hebrew-Italian `P9302` claim. |
| Exact repeated references | Each of the three manuscripts has zero exact duplicate reference blocks. |

The accepted model separates physical manuscripts from written works.
The user chose that model on 5 November 2026.
The audit therefore does not classify absent `Q47461344` on manuscripts as a defect.
See [the accepted model](../architecture/blocks/wikidata-studio/gila-audit-completion.md).

## Confirmed defects and source review

| Class | Entity or source | Result before the full update |
|---|---|---|
| Incorrect mixed identity | Q141175543 | The old work still has Cantarini and Abraham author claims from different source records. It also has six exact duplicate reference blocks. |
| Duplicate statement | Q141747098 | A second colophon has doubled quotes, no role, and no folio. The correct scoped colophon already exists. |
| Present, not transferred | Q141747098 | Source `958$a` says `קראית-אשכנזית`. The live `P9302` has only generic Ashkenazic script and no exact source term. |
| Present, not transferred | Q141747098 | Source `500$a` lists Etz Chayim selections on folios `21ב–22ב`. The manuscript has no corresponding work occurrence or folio qualifier. |
| Present, not transferred | Q141747170 | The manuscript has two `P1574` claims and no folio ranges. Source 500 and 740 describe further components. |
| Scope uncertain | Q141747170 | The whole manuscript has the letter genre and sender/recipient roles. The source places the letter on folios `51א–52א`. |
| Scope uncertain | Q141747170 | Buchach has the place-of-attestation role. The source gives a related place through a person; it does not establish manuscript presence there. |
| Identity uncertain | Q141175530 and Q141754992 | Both works retain author text without `P50`. The source export lacks exact authority identifiers for those authors. |
| Identity uncertain | Q141747098 | The colophon retains `שמואל(?)`. That uncertain name does not establish a definite person identity. |
| Scope uncertain | Q141175895 | Source 041 gives Italian and Hebrew. Source 500 specifies Italian in Latin letters. A catalogue or scan check must resolve their scope. |
| Absent in source export | Q141175895 | The source has no prepared extent or 300$a. The image filename does not establish codex form. |
| Source qualification | Q141175895 | Source 590$a says `רשומה זמנית`. The audit retains that note without a general uncertainty inference. |
| Property model review | Q141175895 and Q141747170 | Full bibliographic citations survive in `P528`, which represents a catalogue code. The property model requires review. |

The corrected Cantarini manuscript link does not repair the old mixed work.
The source distinguishes Abraham the Levite from Morocco from Isaac Hayyim Cantarini.
The shared title `דרושים` does not justify a merge.
The report does not equate each source component with a distinct established literary work.

## Why the exact repair proposer retains the Sukkah duplicate

The audit called `build_gila_repair_proposals` with the current source and the fresh entity snapshot.
The pure function returned no proposal for Q141747098.
Only its text-equivalence gate failed.
The gate collapses doubled quotes, but the old text also has outer quotes and a terminal period.
The prepared source omits that catalogue wrapper.
The texts therefore remain unequal after the permitted transformation.

All other gates passed: Hebrew language, normal rank, retained colophon role, retained folio, and the source subset check.
Both claims cite source record `990000802880205171`.
The old claim has no qualifiers.
The earlier batch did not simply omit an otherwise eligible repair.
The exact text guard excludes this case.
Any repair must preserve the scoped claim and every distinct reference.

The diagnostic evidence resides in `state/gila-independent-samples-2026-11-11/colophon-diagnosis.json`.
The [repair predicate](../../backend/app/pipeline/wikidata_gila_repairs.py) defines the text gate.
The [source adapter](../../backend/app/pipeline/wikidata_gila_execution.py) prepares the current colophon.

## Limit

This sample check does not certify the full corpus.
The parent audit must check every relevant entity and retain source review cases after the upload.
An upload success does not establish full semantic correctness.

## Later full-corpus result

The authorized operator later completed the full source comparison and supported live repairs.
The final public read covers 897 source manuscripts and 2,135 public entities.
The [final audit](gila-live-update-recheck-2026-11-11.md) supersedes these earlier sample statuses.
It retains every unresolved source identity and scope case.
