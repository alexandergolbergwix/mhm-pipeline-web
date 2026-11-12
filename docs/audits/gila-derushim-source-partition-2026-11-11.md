# Derushim: source partition and the old mixed work

The sources and entity history establish two different work groups.
Q141175543 originally represents Cantarini, not Abraham.
A separate Abraham work already exists as Q141990285.
After source cleanup, Q141175543 and Q141755239 represent duplicate Cantarini works.
The preferred recipient is the established active target Q141755239.
This audit made no live edits.

## Evidence and scope

The audit used the source export at `state/gila-live-audit-2026-11-11/source-records.json`.
It used the live snapshot from 19:23:23–19:27:05 UTC on 11 November 2026.
That snapshot covers 897 manuscripts and 1,692 total entities.
The exact evidence resides in `state/gila-independent-samples-2026-11-11/mixed-work-evidence.json`.
The selected claims reside in `state/gila-independent-samples-2026-11-11/mixed-work-selected.json`.
The later full upload requires a fresh receipt before any final action.

## What the source records establish

| Source record | Exact author evidence | Other evidence |
|---|---|---|
| [990001123390205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001123390205171) | `100$a=אברהם`; `100$c=הלוי, ממרוקו` | `500$a` explicitly says the copy derives from Abraham the Levite's notebooks. The source gives 1695, six folios, JTS `Ms. 6209`, and a provisional record note. |
| [990001915930205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001915930205171) | `100$a=קנטריני, יצחק חיים`; `100$d=1644-1723` | The source gives 1675 and Kaufmann `Ms. A 315`. Source 500 specifies Italian in Latin letters. It also has a provisional record note. |

Both records have `245$a=דרושים`.
That generic title does not establish a shared work identity.
The explicit author fields and the Abraham attribution support separate work groups.
The export does not identify Abraham through a verified person QID.
No automatic person match is necessary for that separation.

## Current entity targets in the corpus

| Manuscript | Source record | Current `P1574` target |
|---|---|---|
| [Q141747349](https://www.wikidata.org/wiki/Q141747349) | 990001123390205171 | [Q141990285](https://www.wikidata.org/w/index.php?title=Q141990285&oldid=2564458584) |
| [Q141175895](https://www.wikidata.org/wiki/Q141175895) | 990001915930205171 | [Q141755239](https://www.wikidata.org/w/index.php?title=Q141755239&oldid=2567875209) |

None of the 897 manuscripts links to Q141175543.
No main claim among all 1,692 snapshot entities points to Q141175543.
This result does not prove the absence of links elsewhere on Wikidata.

Q141990285 has the title `דרושים` and the author text `אברהם`.
Every reference on its three claims cites source record 990001123390205171.
Its English description cites that same record.
It already represents the source-defined Abraham work in this corpus.

Q141755239 has the title `דרושים`, Cantarini author text, and `P50=Q6076130`.
Its source references cite 990001915930205171.
It already represents the source-defined Cantarini work in this corpus.

## What remains wrong on Q141175543

The old work remains at revision 2558074052 from 17 October 2026.
Its English description still cites the Cantarini record.
Its title and class have references from both source records.
Its two author claims retain different source records.

| Claim | Value | Source record |
|---|---|---|
| `Q141175543$28694B77-BE65-4E3B-A39D-49702D4CF9E7` | `קנטריני, יצחק חיים` | 990001915930205171 |
| `Q141175543$BF5C795A-A21C-42F6-99FC-727FD21CBF5A` | `אברהם` | 990001123390205171 |

The earliest reference date within these claims is 11 August 2026 for Cantarini.
The Abraham reference date is 11 October 2026.
Reference dates do not prove the complete entity history.
They do not support an automatic decision to repurpose the old item as Abraham.

## Recommendation

Keep Q141747349 linked to Q141990285.
Keep Q141175895 linked to Q141755239.
Keep Abraham and Cantarini in separate work groups.
Do not create another work for either source record.
Do not change the manuscripts merely to reuse the older mixed item.

The history check below supplies the evidence absent from the two MARC records alone.
The old item must not become another Abraham entity.
The two Cantarini items require source cleanup before a merge.
Exact reference deduplication remains a separate safe operation under the normal ownership guard.

## Completed history check

The audit fetched the first revision of all three items through the Action API.
It also fetched the complete revision list for Q141175543 and the revisions around the identity error.
The API evidence resides in `state/gila-independent-samples-2026-11-11/history/`.

| Entity | Creation revision and date, UTC | Original identity |
|---|---|---|
| [Q141175543](https://www.wikidata.org/w/index.php?title=Q141175543&oldid=2536216742) | 2536216742; 25 August 2026, 13:20:43 | Cantarini only. All three claims cite 990001915930205171. The description cites that same record. |
| [Q141755239](https://www.wikidata.org/w/index.php?title=Q141755239&oldid=2557950411) | 2557950411; 17 October 2026, 10:23:23 | Cantarini only. Its original label names manuscript 990001915930205171. Its author claim cites that record. |
| [Q141990285](https://www.wikidata.org/w/index.php?title=Q141990285&oldid=2564458584) | 2564458584; 3 November 2026, 22:57:16 | Abraham only. All three claims cite 990001123390205171. |

Q141175543 still has only Cantarini at revision 2543344604 from 10 September 2026.
Revision 2557950502 adds Abraham on 17 October 2026 at 10:23:37 UTC.
The new Cantarini duplicate already exists fourteen seconds before that change.
The error therefore combines a duplicate creation with an incorrect same-title update.
The later revision 2558074052 retains both authors.

The two Cantarini creation revisions identify the same source-defined work.
This identity conclusion uses the source record and author, rather than the generic title.
The history does not support an Abraham identity for the old item.

## Completed backlink and external identifier check

The audit read current sitelinks and all backlink pages for each entity at 19:48 UTC.
The Action API used `bllimit=500` and followed continuation to completion.

| Entity | Sitelinks | Current Wikidata backlinks |
|---|---:|---|
| Q141175543 | 0 | None |
| Q141755239 | 0 | Q141175895 only |
| Q141990285 | 0 | Q141747349 only |

No backlink comes from a page outside the local corpus.
The backlink result covers Wikidata pages, not all websites on the Internet.
No external identifier claim on the old item establishes a different identity.

Q141755239 has `P2888` to [HMO Q3398](https://mhm-hmo.wikibase.cloud/wiki/Item:Q3398).
The audit fetched that HMO item at revision 71292.
Its label names manuscript 990001915930205171.
Its source text names Cantarini.
Its ontology URI also contains the Cantarini name and the same manuscript identifier.
This external correspondence supports the current Cantarini target Q141755239.
It supplies no support for an Abraham merge.

## Supported cleanup and recipient

The preferred recipient is Q141755239.
It has the active manuscript link, the verified author link, and the HMO correspondence.
Q141175543 has no current backlink and only the older source evidence.
Wikidata's [merge help](https://www.wikidata.org/wiki/Help:Merge#Select_recipient_item) usually prefers the more-used item.
It uses the lower identifier as a fallback when the recipient remains uncertain.
The current use therefore supports Q141755239 despite its later creation date.

Before a merge, remove the Abraham author claim from Q141175543.
Remove Abraham-only source references from its shared title and class claims.
Preserve those valid Abraham claims and references on Q141990285.
Preserve all distinct Cantarini references when the two Cantarini items combine.
Retain Q141175543 as a redirect to Q141755239 after the merge.
Do not merge either Cantarini item with Q141990285.

The evidence now supports that cleanup and target choice without a guessed author identity.
It does not authorize an unguarded merge during the active upload.
The operator must use fresh revisions and the repository's write controls after the upload ends.
This audit did not execute any cleanup or merge.

## Exact cleanup proposal

The audit fetched Q141175543 again before the proposal.
The entity still has revision 2558074052.
The proposal resides in `state/gila-independent-samples-2026-11-11/mixed-work-cleanup-proposal.json`.
It includes each complete before and after claim, every reference block, and the expected complete entity.
It also binds the proposal to the exact revision and entity digest.

| Operation | Count |
|---|---:|
| Claims before cleanup | 4 |
| Reference blocks before cleanup | 15 |
| Abraham author claims to remove | 1 |
| Abraham-only reference blocks to remove, including the removed author's references | 6 |
| Exact duplicate Cantarini blocks to remove | 3 |
| Claims after cleanup | 3 |
| Cantarini reference blocks after cleanup | 6 |
| Ambiguous claims or reference blocks | 0 |

The six Abraham blocks consist of two blocks on the class, two on the title, and two on the Abraham author.
Each block has the Abraham record in both `P3959` and its source URL.
Each retained Cantarini block has the Cantarini record in both fields.
No block combines the two source records.
No block lacks clear source identity.

The proposal preserves two distinct Cantarini blocks on each retained claim.
Their retrieval dates are 11 August and 6 September 2026.
The proposal preserves every distinct source snak, language, rank, qualifier, and main value on those claims.
It removes only whole existing blocks and exact duplicates.
It does not infer a boundary within a flat reference list.
The evidence therefore satisfies the source boundary requirement of W-304.

The proposal remains non-executable.
The generic repair validator does not implement its source-partition change kinds.
An exact write still requires the normal ownership check, a fresh revision check, and a complete readback receipt.
The later merge remains a separate operation.

## Later verified execution

The authorized operator completed a separate guarded source partition and merge.
The final public read confirms the Cantarini target Q141755239 and redirect from Q141175543.
The Abraham work remains separate.
The [final audit](gila-live-update-recheck-2026-11-11.md) records the exact revisions.
