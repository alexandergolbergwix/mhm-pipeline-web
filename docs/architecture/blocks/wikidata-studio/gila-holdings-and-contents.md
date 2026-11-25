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

## Reviewed MARC 730 cases

The repair endpoint accepts scope `uniform_titles` for requirement 6.
`wikidata_gila_uniform_titles.py` uses `gila_reviewed_uniform_titles.json` for exact work identities and folios.
Each review binds the public manuscript to the original 730 fields and the exact supporting MARC fields.
Scan and catalogue URLs document the work or folio proof.
A scroll can have an exact work with no folio number.
The audit checks every original 730 field before it credits the case.
It can add a proven component while the remaining components stay open.

The worker adds a sourced P1574 claim if the work and scope have no competing active claim.
It can add P958 and a reference to one confirmed work claim with no folios.
It keeps the claim ID and every old qualifier and reference.
A conflicting claim needs a separate exact review of its ID and full digest before the worker can give it deprecated rank.
Separate reviewed texts can share the same folios.
The evidence can name parallel work IDs from the same review.
The worker still checks the catalogue reference and rejects a conflicting title.
The worker confirms the first creator before it edits an item.
This scope excludes item creation and statement removal.

The review started with 106 cases and 142 MARC 730 fields.
All 142 fields have a blank second indicator.
The review found public scan manifests for 93 records.
The manifests gave no work-level ranges.
The evidence file includes exact boundaries from scanned pages or the holding catalogue.

The scans of NLI Ms. Heb. 2428=38 show Esther on folios 106a–117a.
The signature at the end of the text is the source of a prior work link.
The repair gives that exact prior claim deprecated rank and retains its data.
[NLI scan](https://iiif.nli.org.il/IIIFv21/DOCID/PNX_MANUSCRIPTS990000454350205171-1/manifest)

The Columbia scan shows the Carpentras prayer on folios 93a–119a.
The repair adds the range to the existing work claim.
[Columbia catalogue](https://clio.columbia.edu/catalog/17229167) · [Scanned folio 93a](https://archive.org/download/ldpd_17229167_000/ldpd_17229167_000.pdf#page=199)

The [second review](../../../audits/2026-11-25-marc-730-second-review.md) records additional scan evidence and cases that need more proof.

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
