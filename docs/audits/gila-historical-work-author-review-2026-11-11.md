# Gila: historical work authors and source scope

Q141755231 and Q141755236 do not contain mixed authors.
Each has one author and one source record, both unchanged since creation.
The review found a separate source-scope defect on Q141990660.
It also found six duplicate author name statements that require only exact format cleanup.
This research made no live edits.

## The two requested work items

The API reads occurred at approximately 21:05–21:07 UTC on 11 November 2026.
Evidence resides in `state/gila-independent-samples-2026-11-11/multi-author/`.
The audit read the current claims, complete backlinks, and creation revisions.
It compared the original MARC fields and the current 2,409-item native export.

| Item | Current revision | Author and source | Creation evidence | Current backlink |
|---|---:|---|---|---|
| [Q141755231](https://www.wikidata.org/w/index.php?title=Q141755231&oldid=2568074970), ספר המספר | 2568074970 | `אבן עזרא, אברהם בן מאיר`; [990001255910205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001255910205171) | Revision 2557950372, 17 October 2026: the same title, author, and source | Q141747539 |
| [Q141755236](https://www.wikidata.org/w/index.php?title=Q141755236&oldid=2568075021), מרשה קהלת יעקב | 2568075021 | `צהלון, יעקב בן יצחק`; [990001240470205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001240470205171) | Revision 2557950399, 17 October 2026: the same title, author, and source | Q141747520 |

Source 100 assigns Ibn Ezra to Sefer ha-Mispar.
Source 500 repeats that title and author.
The additional mathematical passage on folios `36א–39ב` has an uncertain Ibn Ezra attribution.
That uncertainty does not make the main work a mixed-author entity.

Source 100 assigns Tzahalon to Morashah Kehilat Yaakov.
Source 500 explicitly names him in the manuscript's title statement.
It also lists embedded source texts and medical passages.
Those components do not make Maimonides a joint author of the complete Morashah work.

The current native export has source-matched drafts for these two main works.
It gives those drafts no `existing_qid`.
The corresponding live manuscripts already point to the two established work QIDs above.
The exact source, title, and author support reuse of those targets after the normal identity check.
Their absence from the current native map does not justify another entity creation.

No source partition proposal is necessary for either requested work.
Their earlier review status concerned repeated references, not multiple authors.

## Confirmed adjacent defect: Q141990660

The current item [Q141990660](https://www.wikidata.org/w/index.php?title=Q141990660&oldid=2568058596) has the Hebrew title `משנה תורה (ספר מדע)`.
Its `P2093` names Tzahalon.
Its English label instead names Morashah Kehilat Yaakov.
These data combine the encompassing work with a separate source text.

The creation revision 2564473627, from 3 November 2026, identifies the original local work as `work:משנה_תורה_(ספר_מדע)`.
It already contains the incorrectly inherited Tzahalon author and the wrong English label.
Before the current bulk upload, its Hebrew title instead names Pirkei Moshe.
The fresh read shows the original Mishneh Torah title again.
The three local targets below explain that identity instability.

| Local work identifier | Current native target |
|---|---|
| `work:משנה_תורה_(ספר_מדע)` | Q141990660 |
| `work:פרוש_משנה_תורה_(ספר_מדע)` | Q141990660 |
| `work:פרקי_משה_(מאמר_יז-כ)` | Q141990660 |

The source lists the three titles separately in 740.
It explicitly identifies the medical passage on `107א–113ב` as Maimonides' aphorisms.
It distinguishes those source texts from Tzahalon's encompassing work and its commentary.
The three source scopes must not share one public identity merely because they occur in one manuscript.
The commentary may overlap the main Morashah work; that relation requires an explicit source decision.
The audit does not assume three new independent literary works.

### Exact historical correction

The source-scope proposal resides in `multi-author/Q141990660-source-scope-cleanup-proposal.json` under the evidence directory.
It binds the change to revision 2568058596 and the complete entity digest.
It removes this inherited author claim:

`Q141990660$B34C1E17-75FD-4F76-8E10-37B3EC7FB19E`

The removed claim assigns `צהלון, יעקב בן יצחק` to the distinct Mishneh Torah source text.
Every reference block on that claim cites source record 990001240470205171.
No reference block has mixed or unclear source identity.
The proposal preserves the complete before claim and expected after entity.
It does not invent another author claim.
It remains non-executable and requires the normal ownership, revision, and readback checks.

### Established base-text target

The primary lookup found [Q15633724](https://www.wikidata.org/w/index.php?title=Q15633724&oldid=2297623687), Sefer Hamadda, Mishneh Torah.
Its Hebrew names include `ספר המדע` and `ספר מדע`.
It has `P361=Q201029`, Mishneh Torah, and `P50=Q127398`, Maimonides.
It also has three sitelinks.
This matches the base-text identity from the source and original creation revision.
Evidence resides in `multi-author/maimonides-established-targets.json`.

Use that established identity for the base text after the normal target check.
Resolve the commentary and Pirkei Moshe independently.
The historical Q141990660 requires the exact wrong-author cleanup before any later duplicate merge.
A write to the established foreign item still requires the repository's own-or-accept guard.
This audit did not perform a merge or modify any target.

## Other apparent multiple-author candidates

| Item or group | Classification | Required action |
|---|---|---|
| Q141754713, Q141754903, Q141754978 | One Isserles identity with doubled-quote and terminal-comma variants | Consolidate the equivalent `P2093` statements and preserve distinct references. Do not split the work. |
| Q141754963, Q141754981 | One David ben Abraham identity with doubled-quote variants | Consolidate the equivalent `P2093` statements and preserve distinct references. Do not split the work. |
| Q141755239 | One Cantarini identity, including two text variants after the merge | Consolidate only the equivalent text claims. Keep the verified `P50`. |
| Q141755237 | Cantarini `P50` plus the same author as source text | No source conflation. The two properties do not identify two persons. |
| Q42195430 | David Kimhi as a person QID and the text `דוד קמחי` | No source conflation. |
| Q205388, Zohar | Both author claims have deprecated rank and retain attribution qualifiers | They do not establish two active joint-author claims. This project has no source-partition basis for a repair. |
| Q191825, Mishnah | Judah ha-Nasi and the collective Tannaim | These are established community claims about the same corpus. The local source does not prove an incorrect project merge. |

The six exact format proposals reside in `multi-author/equivalent-author-formatting-proposals.json`.
Each pair matches the CSV-decoded source 100 after only quote collapse and terminal-comma removal.
Both claims have the same source record, normal rank, and no qualifier.
Each proposal retains a correctly formatted existing claim and every complete distinct reference block.
The proposals remove six redundant text claims across six entities.
They infer no reference boundary and create no new person or work identity.

The author count alone therefore cannot classify a work as mixed.
The audit checks the source identity, property, rank, qualifier, and author text before it proposes a correction.

## Later live repair receipt

The authorized operator later completed all seven exact author repairs.
It removed six redundant author text claims across six work entities.
It preserved every complete distinct reference block on the retained author claims.
It also removed the source-disproved Tzahalon author from Q141990660.
The operator checked current source, creator ownership, exact claims, and revisions before each request.
Each request used `baserevid`, no retry, and a complete semantic entity readback.

| QID | Verified revision |
|---|---:|
| Q141754713 | 2568079099 |
| Q141754903 | 2568079145 |
| Q141754963 | 2568079189 |
| Q141754978 | 2568079242 |
| Q141754981 | 2568079267 |
| Q141755239 | 2568079541 |
| Q141990660 | 2568079627 |

Evidence: `state/gila-recheck-2026-11-11/author-scope-receipts.json`.
The Q141990660 target conflict and incorrect English label still require a separate identity decision.
These exact author repairs do not close those two issues.

The later complete-name pass also repaired 24 catalogue author names.
It retained the earlier source form in `P1932` and preserved every complete source block.
Each full name matches the exact MARC `100$a` and `100$c` fields.
All 24 public results passed full entity readback.
Evidence: `state/gila-recheck-2026-11-11/complete-author-heading-receipts.json`.
The separate supported pass completed 17 further work-source repairs and two modern/historical person partitions.
Evidence: `state/gila-recheck-2026-11-11/supported-public-receipts.json`.
