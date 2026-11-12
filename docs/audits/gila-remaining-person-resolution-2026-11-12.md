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
The dedicated identity-choice endpoint stores the exact source proof in `person_identity_choice`.
The [choice contract](../architecture/blocks/wikidata-studio/person-identity-choice.md) describes its guards.
A run-local choice must bind the actor, current native target, source fingerprint, exact authority ID, and source scope.
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

## Smallest source-preserving proposal

This section proposes code behavior; it does not record a live correction.
The proposal needs no new database column.
The current HMO override stores the claim choice and the project event stores the curator action.
The normal HMO upload and read-back must preserve that choice before the canonical rebuild.

The ontology declares `hm:external_identifier_nli` and the live schema exposes P82 as a string.
The ontology distinguishes manuscript control numbers from `hm:mazal_id` authority identifiers.
The live schema exposes P276 as a string and P84 as a URL.
A fresh property search finds P296, `Mazal / NLI authority ID`, with datatype `external-id`.
P296 has no live ontology-source claim; the normal schema map must confirm its ontology URI before use.
The ontology declares no `hm:control_number` property and no source qualifier contract for identity choices.
The mapper's `control_number` alias does not declare a schema property.

Declare a narrow contract for P276 before the source repair.
Each active P276 claim must hold one public QID and one or more P82 source control qualifiers.
Each qualifier must contain an exact 990 control number from the current run and the reviewed source packet.
The current source must attest the exact person name within that scope.
Saadia permits only CN990001028160205171 and CN990035802410205171.
CN990001147460205171 remains outside that scope because its patronymic reverses the names.
Each other person permits only the one source record in the proposal.

The normalizer must bind the live HMO revision, claim identity, canonical fingerprint, public QID, and active public identifier agreement.
It must reject multiple active target choices, missing source qualifiers, unknown source records, stale proof, and conflicting public identifiers.
It must require the current item approval and preserve the exact scope in the native item.
Current Wikidata item approval is true on all six rows; HMO item approval remains unset.
An old item approval alone does not establish a fresh identity choice.
The normalizer must derive the new proof from the current live claim, not the old cached authority-evidence array.
The resulting proof permits reference-only use of the public target.
It does not permit a foreign public-person update.

The current ResolvedClaim class drops qualifiers, rank, and reference blocks.
The live read-back already preserves these fields.
The upload path must preserve the same fields and fail closed on unsupported snaks.
The bulk HMO upload must apply the same current claim overrides as the single-item path.
The bulk read-back must derive current authority evidence from the actual live identity claims.
The existing identifier-slot repair alone cannot supply an explicit public QID choice.
The existing P276 claim alone cannot safely extend a choice to every pooled source record.

The six live HMO items hold twelve false VIAF claims.
Each item has its 987 NLI authority identifier in P272 and a matching false VIAF URL in P290.
The normal HMO merge retains claims absent from the new payload.
An override that omits these values therefore does not remove them from the live item.
The repair needs exact claim removal with the saved GUID, expected value, and current revision guards.
It must preserve other values and manual claims on those properties.
Use the confirmed authority property for each 987 value and P82 qualifiers only for the 990 source scopes.
If the operator uses P84, its URL must agree with the selected P276 QID.
The public URL mapper currently omits that Wikidata URL; preserve this behavior to prevent a public self-link.

The canonical choice must take precedence over a stale global ledger association.
The six source choices must not overwrite the old global keys.
The existing public upload outcome path can rewrite a global ledger row even for a skipped person.
Exclude these reference-only person rows from public upload scope.
The current corpus has zero incoming local references to all six person rows.
Do not add a manuscript role or force a public upload for these rows.

## Normal scoped repair sequence

1. Implement the claim fidelity, bulk override, live-proof, source-scope, and ledger precedence guards.
2. Run the offline boundary fixtures before any HMO edit.
3. Recheck the six HMO revisions, twelve false claim GUIDs, public identifiers, and source scopes.
4. Save a versioned HMO override for each reviewed person and exact source scope.
5. Remove only the twelve proved false VIAF claims through the guarded HMO operation.
6. Upload the six HMO items through the normal scoped job and read back each live item.
7. Rebuild the canonical Wikidata items from the fresh HMO snapshots.
8. Verify the six target QIDs, exact source scopes, reference-only policy, and unchanged global keys.
9. Verify that the rebuild adds no manuscript role or public self-link.

The exact six-case fixture is `state/gila-resolution-2026-11-12/hmo-identity-boundary-red-fixture.json`.
Its synthetic after-state is not a live claim or an approval.
The first offline baseline has eighteen failed checks across read-back identity, identifier normalization, and stale ledger precedence.
The separate qualifier fixture also fails on the current ResolvedClaim round trip.
Its script is `state/gila-resolution-2026-11-12/reproduce-hmo-qualified-choice-loss.py`.
These failures identify the required code boundaries before the source repair.
