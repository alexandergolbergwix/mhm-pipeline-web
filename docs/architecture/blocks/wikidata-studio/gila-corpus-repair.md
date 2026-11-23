# Corpus preflight for requirements 5 and 8

Select **Repair all eligible items** on the manuscript audit page.
The page sends `{"scope":"people_and_sources"}` to `POST /api/admin/wikidata-gila-repairs/repair-all`.
The existing `wikidata_gila_repair` job audits the corpus before and after the edits.
The job uses the same audit rules for both reports.

The audit uses full MARC to identify each person role and each source occurrence.
`wikidata_gila_sources.py` retains MARC 510 and 581 citations, 590 record notes, 650 subjects, and 655 genres.
It also retains 856 access links and 957 inscriptions.
For MARC 500, it checks explicit title-page text.
It preserves citations that differ from the saved projection.
A bibliography summary and its source fact contribute one case for the same occurrence.
The audit retains other passages in the evidence inventory.

The job checks person identities through MARC, NLI Alma, BIBFRAME, and exact authority IDs.
The source checks require an exact quotation, controlled term, reviewed publication, or already identified scan URL.
A work claim needs one source-bound work with a matching title and no evidence of analytic scope.
The job keeps catalogue status notes as evidence.
The job keeps unresolved edition identities and uncertain access links open.

`wikidata_gila_corpus_repair.py` prepares both plans with current item revisions.
It checks the first creator of every proposed target.
It combines compatible patches for the same target and claim.
A conflicting patch or a source revision change requires review.
The job saves the complete preflight before the first edit.
The `GET /api/admin/wikidata-gila-repairs/preflight` route returns pages of decisions to administrators with project access.
Each decision includes the case, target, and reason.
The page shows the preflight counts while the edit stage runs.

The worker checks the project creator UID before each write.
It keeps the item revision, claim patch, and case IDs in the write intent.
Each edit uses the exact base revision.
The worker reads back the complete item after the edit and verifies the claim change.
The final audit reports the verified counts and percentages for both requirements.
The scope has no item creation path. It reuses only verified existing identities.

`test_wikidata_gila_corpus_repair.py` checks exact inscriptions, duplicate claim protection, and first creator scope.
