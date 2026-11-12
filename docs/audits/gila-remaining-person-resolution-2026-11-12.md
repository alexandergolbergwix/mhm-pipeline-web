# Gila: remaining person identities and the P31 decision

Date: 12 November 2026.
This report contains read-only research.
The research made no live edit, approval, override, ledger change, or job request.

Five person rows have one proven source record and one exact existing target.
A sixth person row needs a source partition before a target choice.
The two false historical matches remain safely outside the upload scope.
The P31 difference reflects the accepted project model, not a failed upload.

## Six existing person targets

The official Wikidata API confirms each correct target's exact active P8189.
A fresh exact-identifier search returns only that target for each of the six identifiers.
Every wrong target has a different active P8189.

| Native item | Wrong target | Correct target | Exact P8189 | Source role and scope |
|---|---|---|---|---|
| `QDraft_Person_1014` | Q141748465 | [Q141384820](https://www.wikidata.org/wiki/Q141384820) | 987007507328605171 | Saadia ben Solomon: scribe in CN1028160; former owner in CN3580241. Partition first. |
| `QDraft_Person_138` | Q141175602 | [Q141748474](https://www.wikidata.org/wiki/Q141748474) | 987007298284205171 | Yahya ben David al-Hamdi: former owner in CN1875220, MARC 700 and 561. |
| `QDraft_Person_368` | Q141175703 | [Q141749919](https://www.wikidata.org/wiki/Q141749919) | 987007306870005171 | Gedaliah Samuel ben Abraham: former owner in CN825080, MARC 700 and 561. |
| `QDraft_Person_373` | Q141749919 | [Q141175706](https://www.wikidata.org/wiki/Q141175706) | 987007400881005171 | Meir Gorvato: subject of a eulogy in CN776020, MARC 600 and 500. |
| `QDraft_Person_87` | Q141175725 | [Q141175593](https://www.wikidata.org/wiki/Q141175593) | 987007528060905171 | Yeshua Azulai: scribe in CN776020, MARC 700 and 500. |
| `QDraft_Person_96` | Q141175731 | [Q5515611](https://www.wikidata.org/wiki/Q5515611) | 987007578812705171 | Gabriel Esperanssa: signatory in CN633490, MARC 700. |

The short CN labels above refer to full control numbers in the machine-readable proposals.
The JSON also retains source URLs, record hashes, native fingerprints, target revisions, and target digests.
The source scope does not authorize an inferred author, owner, or scribe relation.
The normal role mapper must preserve the exact source role.

[CN990001875220205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001875220205171) names Yahya among the former owners.
[CN990000825080205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000825080205171) names Gedaliah in the ownership record.
[CN990000633490205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000633490205171) identifies Gabriel as a signatory.

The earlier source audit missed Meir because it searched only MARC 100 and 700.
[CN990000776020205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000776020205171) has the controlled subject `גורבתו, מאיר` in 600.
Its 500 note names `מאיר גורבאתו` among the eulogy subjects, under the section ראה.
The same record explicitly identifies Yeshua Azulai as the scribe.

## Saadia requires a source partition

The current Saadia item carries 287 record IDs.
That list does not prove 287 independent attestations of the same person.
[CN990001028160205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001028160205171) names `סעדיה בן שלמה` as the scribe.
[CN990035802410205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990035802410205171) names that person as a former owner.
Its provenance distinguishes the father `שלמה בן סעדיה` from his son `סעדיה`.
CN990001147460205171 names `שלמה בן סעדיה`; it does not prove Saadia's identity.
The proposal therefore includes only the two exact source attestations.
It does not reverse a patronymic or approve the other pooled records.

## Versioned target choice

The current ItemOverridePayload has no target field.
An ordinary override cannot safely replace the six old global associations.
A new run-local choice must bind the actor, current native target, source fingerprint, exact authority ID, and source scope.
It must also bind the public target revision and complete entity digest.
A source or target change must invalidate the choice.
The event must precede the read-model change.
The implementation must leave shared global keys unchanged.

Default to reference-only use of the proven target.
Require separate exact-QID approval before any foreign public update.
Keep the normal ownership, bot exclusion, duplicate, identity, and source guards.
An unapproved legacy match must not become positive proof through this choice.
The flattened source fields do not contain these NLI authority identifiers directly.
The operator must therefore recheck the current canonical identity and item curation before any actual choice.
Legacy AuthorityMatch rows default to approved=False and have no explicit rejection field.
That value does not prove a curator rejection or current canonical disapproval.

The complete saved corpus has no manuscript or work mainsnak that targets these six wrong QIDs.
The native export has no manuscript or work statement that targets these six person rows.
A corrected person target therefore does not justify an automatic manuscript role edit.

## Two false historical matches

The fresh read confirms that modern Q141175661 and Q141175604 have no historical HMO exact-match link.
The current native export excludes `QDraft_Person_1331` and `QDraft_Person_975`.
Do not infer explicit rejection from the legacy approved=False values.
Keep the false historical identifiers outside the current source until new identity proof exists.
The modern identity cleanup is complete; the historical replacement identities remain unproven.
The [earlier partition report](gila-historical-modern-authority-partition-2026-11-12.md) records the source distinction and verified revisions.

An official NLI [Ofar Yaakov record](https://www.nli.org.il/he/manuscripts/NNL_ALEPH990001352370205171/NLI) names `סגרי, נתנאל בן אהרן יעקב`.
The 1622 poem record gives only `סגרי, נתנאל`.
The fuller heading alone does not establish that the poet and the rabbi are the same person.
Four official Wikidata name searches returned no exact historical candidate.
NLI direct pages returned HTTP 403 during this research; indexed catalogue evidence is insufficient for a new identity choice.

## P31 is an accepted model decision

The project records the user's separate physical/work choice on 5 November 2026 in [W-296](../architecture/rules/wikidata-studio.md).
The [accepted-model page](../architecture/blocks/wikidata-studio/gila-audit-completion.md) explicitly preserves that decision.
The official [WikiProject Manuscripts model](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model) assigns physical classes to manuscripts.
It uses P1574 to identify contained written works, with section scope where applicable.
It does not require written-work P31 on every physical manuscript.

The current source has Q87167 on all 897 manuscripts and Q123172729 on 762 manuscripts.
It also has 12 Q33308141 values, 13 Q720106 values, and 9 Q30103158 values.
All 1,406 work rows have Q47461344.
The implementation must not add written-work P31 to manuscripts merely to change an upload status.
The response to Gila must record this deliberate model variance.

The private evidence packet is `state/gila-resolution-2026-11-12/person-proposals.json`.
The packet contains six conditional target choices and two historical safeguards.
It contains no credential or executable live mutation.

## Current canonical proof contract

HMO live read-back supplies HmoCanonicalEntity snapshots and their source fingerprints.
The canonical provenance records `canonical_source=wikibase` and the source control numbers.
The current authority normalizer marks `accepted=True` only after identifier and ambiguity checks.
That flag does not record curator approval.
HmoStudioItemOverride and WikidataItemOverride store separate nullable item approval flags.
A false item approval explicitly rejects the item; a false legacy AuthorityMatch approval does not have that meaning.

The supported HMO curator route is `PATCH /runs/{run_id}/hmo-studio/items/{local_id}/override`.
It applies source claim edits or item approval and records a project event.
A normal HMO upload and full read-back then replace the canonical snapshot.
The current source fingerprint must bind any later public target choice.
Existing item approval rows do not themselves store that fingerprint.

A fresh canonical identifier, exact public identifier agreement, source-specific name evidence, and explicit current item approval can support a conditional identity proof.
The implementation must reject conflicting source scopes, public identifiers, explicit item rejection, and stale fingerprints.
It must not infer fresh identity consent from an old general item approval alone.

The code paths are `hmo_item_upload.py:563`, `hmo_item_upload.py:623`, `hmo_canonical.py:236`, and `authority/evidence.py:143`.
The read-only query is `state/gila-resolution-2026-11-12/check-canonical-person-evidence.py`.
The failed legacy-proof query establishes no current canonical rejection.

## Current read-only result

The production query confirms current Wikidata item approval on all six person rows.
Each row has a live canonical source, a matching snapshot fingerprint, and the expected mapped P8189.
The HMO item approval flags are pending.
The canonical authority-evidence arrays are empty, but the direct canonical identifier claims exist.
The absence of an authority-evidence array therefore does not establish identity rejection.
Saadia's canonical snapshot has 285 record IDs; the native merge has 287.
The proposal still restricts the source scope to the two exact MARC attestations.

## Vital requires a new source-curator choice

Canonical `QDraft_Person_478` has the label `חיים בן יוסף ויטל`.
It has no mapped authority identifier and no current HMO or Wikidata item approval.
A fresh [Q2302424](https://www.wikidata.org/wiki/Q2302424) read confirms P8189 `987007269661905171` and P214 `34704971`.
The primary [NLI catalogue](https://www.nli.org.il/en/manuscripts/NNL_YBZ990037511990205171/NLI) publishes that exact authority identifier with the full Hebrew heading.
Thirty-one source records in this run have the exact MARC 100 author heading.
This primary evidence supports a new source-curator identity choice through the normal HMO route.
It does not establish an old approval or authorize a direct public-person write.
The operator must upload and read back the HMO source before the canonical Wikidata rebuild.
The bounded proposal is `state/gila-resolution-2026-11-12/vital-canonical-identity-proposal.json`.
