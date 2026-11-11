# Gila audit: three live examples, 11 November 2026

The live examples do not satisfy all of Gila's requests.
This audit read Wikidata entity JSON on 11 November 2026.
It made no Wikidata changes.
The parent audit checks the full corpus and the current source records.
This report also compares the three examples with the fresh production source export and current local build.

## Evidence and scope

The audit uses the latest batch API responses in `state/gila-live-audit-2026-11-11/live-snapshot.json`.
It copied the audited entities into `state/gila-live-audit-2026-11-11/examples/`.
Each JSON response contains the entity revision, modification time, claims, qualifiers, and references.
`example-summary.json` lists the exact duplicate reference blocks.
The reference comparison includes every source snak and excludes only the reference hash.
`source-records.json` contains the fresh production source export.
`built-items.json` contains the current local proposal.
The bounded source evidence resides in `examples/source-selected.json` and `examples/source-comparison.json`.

| Entity | Revision | Last modification, UTC |
|---|---:|---|
| [Q141747170](https://www.wikidata.org/w/index.php?title=Q141747170&oldid=2567905882) | 2567905882 | 2026-11-11 09:55:10 |
| [Q141747098](https://www.wikidata.org/w/index.php?title=Q141747098&oldid=2567890603) | 2567890603 | 2026-11-11 08:58:59 |
| [Q141175895](https://www.wikidata.org/w/index.php?title=Q141175895&oldid=2567906072) | 2567906072 | 2026-11-11 09:55:40 |
| [Q141175530](https://www.wikidata.org/w/index.php?title=Q141175530&oldid=2567879007) | 2567879007 | 2026-11-11 08:10:54 |
| [Q141754992](https://www.wikidata.org/w/index.php?title=Q141754992&oldid=2567874828) | 2567874828 | 2026-11-11 07:53:22 |
| [Q141175543](https://www.wikidata.org/w/index.php?title=Q141175543&oldid=2558074052) | 2558074052 | 2026-10-17 19:23:38 |
| [Q141755239](https://www.wikidata.org/w/index.php?title=Q141755239&oldid=2567875209) | 2567875209 | 2026-11-11 07:54:34 |
| [Q142031614](https://www.wikidata.org/w/index.php?title=Q142031614&oldid=2567874703) | 2567874703 | 2026-11-11 07:52:57 |

The local accepted model keeps physical manuscripts separate from written works.
The user chose that model on 5 November 2026.
The audit does not treat absent `P31=Q47461344` on a manuscript as a defect.
See [the accepted model](../architecture/blocks/wikidata-studio/gila-audit-completion.md).

## Verified corrections

| Request | Live result |
|---|---|
| Current labels and shelfmarks | All three English labels use the current institution and shelfmark. `P217` contains `Ms. 2473`, `Ms. EVR I 726`, and `Ms. Kaufmann A 315`. |
| Descriptions | All three descriptions use the current institution, shelfmark, and year. |
| Ktiv membership | All three have `P361=Q118384267` with `P3831=Q60474998`, the digital collection role. |
| Exact years | All three have year-precision `P571` values: 1681, 1650, and 1675. None has a circa qualifier on `P571`. |
| Hebrew years | Each `P571` has `P1932` with the Hebrew year. |
| Sukkah work link | `Q141747098` now links through `P1574` to `Q141754992`. It does not link to `Q141175530`. |
| Cantarini work link | `Q141175895` now links through `P1574` to `Q141755239`. It does not link to `Q141175543`. |
| Cantarini author | `Q141755239` has `P50=Q6076130`. |
| Named unknown scribe | `Q141747170` has `P11603 somevalue` with `P1932=פיבש יואל בן שמואל שליט`. The exact person identity remains unresolved. |
| Former owner | `Q141747170` links to Elkan Nathan Adler, `Q1170731`, with the former owner role. The claim retains `Ms. 4088` as a qualifier. |
| Separate sermon | `Q141747170` links to `Q142031614`, `דרשה על פרשת ויגש`. That work has `P136=Q60797`, sermon. It has no unsupported author claim. |
| Sukkah place | `Q141747098` has `P1071=Q1719466`, Panevėžys. |
| Sukkah colophon | One `P1684` retains the full colophon, including the detailed Hebrew date and `שמואל(?)`. Its qualifiers identify a colophon on folio `21א`. |
| Derushim alphabet | `Q141175895` has `P282=Q8229`, Latin script. It has no `P9302` claim for Hebrew-Italian script. |
| Derushim bibliography and scan | `Q141175895` retains the Weisz 1906 citation as `P528` text. `P953` and `P973` retain `http://real-ms.mtak.hu/14760/`. |

These results come from the entity revisions in the evidence table.
They establish the live values for these examples.
They do not establish completeness across the corpus.

## Confirmed open defects

### The old Derushim work still mixes source identities

`Q141175543` still has two `P2093` values.
The Cantarini claim cites source record `990001915930205171`.
The Abraham claim cites source record `990001123390205171`.
The entity has not changed since 17 October 2026.
The corrected manuscript link does not remove this old mixed identity.

| Author text | Claim ID | Source record |
|---|---|---|
| `קנטריני, יצחק חיים` | `Q141175543$28694B77-BE65-4E3B-A39D-49702D4CF9E7` | `990001915930205171` |
| `אברהם` | `Q141175543$BF5C795A-A21C-42F6-99FC-727FD21CBF5A` | `990001123390205171` |

The existing source review separates these works by author and source record.
It proposes that `Q141175543` retain only the Abraham evidence.
It assigns Cantarini's work to `Q141755239`.
See [the source review](../plans/gila-review-cases.md).
The live data still requires that separate repair.

### Exact duplicate references remain on the old mixed work

| Entity | Repeated reference blocks beyond the first identical block |
|---|---:|
| `Q141747170` | 0 |
| `Q141747098` | 0 |
| `Q141175895` | 0 |
| `Q141175543` | 6 |

These counts include only identical source snaks, including the retrieval date.
Different retrieval dates do not count as identical blocks.
The other audited entities have no exact duplicate reference blocks.
Gila explicitly requested removal of identical repeated references within each claim.

The first `Special:EntityData` responses contained older revisions for `Q141747170` and `Q141175895`.
Those older revisions had 25 and 13 duplicate blocks, respectively.
The newer batch API revisions supersede those observations.
The latest evidence confirms that the duplicate blocks are absent from all three example manuscripts.
The old mixed work still has six duplicate blocks.

### The Sukkah colophon also has an old duplicate statement

`Q141747098` has two colophon texts in `P1684`.
The first claim has the colophon role and folio `21א`.
The second claim repeats the text with doubled quotation marks and no role or folio.
Its claim ID is `Q141747098$2C6608CB-923F-4969-AA82-D02DB03D55E3`.
The exact reference comparison does not count this as a duplicate reference block.
It is a separate duplicate statement with defective quotation marks and absent context.

## Source review still required

| Question | Live evidence and remaining check |
|---|---|
| All contents and folios | `Q141747170` has two live `P1574` claims; the current local proposal has nine. `Q141747098` has one live claim; the proposal has two. No live claim has a folio range. |
| Sukkah supplement | Source `500$a` explicitly lists selections from Etz Chayim on folios `21ב-22ב`. The current proposal retains that range through `P958`. The live manuscript has no corresponding claim. |
| Sukkah scribe | Source `957$a` and the live colophon preserve `שמואל(?)`. The source has no structured 700 scribe. The entity has no `P11603`. Confirm the role before a separate uncertain scribe claim. |
| Sukkah script | Source `958$a` says `קראית-אשכנזית`. The live entity and current local proposal preserve only generic Ashkenazic script, `Q121094898`. Both lose the source distinction. |
| Buchach role | `Q141747170` uses `P7153=Q849109` with the role `place of attestation`. It no longer uses a provenance role. Check whether that role still overstates a place mentioned through a person. |
| Author authority links | `Q141175530` and `Q141754992` retain author text through `P2093`, without `P50`. The source export has no `100$0/100$1` for these records. The local proposals also lack authority evidence. |
| Derushim languages | Source `041$a` says `ita\|heb`; source `500$a` says Italian in Latin letters. The live entity preserves both languages. A scan or detailed catalogue must resolve their scope. |
| Derushim form and extent | The source has no `300$a` or prepared extent. `907$n=leaf0003.tif` names an image. It does not prove codex form. Absent live `P1104` is therefore not a confirmed transfer defect. |
| Provisional catalogue status | Source `590$a` says `רשומה זמנית`. The live entity has no explicit status note. This report retains the qualification without treating the year as approximate. |
| Bibliographic semantics | The Weisz citation survives as text in `P528`. Wikidata defines `P528` as a catalog code, normally with `P972`. Its current use needs model review. |

### Source components absent from the live Hizzuk Emunah manuscript

The source describes more components than the two live work links represent.
The current proposal has seven additional claims.
This count describes proposed source components, not seven established distinct literary works.
The two chronicle titles may describe the same passage and require identity review.

| Source field | Component | Source folios | Current live result |
|---|---|---|---|
| `500$a` | `סמנים על טענות כלליות` | `69א-69ב` | No corresponding `P1574` |
| `500$a` | Letter from Elia Levita to Sebastian Münster | `51א-52א` | No corresponding `P1574` |
| `500$a` | Historical annals about 1681–1685 | `70א` | No corresponding `P1574` |
| `500$a` | Account of Abraham's death at Hamburg | `70ב-71א` | No corresponding `P1574` |
| `500$a` | `העתק בשבועי' שבעי' דדניאל` | `71ב` | No corresponding `P1574` |
| `500$a` | `ראיתי לכתוב בקיצור נמרץ ענין גזירה תתנ"ו` | `72א-72ב` | No corresponding `P1574` |
| `740$a` | `כרוניקה על גזירות תתנ"ו (קצור)` | No explicit range in 740 | No corresponding `P1574` |

Source `500$a` also identifies polemical selections on folios `52א-52ב`, `53א`, `68א`, `71ב`, and `73ב`.
The current local proposal does not represent each selection as a distinct work.
The source does not establish an exact literary identity for each selection.
The report therefore keeps these selections as source scope questions.
Absent authority IDs in this export do not prove that the full catalogue lacks them.

The source identifies the letter's sender and recipient within the passage on folios `51א-52א`.
The live manuscript carries those roles through `P3342` on the whole manuscript.
Its live `P136` also classifies the whole manuscript as a letter, `Q133492`.
The current local letter work has no corresponding person role claims.
These roles and the genre need a component scope check.

The source identifies Buchach through a person in the passage on folio `68ב`.
It labels Buchach a related place through `751$e`.
The source does not establish manuscript custody or presence there.
The live `place of attestation` qualifier therefore still requires review.

### Exact dates and distinct events

Source `260$c` gives `תמ"א (1681)`, `ת"י (1650)`, and `תל"ה (1675)`.
The live `P571` values preserve those years without circa.
Source `957$a` gives the Sukkah colophon date as `כ"א לחדש תמוז שנת תיו יוד`.
The qualified live colophon preserves that detailed date verbatim.
The structured `P571` still uses year precision, rather than an exact calendar day.
This does not erase the detailed date from the entity.

The Hizzuk Emunah source also mentions events in 1684 and 1681–1685 within added historical passages.
Those event dates do not replace the manuscript's 1681 copy date.
The Hebrew year text and Gregorian years in the historical annals disagree within the source.
That disagreement needs source review; an automatic correction must not select one silently.

The NLI viewer URLs did not return accessible content through the web tool during this audit.
The audit uses Gila's letter, the prior source review, and the fresh production source export.
The export supplies the source-exact checks above.
This report does not claim a fresh independent NLI catalogue or scan check.

The property definitions support two model checks.
[P7153](https://www.wikidata.org/wiki/Property:P7153) means a significant place associated with the subject.
[P528](https://www.wikidata.org/wiki/Property:P528) means a catalog code, rather than a full bibliographic citation.

## Acceptance result

The audit confirms substantial corrections in the three examples.
It also confirms an unrepaired mixed work with duplicate references and a duplicate colophon statement on the Sukkah manuscript.
Source review must resolve the remaining contents, roles, identities, languages, and material scope.
The audit cannot certify all seven original entities against all nine requirements yet.
