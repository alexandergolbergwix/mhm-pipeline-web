# Bulk authority and current label repair

On **Wikidata Manuscript Audit and Repair**, select **Repair people and current labels**.
The page starts the existing `wikidata_gila_repair` job with scope `people_authority`.
The job runs an initial audit, saves one preflight decision per open case, applies eligible edits, and runs a final audit.
The scope uses the current open cases for requirements 1 and 5.
It does not depend on the historical manifest of 214 cases.

## Authority and holding evidence

The preflight checks the full MARC occurrence, the role, and the scope of each person case.
It reads NLI records by control number and checks an authority ID once per source service.
`wikidata_gila_authority_cache.py` saves successful Alma JSON-LD, BIBFRAME, and VIAF reads in private job data.
It reuses the reads across the initial audit, preflight, and final audit.
A later audit can reuse a snapshot for the same source fingerprint for up to one hour.
The snapshot keeps its original time. Failed reads remain eligible for another request.
The planner checks each occurrence again. It also reads the current Wikidata person and target revisions.

## Reviewed evidence when VIAF fails

The audit can confirm a cited VIAF link through a reviewed cross-reference when VIAF is unavailable.
It checks the exact MARC occurrence, NLI role link, and both URLs in the reference on the live statement.
It checks the current person item for the exact VIAF ID and an independent authority ID.
It rejects a conflicting MAZAL ID or a fresh VIAF response that lacks matching proof.
The cache returns that response to the current audit. It saves only positive evidence for later audits.

The NLI records identify [Giovanni Paolo Eustachio](https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/authorities/987007583337405171.jsonld) and [Samuel Schönblum](https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/authorities/987007308955805171.jsonld).
The [BnF record](https://data.bnf.fr/ark:/12148/cb17163680d) identifies Eustachio and cites VIAF 268998259.
The [Schoenberg record](https://sdbm.library.upenn.edu/names/47056) gives Schönblum's name and exact dates from his NLI authority.
The fallback verifies existing claims on Q141746870, Q141747220, Q141747353, and Q141747239.
The proof requires an existing source reference. It creates no new repair candidate.

## Current holdings

For a current holding, the full MARC must prove one exact association between the library and shelfmark.
The audit checks the live catalogue identity, the one active shelfmark, its source reference, and the English label.
It compares English labels in Unicode NFC. An equivalent label requires no edit.
The audit counts an existing match when that proof resolves a gap in the flattened source.
If only the English label differs, `plan_holding_labels` can propose that exact label.
An ambiguous holding or conflicting shelfmark remains open.

## Edits and verification

The preflight groups compatible patches by target QID and checks the first revision creator.
The worker requires creator UID `6935067` again before it sends each edit.
The job uses the current revision and a saved intent. It reads the complete edited item back.
A label edit changes only the English label. The comparison checks all other entity data.
The job reuses existing items. It has no creation operation.
Competing person matches and conflicts between patches require review.

The final audit shows the before and after counts for requirements 1 and 5.
The requirement 9 card shows manuscript mappings, source passages that need proof, and current receipts separately.
The audit checks each repair receipt against the exact public revision before it counts the receipt.
See [creator-only writes](creator-only-writes.md) and [completion packet](gila-completion-packet.md).
