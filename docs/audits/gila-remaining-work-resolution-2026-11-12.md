# Gila: the remaining work links and source identity

This audit makes no live change.
The audit classifies all 182 P1574 facts from the final comparison on 12 November 2026.
Those facts comprise 106 `missing` cases and 76 `identity_review` cases.
The audit uses the exact MARC records, current native items, and fresh Action API reads.
The fresh read covers 82 public work and person entities.

## Exhaustive result

| Class | Facts | Proposed action |
|---|---:|---|
| Verified existing target | 32 | Correct 26 audit flags and resolve six missing associations. |
| Exact public author heading | 31 | Complete the source heading, then use the normal target guard. |
| Another witness of a heading repair | 3 | Repair the same target first and require exact normal reconciliation. |
| Source note or bibliographic part | 34 | Preserve the source decision; the whole note is not one missing work. |
| Structured part title | 9 | Resolve the parent work and retain the part designation. |
| Missing safe author or scope proof | 68 | Retain the exact source evidence and the identity review. |
| No unique native identity | 5 | Resolve the occurrence before a public target choice. |

The machine packet records one row for each original fact ID.
It retains the source record, title, author, folio, native target, public target, and exact proof.
Every statement index refers to the final export.
The operator must recalculate the base index before an override edit.
The packet does not authorize a public write by itself.

## Four repeated audit defects

The live author check compares source text with P2093 and P1932.
It does not follow a proven P50 person identity.
For example, [Maggid Mesharim](https://www.wikidata.org/wiki/Q23498026) has the same P50 Q467148 as the source-backed native item.
The audit still marks that existing manuscript link as an identity review.

The text normalizer does not collapse doubled quote transport forms.
The source author for [Midrash David](https://www.wikidata.org/wiki/Q141754963) contains doubled quotes in `הרמב""ם`.
The completed public author contains the corresponding single quote.
The exact source identity does not change.

The audit first marks an unknown target as `identity_review`.
Its later author and source checks do not clear that status after proof succeeds.
[Loanz's Tsafnat Paneah](https://www.wikidata.org/wiki/Q141755388) illustrates this case.
The existing source-qualified link and complete author literal already agree.

The audit also counts 43 deferred source notes or part titles among the 106 missing links.
A catalogue sentence can name several works or describe a part without identifying one standalone work.
These 43 cases do not prove 43 missing standalone P1574 assertions.
The nine structured part titles still need a parent-work identity decision.

The source code for these checks is [wikidata_gila_facts.py](../../backend/app/pipeline/wikidata_gila_facts.py).
The evidence directory contains five minimal fixtures for these distinct cases.

## Exact author heading repairs

The packet proposes 31 further P2093 heading completions.
For each proposal, MARC100$a matches the current short literal.
MARC100$a and MARC100$c supply the complete name.
Every source block on that claim binds to one of the checked source records.
Every checked source record supplies the same complete heading.
The proposal preserves the current rank and every reference block.
It retains the old short form as P1932.
It does not choose a new person QID.
The prior exact heading validator passed all 31 source fixtures with zero issues.
This local result does not replace a fresh database source check or a live receipt.
A fresh source read, full entity comparison, and normal write guard remain mandatory.

## The lost fragment relation

The source740 field for CN990000580410205171 explicitly names `שער המצוות (קטע)`.
The current native item already targets [Q142031906](https://www.wikidata.org/wiki/Q142031906).
The public target has the same P1476 and source references for that exact record.
The native manuscript still has this relation at final statement index 24.

The archived manuscript claim is `Q141746927$3193CE03-B8C1-4461-80EA-A1C6E982F8FE`.
It has P1932 for the fragment, P1480 for uncertainty, and two complete reference blocks.
The current public manuscript lacks that relation.
The packet proposes its source-bound restoration through the corrected connection path.
The restoration must preserve both archived source blocks.

MARC500 places two fragments together at folios 275ב–279ב.
That group range does not prove the interval of this one fragment.
The operator must not add that interval or a named author to the fragment relation.
The operator must not merge this fragment with the complete Shaar ha-Mitzvot work.
The [official catalogue record](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000580410205171) is the source boundary.

## Evidence and limits

The frozen packet is `state/gila-resolution-2026-11-12/work-proposals.json`.
Its SHA256 is `e9fe34d75aa39bdc041e9b0c9e7096ebb54e11ad0c8eb3b49a0b0f6ff335694d`.
The packet includes the hashes of its five audit fixtures.
The companion `public-heading-execution-proposals.json` contains the complete expected entities for the 31 heading repairs.
The `fresh-work-evidence.json` file retains exact public revisions and API request timestamps.
The `lost-known-links.json` file retains the complete earlier fragment claim.

The audit does not infer author absence from a native omission.
It does not choose an existing work from its title alone.
The remaining evidence gaps can require another catalogue, authority record, or scan.
The packet preserves those gaps and does not classify them as successful writes.

## Additional primary-source proposals

A separate supplement records six further existing-target proposals and one public author addition.
Four proposals concern Vital's primary works on four source records.
The official [NLI catalogue metadata](https://www.nli.org.il/en/manuscripts/NNL_YBZ990037511990205171/NLI) binds Vital's complete heading and dates to authority ID `987007269661905171`.
[Q2302424](https://www.wikidata.org/wiki/Q2302424) has that exact P8189.
This evidence supports an author identity check without a person-name guess.
The decisions must remain specific to their source occurrences.
They cannot retarget the pooled native work key.

The other two proposals concern [Maavar Yabbok](https://www.wikidata.org/wiki/Q7066673) and [Shaarei Orah](https://www.wikidata.org/wiki/Q42195513).
Their source title-page quotations identify the author and work.
The native source evidence and public P50 identities corroborate those identifications.
The [NLI catalogue subject](https://www.nli.org.il/en/dissertations/NNL_ALEPH990023334360205171/NLI) also identifies Gikatilla's work and complete author heading.

The seventh proposal concerns [Adam Yashar](https://www.wikidata.org/wiki/Q134619519).
Its public description already names Vital, but it has no P50 or P2093.
The current native target is that exact QID, and MARC100 supplies the complete author heading.
A public author addition still requires normal exact-target acceptance and a fresh complete entity check.
The proposal preserves all current public claims and metadata.

The supplement is `state/gila-resolution-2026-11-12/primary-work-supplement.json`.
It does not alter the frozen exhaustive packet.
The Boethius translation and the Hanin/Gibbor attribution remain outside these proposals.
Direct NLI and VIAF requests returned HTTP errors; the report does not claim full authority-page reads.
The cited NLI evidence comes from the indexed official catalogue metadata.
