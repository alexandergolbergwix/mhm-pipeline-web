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

The audit honors the VIAF retry delay. It retries once within an eight-second
wait budget. It defers further VIAF requests after a second HTTP 429 or a
longer delay. For an existing scribe claim with an exact VIAF citation, the
audit can then use the linked Library of Congress personal authority. The
authority heading must match the MARC name and both life dates. The fallback
applies to published claims with the exact catalogue reference. It grants no
permission to write. Tests exercise both retry paths and reject conflicting
evidence. The code lives in wikidata_gila_authority_reads.py.

The audit can also verify a role with the person's exact Library of Congress
authority record. It requires the full MARC name, both life dates, the correct
role, and the exact catalogue reference. For a scribe, the claim must cite
the same authority record. The VIAF fallback still applies to earlier claims.
The audit reads authors on linked work items and counts a repeated link to
the same work once when it selects the work for a source occurrence.

The audit also uses the preferred personal names and life dates in the
imported MAZAL authority file. It checks the exact MAZAL ID on the live
person. If MAZAL lists alternate birth years, the MARC year must be one
of those years. The audit still requires the exact death year. A scribe
claim must cite the MAZAL record or the person's exact unavailable VIAF
record. The audit still requires the catalogue reference. In production
v845, the VIAF outage left the verified Modena claim open because the
Library of Congress used a different Hebrew heading.

When the life dates do not identify the person, the audit can use the
catalogue association with MAZAL. It checks the exact control number and
authority ID through NLI Alma SRU. A preferred name must match one MARC
100 or 700 occurrence in both the source copy and the current catalogue.
The audit also checks the role and any stated dates. The claim must cite
the exact SRU query and the catalogue record. The code that reads the
authority association lives in `wikidata_gila_catalogue_authority.py`.

The role planner in `wikidata_gila_people.py` supports scribes, authors, and
former owners. An author repair requires a unique work for the exact MARC
title. A copy role requires the exact manuscript. The planner keeps existing
claims and unknown values. It can add the catalogue and authority references
to an existing claim with the same scope. A conflict or an uncertain source
leaves the case open. Before a live edit, the operator must check the current
revision and the first creator of the exact target.

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
