# Gila completion: implementation and live state

The local implementation is ready for review. The live completion is blocked by the app access decision.
The agent did not apply the new public proposals or deploy this code.
The full email request remains open while public corrections and source reviews remain unresolved.

## Local changes

| Change | Result |
|---|---|
| Flattened date associations | The parser preserves empty slots and withholds an association when the arrays have different lengths. |
| Source evidence | `person_subfield_reviews` retains each field, name array, date array, and reason. The audit classifies it under point 5. |
| Troki authority | The exact full name and source dates `1533-1594` can supply P50=Q168171. A rejected match, shorter name, or different dates still blocks the fallback. |
| Cache versions | `source-aware-works-v22` and `hmo-wikidata-v25` require a fresh normal build after deployment. |
| Complete acceptance packet | The exporter covers all nine points and separates verified facts, source gaps, manual cases, supported corrections, and unchecked evidence. |
| Receipt proof | A corrected QID requires a verified receipt for the exact current public revision. |
| Repeated references | Normal owned updates retain one latest retrieval when all other source snaks match. Append-only paths preserve existing blocks. |

The 897-record source check finds 187 ambiguous date groups across 185 records.
The new parser retains these dates without a guessed association.
The person-date result stays the same after a second preparation on every record.
The check also finds 25 pre-existing `linked_880` differences outside this change.
The available local raw MARC samples contain none of the 185 affected records.
They cannot restore the missing occurrence associations.

The exact Troki proposal binds both source records that support the current work-author claim.
Both source records name the same author and give the same life dates.
The [official NLI authority](https://www.nli.org.il/en/a-topic/987007297464205171) corroborates that heading.
The public person Q168171 has NLI ID `987007297464205171` and VIAF ID `67288238`.
The proposal preserves the existing author string and every distinct source citation.

## Latest complete public read

The public read took place on 12 November 2026, from 10:42:53 to 10:47:18, Israel time.
It covers all 897 manuscript sources and 2,167 public responses, including 831 linked works.
The earlier operation completed 34 manuscript links and six source-specific person choices before this source export.
The native export contains 2,407 entities and has zero rebuild flags at its export time.
That export precedes the new local code versions above.

- All 854 resolved current shelfmarks and labels match.
- All 897 manuscript source IDs and qualified Ktiv memberships match.
- All 413 proposed named scribe claims remain present on 400 manuscripts.
- All 659 compared person and copy-place claims match their native proposals.
- The three examples pass 24 explicit checks.
- The latest comparison flags 107 work links across 74 manuscripts: 57 absent links and 50 identity-review cases.

These flags do not establish 107 incorrect public statements.
All 107 rows match the earlier source review by fact ID, control number, source text, and credited author.
The [complete disposition list](data/gila-completion-2026-11-12-work-dispositions.csv) retains each match and its proposed action.

| Class | Rows | Remaining evidence |
|---|---:|---|
| Safe author or work scope absent | 68 | Source or authority proof for the exact component |
| Source note or bibliographic part | 11 | A source decision about representation |
| Existing target candidate | 14 | Source-bound authority proof; a shared native P50 alone is insufficient |
| Structured part title | 9 | Parent identity and the exact part designation |
| No unique native identity | 5 | A versioned source identity choice |

The source inventory retains 90 absent-source cases.
The date fix adds explicit review cases; it does not fill absent source data.
The comparison covers 10,705 source facts, including 187 date-alignment facts.
It finds 8,624 present facts, 1,974 evidence-only facts, 57 absent links, and 50 identity-review links.

## Prepared public work

The exact packet is `state/gila-completion-2026-11-12/exact-correction-proposals.json`.
It contains one author proposal, three reference-cleanup proposals, and one duplicate-manuscript review.
Each public proposal retains its target revision, entity digest, and source evidence.
The packet is non-executable and grants no write permission.

The complete public read found twelve exact repeated blocks on Q134926101 and Q141175593.
A separate target check at 11:24:23, Israel time, found a newer revision on Q141175593.
That revision removed its exact repeats. Its retrieval-date variants still need a proposal.
All 897 mapped manuscripts have zero exact repeated blocks.
That result excludes repeated citations with different retrieval dates.

The [retrieval audit](gila-reference-retrievals-2026-11-12.md) corrects that limitation.
It finds 45,202 retrieval-date variants and ten exact redundant blocks on 16,633 claims across 1,619 entities.
The example Q141175529 retains four title references in public revision 2568055253.
The local proposal retains one title reference with the latest recorded date, 11 November 2026.
The full acceptance packet retains 1,619 current historical proposals.
The three smaller reference proposals cover Q134926101, Q141175593, and Q141175529.
Each target still requires a separate current source and ownership check.
The older Q134926101 and the mapped Q141754492 share source ID `990001680850205171`.
The duplicate review retains both complete target identities before any merge or ledger change.

The acceptance packet is `state/gila-completion-2026-11-12/acceptance/`.
It contains complete JSON, separate CSV files, a nine-point table, and a SHA256 manifest.
The supplied exact receipt array still matches the current revisions of Q134604699 and Q134688454.
These receipts describe earlier corrections. This operation made no new public corrections.
The packet contains 8,624 verified cases, 4,338 manual cases, and 1,619 repair cases.
All source manuscripts have a public read. The packet does not certify all requirements.
Later revisions of other targets require fresh proof before their receipts count as current corrections.

## Validation and documentation

The combined MARC, authority, audit, reference, and ownership suite passed 941 tests.
The final validation receipt records the exact test count and checks.
The new report modules pass strict `mypy`; the new files pass Ruff.
The modified legacy files retain unrelated existing lint findings.
The checks detect no new lint finding on the changed lines.

See [the completion contract](../architecture/blocks/wikidata-studio/gila-completion-packet.md),
[the date contract](../architecture/blocks/extraction/person-dates.md), and Rules W-322–W-323.
The architecture index and current cache versions match the code.

## Remaining live actions

Deploy the reviewed code through the normal deployment workflow.
Rebuild the source with the new versions.
Apply only source-proven proposals through the normal target and ownership guards.
Resolve the duplicate identity before a versioned ledger change.
Read every changed public entity again and attach its exact receipt.
Retain the remaining source cases for the supervisor's review.

The browser approval check rejected access to `https://mhm-pipeline.org` because the origin permission was declined.
The agent did not bypass that decision through another session or authentication path.

## Trellis candidates

Both captures await Review. Neither capture has a target concept.

| Capture ID | Proposal kind | Review |
|---|---|---|
| `e088de07-10c5-4bfa-adbf-e690f07b5e23` | regular | pending |
| `bfcd39a1-a092-49e7-9e78-e8487688bd44` | ambiguous | pending |

The first candidate records the person-date rule.
The second candidate records the reference retrieval rule.
