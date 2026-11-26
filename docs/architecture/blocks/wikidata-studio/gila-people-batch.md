# Bulk repairs for people and roles

The [corpus repair](gila-corpus-repair.md) uses this person plan in the `wikidata_gila_repair` job.
The job audits all nine requirements before and after the edits.
Send `{"requirement": 5}` to the same administrator route to limit edits to requirement 5.
The legacy `people_links` selection still uses the scoped Publication release.
Use the [214-case preflight](gila-people-evidence-batch.md) for the reviewed author, scribe, and former owner cases.

The bulk stage reads one NLI Alma JSON-LD record for each eligible manuscript.
It validates the exact MMS ID and uses the full MARC record from the first audit.
It checks one distinct name occurrence, the exact role, and any stated life dates.
It uses the existing property map for annotations, signatures, and illustrations.
For a former owner, the JSON-LD path also needs a copy-specific public MARC 561 note.
For a recipient, the job requires a letter title, the MARC genre for personal correspondence, and one explicit recipient.
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
When an author has a reviewed authority cross-reference, the job requires the same live person and identifier.
The job cites the independent authority and the exact NLI record in the same reference.
An unexpected MAZAL identifier blocks this path.
When MARC has no linked authority, an exact review can bind one primary-author heading to the expected work and an existing person.
The job requires the same current person identity and a source-bound work link.
A reviewed title variant applies only to its exact source control number, heading, and unique live work.
The final audit checks the same source and claim conditions.
The job can keep an existing author qualifier that matches the full MARC name, title, and numeration.
The qualifier can contain these parts in addition to the name in subfield `$a`.
A reviewed scribe needs the exact MARC occurrence and a digest of the source note.
The job checks the linked person before it adds the scribe reference.
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
The GND reader retries an interrupted request twice. The audit requires valid authority evidence after a retry.
It checks the relation and authority ID on the live statements.
The audit assigns a stable ID to each source occurrence and role.
The result shows verified case counts, edited items, creator exclusions, and unavailable records.
The page displays the current phase and the before and after verification counts.

## Files

- `wikidata_gila_linked_people.py` checks NLI links and matches exact authority IDs.
- `wikidata_gila_bibframe_people.py` matches the exact MARC fields on NLI agents.
- `wikidata_gila_mazal_viaf.py` confirms the MAZAL source ID and resolves exact authority cross-references.
- `wikidata_gila_reviewed_people.py` lists reviewed NLI cross-references for existing person identities.
- `wikidata_gila_gnd_evidence.py` checks GND response identity and retries transport errors.
- `wikidata_gila_scribe_colophons.py` binds reviewed scribes to exact source notes.
- `wikidata_gila_people_batch.py` plans, groups, and verifies exact live changes.
- `wikidata_gila_repair_job.py` records the private receipts and starts the final audit.
- `wikidata_gila_audit_job.py` and `wikidata_gila_people.py` check the cited sources.
- `wikidata_gila_completion.py` preserves the identity of each source role case.

The tests check the route scope, the exact person proof, and the full item comparison.
See `test_admin_gila_repairs.py` and `test_wikidata_gila_people.py`.

The authority review used MARC and three independent catalogue records.
See [Giovanni Battista Iona](https://id.loc.gov/authorities/names/n86814129.html),
[Arnold Boate](https://id.loc.gov/authorities/names/n85005814.html),
and [Israel ben Aaron Jaffe](https://d-nb.info/gnd/142126152).
The [scribe authority](https://teman.org.il/content/13241) identifies Solomon son of Israel son of Joseph and his work in Gadas.

Reviewed author sources include [Ezobi's IdRef record](https://www.idref.fr/067065791),
[Hammerschlag's maker record](https://cja.huji.ac.il/?f=maker_name&id=660&mode=treefriend),
and [Adler's edition of Danon's elegies](https://www.jstor.org/stable/29777565).
Reviewed title variants require a unique matching source link and preserve the existing work and its other claims.
