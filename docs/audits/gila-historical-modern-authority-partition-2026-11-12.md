# Historical source people and modern authority records

Two rejected authority rows still supplied identifiers to the current canonical items.
The public entities combine modern authority identities with historical project data.
The safe correction keeps each modern public identity and removes the false historical source assertions.
The packet withdraws both proposed English imports.
This audit made no live edits.

## Exact rejected rows

| Source person | Source record | AuthorityMatch row | Current approval |
|---|---|---|---|
| שמחה בן יוסף | 990000864590205171 | `89ca4fee-fb19-4d85-81b8-6fcc75d51040` | `False` |
| סגרי, נתנאל | 990001748970205171 | `fd58075d-1e8a-40fb-af73-34fd50b5f52e` | `False` |

The read-only production receipt is `state/gila-recheck-2026-11-11/two-bad-authority-match-rows.json`.
No unapprove request is necessary.
The current native identifiers therefore represent stale canonical or ledger evidence.
Do not reapprove either row.
Do not treat agreement with those stale identifiers as independent identity proof.

## Simcha: the historical source differs from the modern author

Source [990000864590205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000864590205171) names Simcha ben Yosef in a household visit in 561.
The catalogue explicitly identifies the visiting Jacob as the manuscript's scribe: `[=המעתיק]`.
Source 957 identifies that scribe and his copy completion in 1651.
The visit's year remains unclear.
The audit derives no precise visit year or lifetime for Simcha.
The scribe identification supplies the historical event context; the manuscript's date alone does not supply it.

[VIAF 22398077](https://viaf.org/viaf/22398077) identifies Simcha H. Benyosef, born in 1950.
Its NLI source is `987007258417705171`.
Its controlled name and occupations identify a modern author and translator.
Those data do not identify the person in the historical household visit.

[Q141175661](https://www.wikidata.org/w/index.php?title=Q141175661&oldid=2547971742) was already a hybrid at its creation.
Creation revision 2536219362 has the modern authority identifier and English name beside the project source identity.
Later authority tools added further modern identifiers.
The history does not establish an original clean historical entity that we can reclaim without a new identity decision.

[HMO Q1164](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1164) explicitly describes the person mentioned in source record 990000864590205171.
Its exact-match link from the modern public person is therefore false.
The public proposal removes that `P2888` claim.
It replaces the generic manuscript description with the independently supported modern description: `author and translator born in 1950`.
It preserves the modern name, authority identifiers, and valid community facts.

## Segri: the 1622 poet differs from the modern researcher

Source [990001748970205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001748970205171) names `סגרי, נתנאל` as the author.
Its poems address members of the House of Savoy.
Source 957 explicitly gives `Torino ... Maggio 1622`.
The source also states that the poems use Hebrew and Italian.

[VIAF 308274310](https://viaf.org/viaf/308274310) gives the NLI-controlled name `סגל, נתנאל` and the Latin form `Segal, Netanel`.
Its NLI identifier is `987007521014805171`.
Its NLI bibliography concerns a modern study of pomegranate storage disease.
The cluster also includes a film worker, [Q95893544](https://www.wikidata.org/wiki/Q95893544).
That cluster therefore cannot identify the poet from the shared given name.

[Q141175604](https://www.wikidata.org/w/index.php?title=Q141175604&oldid=2546470049) was also a hybrid at creation, revision 2536217593.
A community editor later distinguished the researcher from the film worker through `P1889`.
The editor also deprecated the VIAF claim with the reason `conflation`.
The cleanup must preserve both decisions.

[HMO Q1084](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1084) explicitly describes the author in the 1622 manuscript record.
The proposed public cleanup removes that false `P2888` relation.
It corrects the modern person's Hebrew label and native name to `נתנאל סגל`.
It removes only the Segri alias from the modern person.
It preserves the modern English name, NLI identifier, researcher description, occupations, `P1889`, and deprecated VIAF claim.

The proposal also removes the two person-language claims inherited from the historical poem record.
Those claims already occur in the creation revision.
The pre-fix author path copied manuscript languages to people.
Local commit `82710927` and Rule W-206 corrected that source-scope error on 28 August 2026.
The removal does not assert that the modern researcher cannot use either language.
It removes assertions whose source scope belongs to a different person.

## Current associations and native suppression

The fresh public backlink checks found no manuscript or work relation to either person.
The 2,103-entity read from 22:01–22:06 UTC also found no such corpus relation.
The only outside backlink is the valid reciprocal distinction from the film worker.
The current native export has no manuscript or work statement that targets either person.
No manuscript role needs a guessed replacement.

The native proposals suppress the two rejected `P8189` values.
They preserve the historical source names and complete source evidence.
The old modern targets remain explicit source exceptions until a supported versioned target correction exists.
No normal override field can silently replace `existing_qid`.
The normal source and identity guards must continue to defer an unsupported person link.

The exact public and native proposals reside in:

`state/gila-independent-samples-2026-11-11/blocked/latin-authority/historical-identities/false-historical-source-partition-proposals.json`

The complete before entities, creation revisions, histories, primary VIAF responses, and HMO source descriptions accompany that file.
The operator must use fresh revisions, the normal ownership checks, and complete public readback receipts.

## Verified live execution

The authorized operator completed both public source partitions.
Simcha H. Benyosef passed full public readback at revision `2568106807`.
Netanel Segal passed full public readback at revision `2568108525`.
Both modern people retain their independent authority identifiers and community facts.
Netanel Segal retains `P1889` and the deprecated VIAF claim with its conflation reason.
The operator removed the false historical HMO links.
The operator also corrected Netanel Segal's Hebrew name and removed the source-disproved historical poem languages.
Those language claims came from the original source projection; their removal does not assert language inability.

The versioned source overrides suppress both false historical `P8189` values.
The offline source check excludes exactly `QDraft_Person_1331` and `QDraft_Person_975`.
The two historical source people remain outside live upload.
The operator did not replace a global target or approve a rejected authority row.

One request used an invalid alias format and caused no public edit.
The fresh complete entity and page history proved that unchanged state before the corrected request.
A separate readback resolved the empty-array serialization difference on the Simcha repair without another write.
Evidence: `state/gila-recheck-2026-11-11/supported-public-receipts.json`.
