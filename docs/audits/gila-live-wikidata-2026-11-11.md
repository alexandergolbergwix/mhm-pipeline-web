# Gila audit: live Wikidata, 11 November 2026

The audit is not complete. The main field fixes are present, but content links, historical work data, and references still require correction.
This audit made no Wikidata or database changes.

## Scope and evidence

The audit compared 897 production source records with 897 live manuscript entities.
It also read 794 linked work entities, including the four work entities in Gila's letter.
The batch API read took place from 12:00:58 to 12:03:27, Israel time, on 11 November 2026.
The snapshot contains 1,691 entities with revision IDs, qualifiers, and references.
These results describe that snapshot. Later edits require another read.

The source run is `47e03437-09f2-4d6b-aea7-89fe79d76198`.
The audit used the current source parser and a fresh local legacy proposal with 1,996 entities.
The local proposal had zero validator errors. That result does not establish live completeness.
The source export used a read-only Postgres transaction.

- [All 897 manuscript QIDs and field checks](gila-live-wikidata-2026-11-11-manuscripts.csv)
- [All 794 work QIDs and revisions](gila-live-wikidata-2026-11-11-works.csv)
- [Exact duplicate references, with claim IDs](gila-live-wikidata-2026-11-11-duplicate-references.csv)
- [All 1,310 local source-review cases](gila-live-wikidata-2026-11-11-source-review.csv)
- [Detailed checks for the three examples](gila-live-wikidata-2026-11-11-examples.md)

The local evidence directory is `state/gila-live-audit-2026-11-11/`.
`live-snapshot.json` contains the API responses. `corpus-summary.json` contains the counts and input hashes.
`source-records.json` and `built-items.json` preserve the source and proposal used for this comparison.
The audit used the previous receipt for one QID absent from the current ledger.
That entity, `Q141754492`, has the correct live source ID, `990001680850205171`.
Its local ledger entry requires review.

## Results against all nine requests

| Request | Verified result | Open work |
|---|---|---|
| 1. Labels and current shelfmarks | All 854 source records with a resolved current 942 shelfmark match the live shelfmark and English label. All 897 live source IDs match. The three example descriptions are correct. | Seven records lack a current source shelfmark. Another 36 require a source choice. This audit did not certify every description field. |
| 2. Physical classes and written works | All 762 proposed codex classes occur on the same live manuscripts. | The accepted model keeps `written work` on separate work entities. A matching proposal does not independently prove every physical form. |
| 3. Work identity and links | The Sukkah manuscript links to `Q141754992`. The Cantarini manuscript links to `Q141755239`, which has `P50=Q6076130`. | `Q141175543` still mixes Cantarini and Abraham. Other work identities require source review. |
| 4. Ktiv membership | All 897 manuscripts have `P361=Q118384267` with the digital collection qualifier. | No defect occurred in this selected check. |
| 5. People and roles | All 413 structured source scribe entries survive as named unknown-value claims on 400 manuscripts. The Adler former-owner role and former shelfmark survive. | These claims preserve names, not resolved person identities. The local report retains author and scribe identity cases. |
| 6. Contents and folios | The live manuscripts have 1,073 work links and 34 folio-qualified links. | The current proposal has 1,428 work links and 90 folio-qualified links. The differences affect 198 and 28 manuscripts, respectively. |
| 7. Dates, places, scripts, and extent | All 897 proposed date values match. All 779 proposed Hebrew date texts survive. No matching exact source date has an unsupported circa qualifier. All 202 proposed place sets, 896 alphabet sets, and 897 language sets occur live. | The Karaite-Ashkenazi detail is absent from the Sukkah item and the local proposal. Eight language-scope cases require source review. Extent and physical scope still need review. |
| 8. Subjects, sources, bibliography, and inscriptions | All 532 proposed bibliography-text sets occur live. The Sukkah item preserves the full qualified colophon and folio. | Exact repeated references remain. The Sukkah item also has an old duplicate colophon. Some genre and person-role claims need component scope. |
| 9. Full corpus receipt | The attached files list every checked manuscript and work QID, revisions, field differences, and source-review cases. | The audit cannot certify all requests. This audit corrected no live entities. |

The accepted model comes from the user's decision on 5 November 2026.
It keeps physical manuscripts and written works separate.
Therefore, absent `P31=Q47461344` on a manuscript is not a defect under that decision.
See [the accepted model and audit limits](../architecture/blocks/wikidata-studio/gila-audit-completion.md).

## Confirmed defects and source differences

### Historical work identity

[Q141175543](https://www.wikidata.org/w/index.php?title=Q141175543&oldid=2558074052) still combines Cantarini and Abraham from two source records.
Its revision has not changed since 17 October 2026.
The corrected manuscript link does not repair this historical entity.
The detailed example report retains the author claim IDs and source IDs.
Do not merge works from their shared title alone.

### Exact repeated references

The audit found 850 duplicate reference blocks on 52 manuscripts.
It also found 12 duplicate blocks on three works: `Q141175543`, `Q141755231`, and `Q141755236`.
The total is 862 duplicate blocks on 55 entities.
The comparison includes all reference snaks and retrieval dates. It ignores only hash values and order.
It counts each repeated block after the first identical block within one claim.

All three sample manuscripts now have zero exact duplicate reference blocks.
The earlier example responses were older than the batch API responses for two manuscripts.
The detailed report marks those earlier counts as superseded.
The reference CSV contains the current affected QIDs, revisions, properties, claim IDs, and reference hashes.

### Contents and folio differences

The proposal has 355 additional work links across 198 manuscripts.
The live count is lower on each affected manuscript; no manuscript has a higher count than its proposal.
This difference identifies review cases. It does not authorize every proposed work or prove its identity.
The current builder also has known gaps for repeated titles and distinct folio ranges.
See [the documented audit risks](../architecture/blocks/wikidata-studio/gila-audit-risks.md).

[Q141747170](https://www.wikidata.org/w/index.php?title=Q141747170&oldid=2567905882) has two work links; its source-based proposal has nine.
The source names additional contents, including a letter on folios `51א–52א`.
[Q141747098](https://www.wikidata.org/w/index.php?title=Q141747098&oldid=2567890603) has one work link; its proposal has two.
Its source identifies selections on folios `21ב–22ב`, including an unresolved Etz Chayim source.
The missing selections require source-qualified representation without a guessed work identity.

The Sukkah item retains an old duplicate colophon statement:
`Q141747098$2C6608CB-923F-4969-AA82-D02DB03D55E3`.
That statement has doubled quotation marks and lacks the colophon role and folio.
The separate, correct statement preserves the full text, exact Hebrew date, `שמואל(?)`, and folio `21א`.

The Sukkah source states `קראית-אשכנזית` in `958$a`.
Both the local proposal and live item retain only generic Ashkenazic script.
This is a remaining loss of source detail.

## Cases that require review

The current local source report has 90 absent-source cases and 1,220 identity or scope cases.
These are case counts, not counts of distinct manuscripts.
The report lists zero transfer failures in its selected checks.
Those selected checks do not cover all content links, historical claims, or script detail.
Therefore, zero selected failures does not establish complete transfer.

| Class | Selected reason | Cases |
|---|---|---:|
| `absent_in_source` | No physical extent in the source | 83 |
| `absent_in_source` | No current 942 shelfmark in the source | 7 |
| `identity_or_scope_uncertain` | Named scribe identity unresolved | 412 |
| `identity_or_scope_uncertain` | Author identity lacks a link | 391 |
| `identity_or_scope_uncertain` | Production place unresolved | 107 |
| `identity_or_scope_uncertain` | Image name does not prove physical form | 97 |
| `identity_or_scope_uncertain` | Current institution requires a source choice | 36 |
| `identity_or_scope_uncertain` | Source extent is not numeric | 21 |
| `identity_or_scope_uncertain` | Language scope requires a scan check | 8 |

The source-review CSV contains all remaining groups.
The detailed example report distinguishes absent source data from lost data and unresolved identity.
It also retains the provisional catalogue status for Derushim.
For one scribe, `Q141748424` uses a normalized name without the source question mark.
Its `P1480=Q30230067` qualifier preserves the uncertainty. The other 412 source names match exactly.

Ten linked works have more than one author value across `P50` and `P2093`.
Most are duplicate name forms or a text name beside an entity link.
Multiple author values alone do not establish a mixed work.
The confirmed mixed case remains `Q141175543`.

## Required closure evidence

1. Repair the historical mixed work after source review.
2. Remove exact repeated references through the applicable owned-item or approved-item path.
3. Resolve the missing source components and their folio ranges.
4. Preserve the specific script detail and remove the defective duplicate colophon.
5. Resolve or explicitly retain uncertain author, scribe, language, place, and physical-scope cases.
6. Read the affected live entities again after each repair.
7. Produce a correction receipt with the actual changed QIDs and remaining cases.

This audit has no corrected-QID receipt because it made no live changes.

## Trellis review candidate

Capture `2d86e2ff-5d7b-4cb2-84dc-b0beb8805751` has proposal kind `ambiguous`.
Trellis selected no target concept. Review is pending; the candidate is not stable.
The candidate links the earlier dated audit and proposes the new evidence as a follow-up.
