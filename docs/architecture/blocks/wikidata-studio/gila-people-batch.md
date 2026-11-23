# Bulk repairs for people and roles

**Repair all eligible items** uses the existing `wikidata_gila_repair` job.
The job audits all nine requirements before and after the edits.
Send `{"requirement": 5}` to the same administrator route to limit edits to requirement 5.
The legacy `people_links` selection still uses the scoped Publication release.

The bulk stage reads one NLI Alma JSON-LD record for each eligible manuscript.
It validates the exact MMS ID and uses the full MARC record from the first audit.
It checks one distinct name occurrence, the exact role, and any stated life dates.
It uses the existing property map for annotations, signatures, and illustrations.
For a former owner, the JSON-LD path also needs a copy-specific public MARC 561 note.
If JSON-LD gives no exact match, the stage reads NLI BIBFRAME.
It checks the MMS ID on the Work and Instance and checks their link.
The agent must identify a person and contain the exact MARC field and role.
The BIBFRAME path can prove the explicit former-owner role on that field.
The stage leaves unclear roles, analytic copy roles, and conflicting names open.

The job searches Wikidata by MAZAL ID in batches.
It requires one candidate and confirms the live human class and MAZAL ID.
If Wikidata has no item for that ID, VIAF must confirm the same MAZAL source ID.
The job searches the exact VIAF, GND, Library of Congress, ISNI, and BnF IDs.
It rejects multiple Wikidata candidates and conflicting current IDs.
The MAZAL index can also supply proof through a preferred name and two exact life years.
That path requires the same MAZAL ID on a live human item.
The job checks the exact manuscript ID for copy roles.
An author edit needs an exact source-bound work link and a unique work title.
The job reuses existing people and has no item creation path.
A Wikidata person that another account created can supply an identifier.

The stage groups exact claims by the item that receives the edit.
It keeps unrelated claims, references, ranks, and qualifiers.
A source-bound unknown scribe claim can receive a proven person ID.
The job checks the current source and the Studio build before it starts the edit stage.
It checks the current administrator, the edit target, the source manuscript, and the person revisions.
The gateway requires account UID `6935067` and checks the first revision of the edit target.
A saved intent records the exact changes before the gateway sends the request.
The gateway uses `baserevid` for the exact request.
It checks the revisions again before a retry of a confirmed temporary rejection.
The job reads the complete item again and checks the result.
An uncertain result stops the edit stage and keeps the intent for recovery.

The final audit reads the cited NLI JSON-LD, BIBFRAME, and VIAF sources again.
It checks the relation and authority ID on the live statements.
The audit assigns a stable ID to each source occurrence and role.
The result shows verified case counts, edited items, creator exclusions, and unavailable records.
The page displays the current phase and the before and after verification counts.

## Files

- `wikidata_gila_linked_people.py` checks NLI links and matches exact authority IDs.
- `wikidata_gila_bibframe_people.py` matches the exact MARC fields on NLI agents.
- `wikidata_gila_mazal_viaf.py` confirms the MAZAL source ID and resolves exact authority cross-references.
- `wikidata_gila_people_batch.py` plans, groups, and verifies exact live changes.
- `wikidata_gila_repair_job.py` records the private receipts and starts the final audit.
- `wikidata_gila_audit_job.py` and `wikidata_gila_people.py` check the cited sources.
- `wikidata_gila_completion.py` preserves the identity of each source role case.

The tests check the route scope, the exact person proof, and the full item comparison.
See `test_admin_gila_repairs.py` and `test_wikidata_gila_people.py`.
