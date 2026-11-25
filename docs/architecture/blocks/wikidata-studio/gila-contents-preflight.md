# Full contents preflight

Select **Preflight and repair all contents** to run scope `contents`.
The job compares all open scored requirement 6 cases.
It retains cases from the prior audit when the source and Studio build match.
The preflight lists cases with public proof, eligible repairs, and cases that need review or evidence.
Download the preflight archive to inspect the source alongside its outcome.

`wikidata_gila_contents.py` checks the original MARC work occurrence.
A new or reused work link needs a unique live work identity and exact source scope.
A shared title needs an author link that matches authority evidence or the original heading and dates.
A legacy ledger key can supply a candidate when its exact local work key has one public QID.
The audit checks that QID against MARC before it prepares a repair.
The planner checks each public revision, the first creator, and all evidence dependencies.
It permits only additions to P1574, its source references, or a missing P958 qualifier.
The final audit credits a case only when the verified export names the same case ID.

The audit also reports MARC 740 and 505 passage coverage.
This count uses every source passage in those fields and stays separate from the requirement 6 score.
It reports unique public title matches, matches with folios, and passages with exact scope proof.
A MARC 740 row with a blank second indicator needs a corroborating contents note and matching range.
The repair result stores both passage coverage snapshots.
[MARC 740](https://www.loc.gov/marc/bibliographic/bd740.html) · [MARC 505](https://www.loc.gov/marc/bibliographic/bd505.html)

`test_wikidata_gila_contents.py` checks that a work keeps the original case ID.
It covers conflicting work identities, the append-only patch, and the separate passage count.
