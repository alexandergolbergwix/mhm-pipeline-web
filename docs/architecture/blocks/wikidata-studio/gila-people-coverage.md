# People, roles, and authority links in Manuscript Audit and Repair

The first audit loads the complete MARC fields for the project records. It uses
the original structured fields if the project import has them. For a flat
record, it requests MARCXML from NLI Alma SRU with the exact MMS ID. It checks
field 001 and retains source order. It keeps the local Alma fields beside
the standard fields. A missing response leaves an explicit audit case.

The audit stores the source copy in its private job parameters. The first
and final audits in the same repair cycle use this copy. The other eight
requirements still use the project source. Evidence review and Publication
preparation use the same copy for scribe claims. The release verifies the
source digest, the candidate, the current revision, and the first creator.

The audit gives one check to each personal-name occurrence and role. A linked
880 heading adds a name form to its base occurrence. The old flat role and date
warning has one audit row. When the full fields exist, the source role checks
take the place of the legacy scribe and alignment rows. The audit compares
the work author with the exact linked work, and copy roles with the exact
manuscript. The named role needs a source-bound reference. An authority link
needs an exact source ID or independent authority evidence. An uncertain
source name can have a verified unknown-value claim. Other identities stay
open for review. Manuscript notes remain in the source passage report.

The page displays counts for the full MARC records, verified roles, verified
authority links, and preserved uncertainties. The new point 5 denominator
counts source roles, so it differs from the old projection count.

The normal repair button runs the complete cycle. An administrator can send
a body with `people_links` to `POST /api/admin/wikidata-gila-repairs/repair-all`
to limit writes to scribe claims on the listed items and scribe targets. The request allows 1 to 50 pairs. The cycle still audits all nine requirements. The selection does
not authorize edits to items with other creators. The same first-revision
guards and checked Publication workflow apply.

Tests in `test_wikidata_gila_people.py` check the exact NLI control number,
field preservation, linked-script role counts, authority evidence, and
write scope. `test_gila_case_review.py` checks evidence and creator proof.
