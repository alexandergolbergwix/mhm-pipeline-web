# Gila audit: blocked records and exact source proposals

This audit makes no live edits. The packet contains proposals for the normal curator and guarded public APIs.
The audit does not treat a missing native author as proof that the MARC source has no author.

The original audit has 411 blocked rows: 341 work reviews, 51 author conflicts, 12 person conflicts, and seven lookup failures.
The parent task owns the seven lookup retries. This audit does not repeat those retries.
The native export has 2409 items. The original source file has 897 MARC records.

The supported packet is `state/gila-independent-samples-2026-11-11/blocked/consolidated-supported-repairs-frozen-2026-11-12.json`.
Its SHA256 is `fd59b1737f016730aac36d516f4e9573fc4fcf1b25e7a91485f0715b84475b16`.
The complete matrix is `state/gila-independent-samples-2026-11-11/blocked/classification-matrix.csv`.
The packet records the exact source CN, native item, public revision, source block, request, and guard conditions.

| Group | Result |
|---|---|
| 51 author conflicts | Five quote repairs, 14 native P50 conversions, 24 public source-heading completions, and eight true source or target conflicts |
| 12 person conflicts | Four authority-proven English labels, six wrong target exceptions, and two rejected modern authority matches |
| 341 work reviews | Nine existing target cases, 38 unverified global targets, one translation boundary, and 293 rows without a native author |
| 293 rows without a native author | 27 source-proven author cases, 21 explicit source decisions, and 245 unresolved cases |

The frozen `native_patch_proposals[]` array has 47 rows: 26 initial repairs, 19 new author additions, and two bad-ID suppressions.
The two suppressed historical people must stay outside live upload.
The parent confirms that all 24 public source-heading repairs have VERIFIED receipts.
The original 26 native PATCH proposals remain unchanged in the supported array.
They contain five quote repairs, 17 P50 conversions, and four English labels.
The 17 conversions include three proven global works from the work-review group.

The source review adds 27 native author proposals: two P50 additions and 25 literal P2093 additions.
Eight additions require a separate duplicate or alias decision. Those eight rows must stay outside live upload until that decision succeeds.
The other 19 additions retain the normal source, identity, duplicate, ownership, and readback guards.
The 15 new public scope proposals and two Ephraim proposals remain separate from the native PATCH proposals.

## Source coverage and limits

The deterministic scan covers all 293 rows and all 329 referenced MARC500/505 fields.
The first scan selects 41 author passages across 38 rows. The audit manually checks every selected passage.
A second pass checks nine further rows with author prepositions. A MARC740 alias adds one conclusive follow-up.
The manual review therefore covers 48 distinct rows. The packet preserves all 21 unresolved source decisions.

The scan uses title text and author markers. Different words or a different title can escape that method.
The audit does not establish author absence for the remaining 245 rows. Those rows retain the source-review guard.
The file `source500-505-author-scan.json` records the method and every source context that it selects.

## Exact component authors

The live title read ends at 2026-11-11T22:41:02 UTC. The later evidence files record their own UTC times.
The 2103-entity corpus snapshot omits most orphan work items. The separate API read includes those historical targets.
The final readback must include the orphan targets as well as the corpus links.

Both Ephraim poems have explicit author evidence in MARC500.
CN990001922600205171 names Ephraim ben Isaac of Regensburg for the preliminary poem `אם אפס רובע הקן`.
CN990001929740205171 names Ephraim of Regensburg for that poem and `בשורי מענית קצי פדותי`.
Its MARC700 supplies the full author name and approximate life dates.
[Q141990376](https://www.wikidata.org/wiki/Q141990376) and [Q141990252](https://www.wikidata.org/wiki/Q141990252) both carry the wrong Kimhi author.
The exact proposal replaces those claims with P50=[Q1296489](https://www.wikidata.org/wiki/Q1296489) and source P1932.

The other exact repairs replace a primary work author that the old upload transferred to a separate component.
The packet preserves a literal source name when the evidence does not prove one person QID.
For example, the Karaite poet remains `אברהם`. The packet does not select a person from that partial name.
The medical excerpt on [Q141990365](https://www.wikidata.org/wiki/Q141990365) belongs to Maimonides; its translator remains anonymous.
The packet also removes its wrong Tzahalon English label. It does not invent an English title.

The packet removes the inherited Najara claim from the explicitly unsigned poem [Q141990807](https://www.wikidata.org/wiki/Q141990807).
It removes the inherited Troki claim from [Q141991982](https://www.wikidata.org/wiki/Q141991982).
The latter source gives uncertain Hai Gaon or Solomon Molcho attributions. The proposal does not convert either attribution to a certain author.

The audit preserves the father name for Isaac son of Moses Luria. It does not substitute Isaac Luria, the Ari.
The audit also separates a poem recipient, a source author, a translator, an abridger, a scribe, and a commentator.
No individual folio interval comes from a broad group interval.

## Same-work evidence and the Keter partition

The frozen field supplement is `source-proven-alias-and-partition-frozen-v2-2026-11-12.json`.
Its SHA256 is `162b8a8fe84aeb2452a9f83e6b2aa8629b8dbe2352832b36e036c68b4b786ce5`.
It contains seven merge candidates across six source identity cases. None permits a blind merge.
The file includes original creation contents, current entities, sitelinks, and complete item backlinks.
The creation records confirm that the own-created donors already represent these source identities.

| Proposed donor | Proposed recipient | Source proof |
|---|---|---|
| Q141990323, Q141990843 | Q141755369 | CN990001190680205171: named Aderet Haazinu, Jacob Tzemah, and both MARC740 forms |
| Q141990253 | Q16132754 | CN990001206550205171 explicitly identifies Geder Peretz as the original title of Minhat Shai |
| Q141990832 | Q141990791 | CN990038692590205171 gives Sefer haTzivui and its exact incipit on 1b–2b |
| Q141990951 | Q18387842 | Two records name Solomon ibn Gabirol for Keter Malkhut; the recipient has the same author |
| Q141991813 | Q141990204 | Two records name Radbaz or David ben Zimra and repeat `אתה הוא יסוד היסודות` |
| Q141991869 | Q141991868 | CN990001375760205171 gives a heading and its incipit for one astronomical component on 1a–3b |

Only Q16132754 and Q18387842 are foreign recipients. Their ordinary acceptance and ownership requirements remain active.
The proposals never use `ignoreconflicts`. They preserve every distinct source block on an allowed claim.
They require a fresh exact comparison, a metadata check for the redirect, and complete readback.

The star case proves one component. It does not establish a new title or first line for the whole parent book.
The supplement prohibits a title or first-line transfer to Tzurat Haaretz or Cheshbon Mahalkhot haKokhavim.
The incipit on Q141991869 and the long Sefer haTzivui incipit must not become automatic work aliases.
The catalogue description on Q141990843 must not become a work title. Its author name in P1680 is not a subtitle.
The author-bearing Gabirol title must not move as one formal title string.

The supplement lists every allowed and excluded donor claim by exact claim ID.
It also lists excluded metadata and two recipient fields that need source review before a merge.
It archives occurrence text with the source CN, manuscript QID, and exact folio when the source supplies one.
Five native title repairs remain separate from the supported 47-row array. All five require the source identity decision first.

The native `work:כתר_מלכות` item merges five records with two different authors.
CN990000633940205171 and CN990034232700205171 identify the Radbaz work and its incipit.
CN990000649970205171 and CN990035802410205171 identify the Solomon ibn Gabirol work.
CN990000943210205171 gives only the common title. Its author remains unresolved.
A single author override for that native item would preserve the source conflation. The packet prohibits that override.

The Aderet primary native row also pools the Lonzano anthology CN990027840460205171.
The exact Jacob Tzemah identity proof covers CN990001190680205171. It does not prove the identity of the complete two-record pool.

## Person safeguards

The independent Latin evidence supports four English labels. The exact source inscription supports `Joseph Sanger`.
The audit withdraws the proposed English imports for historical Simcha and Netanel Segri.
The modern VIAF headings do not prove those historical source identities.

Both bad AuthorityMatch rows already have `approved=False`. No unapprove request is necessary.
The native P8189 claims survive through stale evidence. The two exact suppression proposals remain separate from the 26 base proposals.
The historical source people must stay outside live upload until a supported source identity flow resolves them.

The separate report `gila-historical-modern-authority-partition-2026-11-12.md` gives the complete source partition.
Its public proposals preserve the modern community identities, P1889, and the deprecated VIAF claim.
The proposals remove the false historical project links and source-derived historical facts.
No current manuscript or work claim points to these two QIDs in the complete after snapshot or the native export.

Six other person rows have proven correct authority targets but wrong global keys.
The normal reconcile path cannot replace those keys. The ItemOverridePayload has no target field.
The packet preserves these six cases as explicit exceptions. It does not invent a target PATCH or change a ledger.
The exact role audit finds no source-proven current manuscript claim that needs a retarget to those six people.

## Execution conditions

The parent task must read each persisted override before any native PATCH.
The controller must retain previous per-index fields. It must union the existing `add_statements` and `remove_statements` arrays.
An export does not expose those persisted override rows. The packet therefore records their expected state as unknown.
The controller must stop if the exact native item, public revision, source block, or expected target changes.

The new source-author proposals require the specified public correction before a normal live upload.
A supported versioned source flow must resolve every duplicate-dependent native key.
No direct global key change, target override, ownership bypass, or title-only target choice is proposed.
The audit makes no claim that these proposals already changed Wikidata.

## Later execution by the authorized operator

The independent audit above made no live writes.
The parent operator later completed 19 public source repairs with full readback.
The operator also completed 24 source-exact author name repairs.
The versioned API retained 123 source edits and six additional manuscript occurrence edits.
The source edits preserve previous override arrays and fields.
The offline build preserves all unaffected source facts and excludes exactly the two false historical person matches.

Six source-proven duplicate work records now redirect to five verified recipients.
The merge receipts preserve every permitted claim and complete source block.
The operator archived the excluded incipits, catalogue terms, and false subtitle.
Those excluded fields did not become whole-work titles, aliases, or first lines.
The Lonzano source remains outside the proven Tzemah identity scope.
The unsigned Keter Malkhut source remains unassigned.
All eight dependent native work rows remain deferred.
The current native target cannot supply a valid approval for the separate Gabirol recipient.
That public merge remains deferred; the source-proven manuscript relation can use the correct existing work without a recipient write.

Evidence: `state/gila-recheck-2026-11-11/source-alias-receipts.json` and `gabirol-merge-deferred.json`.
The main live audit records the later bulk jobs and final public checks.
