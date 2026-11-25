# Corpus preflight and one-click repairs

**Repair all eligible items** sends scope `all_requirements` to the existing repair endpoint.
One `wikidata_gila_repair` job checks all nine requirements against a full MARC audit.
The job reads NLI bibliographic links and BIBFRAME in parallel within their request limits.
It prepares the people, work, holding, and source edits before any edit on Wikidata.
The job reuses authority evidence with the same source fingerprint.
It records the proof for qualified Ktiv membership, explicit MARC language codes, and explicit physical forms when the source supports the claim.
It keeps the model decision and unchecked passages visible in the audit.
The requirement 9 summary includes the count of unchecked passages.
Those passage rows stay in the original audit archive.
The job preserves claim IDs, qualifiers, and references and does not create items.
The writer checks the first creator and current revision before every edit.
The final audit reports before and after counts for all nine requirements.
The earlier `people_and_sources` scope still covers requirements 5 and 8.

## Add source evidence for requirement 8

Select **Preflight and repair all sources** to run the same job with scope `sources_append_only`.
The job audits the full MARC corpus before and after this repair.
The preflight checks every open requirement 8 case.
It retains cases from the previous full audit when the source fingerprint and Studio build match.
It records an existing statement that passes a new proof check as verified before repair.
The final audit must confirm a case explicitly before the job records a verified outcome.
The before and after comparison uses the previous full audit when the source and build match.
The job uses only source proposals for requirement 8.
It adds a claim or appends evidence to an exact claim.
It checks each plan for changes to existing claims, qualifiers, references, or labels before the first write.
It uses the current item revision and checks the first creator before each write.

The file `gila_reviewed_source_proof.json` binds each reviewed source to its control number, field occurrence, exact text hash, and Wikidata item.
The audit requires a match for each of these values.
For scan references, it also requires the exact existing claim ID and URL.
For a missing scan link, the proof identifies the complete copy and the reviewed set of other access URLs.
Any unexpected access URL, including an equivalent URL, holds the case for review.
The writer preserves the existing access claims.
For manuscript form, the audit checks a separate MARC note and the live class item.
For proof that only confirms an existing inscription, it requires the exact live claim and reference.
For a MARC occurrence with several reviewed inscriptions, the worker prepares each exact quotation with its distinct folio.
The audit verifies the occurrence when all its quotations have their matching claims and references.
An unexpected or duplicate inscription holds the entire occurrence for review.
For a reviewed subject or genre, the original record must support the specific work.
The audit requires the reviewed work title and a unique live work link that cites the same Ktiv record.
The writer checks the first creator of the work and uses the current revisions of the manuscript, work, and topic.
The audit requires the cited URL on the matching claim before it verifies the case.
The [subject follow-up](../../../audits/2026-11-24-requirement-8-subject-follow-up.md) records the sources for the next group of subject claims.
The [language and history evidence](../../../audits/2026-11-24-requirement-8-language-and-history-evidence.md) cites exact MARC works and authority records.
The [Weisz and Neubauer catalogue evidence](../../../audits/2026-11-24-weisz-edition-evidence.md), [inscription evidence](../../../audits/2026-11-24-inscription-proof-evidence.md), and [scan evidence](../../../audits/2026-11-24-requirement-8-scan-and-scope-evidence.md) describe the proof sources.
The next proof batch adds [manuscript catalogue pages](../../../audits/2026-11-24-requirement-8-catalogue-pages.md), [Neubauer catalogue entries](../../../audits/2026-11-24-neubauer-additional-catalogue-evidence.md) and [transcribed colophons](../../../audits/2026-11-24-inscription-field-evidence.md).
For an entry across several scan leaves, the claim links to its first leaf and the reference cites the leaf with the shelfmark.
The corpus preflight also uses the [bibliography proof](../../../audits/2026-11-24-catalogue-entry-concordance-evidence.md).
The [holder and illumination proof](../../../audits/2026-11-24-r8-holder-and-illumination-evidence.md) and [inscription proof](../../../audits/2026-11-24-inscription-corpus-preflight.md) identify the exact source fields.
The [work and form proof](../../../audits/2026-11-24-r8-autograph-illustration-scope.md) and [inscription scope review](../../../audits/2026-11-24-inscription-scope-followup.md) document the reviewed targets.
The [catalogue pages](../../../audits/2026-11-24-bibliography-page-and-shelfmark-evidence.md), [Leiden edition proof](../../../audits/2026-11-24-catalogue-entry-concordance-evidence.md), and [holder scan proof](../../../audits/2026-11-24-r8-leiden-and-aiu-scans.md) identify the exact locations.

## Audit rules


The audit uses full MARC to identify each person role and each source occurrence.
`wikidata_gila_sources.py` retains MARC 510 and 581 citations, 590 record notes, 650 subjects, and 655 genres.
It also retains 856 access links and 957 inscriptions.
For MARC 500, it checks explicit title-page text.
It preserves saved citations that differ from full MARC.
If a legacy citation matches exactly one original field or semicolon segment, the preflight links it to that occurrence.
The archive includes the legacy text and the full original field.
The audit counts the original occurrence once and keeps ambiguous and unmatched citations open.
A bibliography summary and its source fact contribute one case for the same occurrence.
The audit retains other passages in the evidence inventory.

The job checks person identities through MARC, NLI Alma, BIBFRAME, and exact authority IDs.
The source checks require an exact quotation, controlled term, reviewed publication, or already identified scan URL.
A work claim needs one source-bound work with a matching title.
An automatic match also needs a record that describes one work.
A reviewed match can use an exact contents note to identify a work in a collection.
For a reviewed subdivided subject, the worker compares the complete ordered MARC 650 field with the proof.
The subject reference includes the catalogue record, the authority URLs, and the complete heading in P1683.
The worker puts the P1683 text in Unicode NFC to match Wikidata. The MARC check uses the original subfields.
The audit checks those values together in one reference on the exact subject claim.
The subject-only repair option restricts edits to P921 and keeps the full audit for verification.
Subject-only repairs send the administrator reference queue limit of `maxlag=100`.
The worker still checks the creator and base revision before each edit.
The job keeps different inscription texts for the same role and folio open for review.
An unknown role or folio can overlap a known one.
The job keeps catalogue status notes as evidence.
The job keeps unresolved edition identities and uncertain access links open.

`wikidata_gila_corpus_repair.py` prepares both plans with current item revisions.
It checks the first creator of every proposed target.
The worker uses the shared Wikidata read window for creator checks and verification.
The window respects temporary rate limits and retries within its time limit.
It combines compatible patches for the same target and claim.
A conflicting patch or a source revision change requires review.
The job saves the complete preflight before the first edit.
The `GET /api/admin/wikidata-gila-repairs/preflight` route returns pages of decisions to administrators with project access.
Each decision includes the case, target, and reason.
The page shows the preflight counts while the edit stage runs.
Use **Download preflight and MARC evidence** to save the complete case list.
The `archive=true` option on the preflight route returns the decisions and original fields from the pinned audits.
It includes catalogue status notes, sources that need review, and final outcomes when the job has them.
The export retains each field's indicators and ordered subfields.
Catalogue status notes stay in the original audit total.

A reviewed author correction can give an exact incorrect author text claim deprecated rank.
It preserves the claim ID, value, qualifiers, and references.
The correction requires the original MARC role note and the exact claim digest.
The author check stays open while that incorrect claim has active rank.
The worker checks the project creator UID before each write.
It keeps the item revision, claim patch, and case IDs in the write intent.
Each edit uses the exact base revision.
The worker reads back the complete item after the edit and verifies the claim change.
The scope has no item creation path. It reuses only verified existing identities.

`test_wikidata_gila_corpus_repair.py` checks exact inscriptions, citation reconciliation, duplicate claim protection, and first creator scope.
The [citation and scope batch](../../../audits/2026-11-25-requirement-8-repair-batch.md) records the catalogue, work, and folio checks.
