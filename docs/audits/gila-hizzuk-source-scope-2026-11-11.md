# Hizzuk Emunah: exact source scope review

The source identifies a letter on folios `51א–52א` within the Hizzuk Emunah manuscript.
It also identifies Buchach through a person mentioned on folio `68ב`.
These passages do not establish the complete manuscript as a letter or place the manuscript in Buchach.
The audit made no live edits.

## Evidence

The source record is [990000885680205171](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000885680205171).
The audit used the original fields in the existing production export.
It compared the current 2,409-item canonical export.
The fresh Wikidata read at 20:18:32 UTC still returned [revision 2567905882](https://www.wikidata.org/w/index.php?title=Q141747170&oldid=2567905882).
The exact before and after proposal resides in `state/gila-independent-samples-2026-11-11/hizzuk-scope-cleanup-proposal.json`.
That proposal remains non-executable.

## Letter genre

Source 500 explicitly describes a copy of a letter on folios `51א–52א`.
The complete manuscript also contains the main Hizzuk Emunah work, a sermon, and other components.
Source 655 says `Personal correspondence`.
The genre therefore exists in the source; its scope is the defect.
Absence from the canonical manuscript proposal alone does not justify deletion.

The canonical export places `P136=Q133492` on the separate letter work.
It proposes a folio-qualified manuscript link to that work.
That work has no public QID in the exported proposal.

The exact cleanup proposal removes the unqualified manuscript genre claim.
It requires a verified public component claim or an explicit source-only disposition before that removal.
The operator must preserve every distinct reference from the old genre claim on the correct component.
The proposal does not invent a component target or assert that the active upload created one.

## Sender and recipient

The passage explicitly identifies the letter's sender as Elia Levita and its recipient as Sebastian Münster.
The catalogue also gives their names and roles in 700.
Fresh Wikidata labels and aliases support both existing person targets.
Q66031 includes `אליהו בחור` and `אליהו בן אשר הלוי אשכנזי`.
Q61073 includes `Sebastian Muenster`, the name in the passage.

The exact proposal adds `P958=51א-52א` to the two existing `P3342` claims.
It preserves the existing person QIDs, sender and recipient roles, ranks, and references.
It does not assign those roles to the scribe or owner.
It does not remove the Adler former-owner claim.

The canonical export defers these roles as `person_role_scope_unresolved`.
Its exact string check does not match the catalogue name forms to the passage variants.
The explicit source passage and current aliases support the narrower manual scope correction.
The audit does not use that deferral as evidence that the persons or roles are false.

## Buchach

Source 751 gives Buchach the role `related place`.
The passage on folio `68ב` identifies Moses ben Judah through his Buchach name.
The source gives no manuscript custody, copy, or presence event there.
The existing `place of attestation` qualifier therefore exceeds the source.

The exact minimum correction removes that qualifier from the Buchach `P7153` claim.
It preserves the place QID, rank, and three reference blocks.
The remaining generic significant-place assertion still requires model review.
The source proves a related place through a person; it does not establish every meaning of significance.
The proposal does not replace attestation with another guessed place role.
It also does not treat absence from the canonical export as proof that all place association is false.

## Exact changes and limits

| Change | Claim suffix | Status |
|---|---|---|
| Remove the whole-manuscript letter genre | `16215B8B-46D1-4129-A8AA-BD840C98A475` | Conditional on the component or source-only disposition |
| Add the explicit folio to the sender role | `B4D6DA6B-9959-4D6E-968F-45EB37E053CE` | Source-supported scope correction |
| Add the explicit folio to the recipient role | `22DE571D-6AD7-4FD9-B137-356465880697` | Source-supported scope correction |
| Remove the Buchach attestation role | `15FB9225-C296-42A9-AEC3-C78CE3FE7B9D` | Source-supported role correction; generic place scope remains open |

All twelve reference blocks on these four claims cite the same manuscript source record.
No block has a mixed or unknown source identifier.
The exact proposal preserves all blocks on the three qualifier changes.
The proposal requires the normal ownership, fresh revision, and complete readback checks.

The corpus screen used the current letter occurrence parser across all 897 source records.
It recognized one record with an explicit contained-letter occurrence: this manuscript.
That record also has the old unscoped genre and person roles.
This narrow parser screen does not establish that the other records contain no letters.
It does not authorize any additional deletion.

## Durable role evidence

The first role override had the correct QIDs, roles, folios, and three complete source blocks per person.
The canonical rebuild still removed both roles under Rule W-170.
Neither person had a person row in that build, and neither claim had `P1932`.
The operator therefore added the exact catalogue names as `P1932` qualifiers.
The source names are `אליהו בן אשר` and `מינסטר, סבסטין`.
The operator retained the sender and recipient role qualifiers and `P958=51א-52א`.
The exact source still determines the person identities; a name qualifier alone does not establish identity.

An offline replay used the actual native statement converter and orphan-person check.
Both complete role statements survived that check with their source names.
The versioned override response confirms both source-qualified statements.
A later public read and upload receipt must still establish live completion.

## Verified public role receipt

The ordinary live upload job `0bc62606-8fd7-47aa-a2ce-0a2e42de7cfa` succeeded.
A fresh Action API read confirms revision `2568087865` on Q141747170.
The entity contains both source-qualified person roles.
Each role has `P958=51א-52א`, its exact `P1932` source name, and three complete reference blocks.
The sender role points to Q66031; the recipient role points to Q61073.
Adler remains the former owner, with the former shelfmark qualifier.
Evidence: `state/gila-recheck-2026-11-11/hizzuk-role-live-readback.json`.
The letter work's public identity remains deferred; this role receipt does not publish that work.
