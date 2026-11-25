# Reviewed shelfmark and content repairs

Select **Repair shelfmarks and contents** in Manuscript Audit and Repair.
The existing repair endpoint accepts scope `holdings_and_contents`.
The job checks requirements 1 and 6 in the full MARC audit.
It records the initial audit, eligible corrections, and final live result.
The existing preflight API accepts point 6 for this scope.

## Source evidence

`wikidata_gila_audit.py` uses a unique complete MARC 942 row with first indicator 1 when repeated numbers block the normal current-holder check.
The row must name a holder that MARC 710 identifies as a current owner.
The verifier checks all fields from that row together.
It checks the active, source-cited P217 and the NFC English label.
It preserves multiple current rows for scope review.

`marc_ingest.py` retains every original MARC 730 occurrence in the prepared record.
The source includes the indicators and ordered subfields.
The Wikidata projection retains this evidence when a flattened related title matches it.
The audit also restores the original occurrences for cases from a saved Studio build.
Both paths standardize quotation marks for the title match.
The report retains the original MARC text.
A uniform title still needs proof that the manuscript contains the named part before the worker can propose a P1574 link.
The audit keeps MARC 730 part and language data visible in the case details.
[MARC 730](https://www.loc.gov/marc/bibliographic/bd730.html)

## Exact corrections

`wikidata_gila_holdings_contents.py` checks the full source fields and the exact prior claim.
The reviewed data is in `gila_reviewed_holdings_and_contents.json`.
For a shelfmark, the prior number must match its reviewed historical or catalogue field.
The audit credits the current number after it verifies the reviewed source and the live claim.
It keeps the ID of the check from the saved Studio build.
For a contents link, the audit checks the exact folio, title, and work target.
The planner adds the sourced current claim and puts the superseded claim at deprecated rank.
It keeps the old claim ID, value, qualifiers, and references.
For the shelfmark batch it also sets the English label.
It corrects the English description when the old description still matches the review.
The worker checks the first creator and current revisions before the write.
The complete read-back checks every claim and term against the planned edit.
The scope excludes foreign items, item creation, and statement removal.

The first batch records 182 items with matching live labels and shelfmarks across 362 checks.
It also records 11 reviewed shelfmark corrections and the Book of Job link at folios 107a–112b.
[Ktiv Book of Job source record](https://www.nli.org.il/he/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990034538980205171&scope=KTIV&vid=KTIV)

The corpus repair tests check repeated current-holder rows, exact shelfmark selection, and retention of MARC 730 parts and languages.
