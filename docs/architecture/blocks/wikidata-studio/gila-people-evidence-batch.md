# Preflight for the 214 people cases

Select **Repair 214 people cases** in **Wikidata Manuscript Audit and Repair**.
This action starts one `wikidata_gila_repair` job with scope `people_evidence_batch`.
The job runs a fresh audit, checks the evidence, applies eligible repairs, and audits the result.
The page shows the preflight decisions and the before and after counts for requirement 5.

The manifest contains 84 author cases, 39 scribe cases, and 91 former owner cases.
It uses the case IDs from audit `041b9dd9-3fcc-4501-9c91-5882bfbbef46`.
The job compares each ID, MARC occurrence, role, and manuscript QID with the current open audit.
It retains one decision per manifest row, including cases that have changed or left the open audit.
An edit must also use the exact destination QID in the manifest.

The job uses the full MARC records and the current authority claims.
An author needs an exact work link and a source-bound author statement.
A reviewed scribe needs a matching MARC occurrence and a digest of the cited note.
A reviewed former owner needs a public MARC 561 note for the same copy and an exact authority ID.
The planner holds cases with conflicting identities or insufficient evidence.
The job reuses existing people and creates zero items.

The preflight checks the first revision of each proposed edit target.
The gateway checks it again before the edit and requires creator UID `6935067`.
The gateway uses `baserevid`, keeps unrelated claims, and reads the saved revision back.
The final audit verifies the role and authority evidence on each edited claim.
The case list shows the saved decision for each source occurrence.

- `gila_people_evidence_batch.json` stores the case and target manifest.
- `wikidata_gila_people_evidence_batch.py` checks the scope and destination.
- `wikidata_gila_reviewed_people.py` stores exact authority checks for reviewed authors and former owners.
- `wikidata_gila_scribe_colophons.py` stores exact source checks for reviewed scribes.

See [bulk repairs for people and roles](gila-people-batch.md) and [creator checks](creator-only-writes.md).
