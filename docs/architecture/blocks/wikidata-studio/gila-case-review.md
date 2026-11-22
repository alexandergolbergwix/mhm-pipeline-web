# Kimi review of manuscript cases

Open a production place or named scribe case in [Manuscript Audit and Repair](admin-gila-repairs.md).
Select **Review with Kimi** to check the current MARC record against live Wikidata candidates.
The case shows progress, the result, the MARC excerpts, and the candidate revision.
Open the same case to see the saved review or resume progress updates.

Public candidate searches send the project User-Agent with its contact URL.
Without this header, Wikimedia can reject the request before the worker calls Kimi.

The worker sends the bounded MARC excerpts and public candidate evidence to Qubrid.
It uses `moonshotai/Kimi-K2.6` through the Chat Completions API.
It uses `QUBRID_API_KEY` or its `QUABRID_API_KEY` alias and the optional `QUBRID_BASE_URL`.
The case needs one current source record and the latest completed audit.
The worker checks access, the audit ID, and the record hash again before it saves the result.
The prompt explicitly requests JSON because Qubrid requires this term for JSON mode.
The worker validates the QID and the response format. It retries an incomplete response or a temporary service error up to twice.

The original MARC record establishes the name, role, or place.
For a scribe, the system requires a Wikidata ID on the exact MARC occurrence or an exact VIAF or NLI ID.
The ID can come from the occurrence or a high-confidence approved authority match for the same record, name, and role.
The batch also searches live VIAF when the record ties a name and both life dates to one scribe.
The VIAF record must match all three values. Wikidata must link to that exact VIAF ID.
The worker checks VIAF identifiers on the Wikidata candidates as well as the result of the VIAF name search.
If VIAF cannot confirm a dated scribe, the worker checks the GND ID of the candidate through lobid-gnd.
Both life years must match the MARC occurrence. The full MARC heading and a GND heading must also match the public names.
The release cites the GND record. The final audit checks the cited record again.
A name match alone leaves the identity open.
For a place, MARC 751 must tie one place name to the production role and name at least one jurisdiction.
The worker uses aligned flat slots or one structured occurrence to verify the role.
It searches Wikidata and the exact KIMA name index.
The candidate needs coordinates, a name match, and live administrative or country links for each stated jurisdiction.
The preparation worker checks the same evidence again.
An item for the holding library or a related place cannot supply the production role.

**Repair all eligible items** checks supported open cases after the audit and reference queues.
For a missing work link, the worker reconstructs one MARC occurrence with its title and folios.
The public work must have the exact title with a reference to the same Ktiv record.
If MARC names an author, the public literal author must also match.
The worker skips an existing link for the same work or source scope.
The release adds a `P1574` claim with the MARC title and folio qualifiers and the Ktiv citation.
Release preparation accepts this evidence only for the selected manuscript and audited revision.
The release test checks the scoped claim and its citation.
The publication writer preserves other existing claims on the selected manuscript.
The gateway test checks this rule for work and scribe links and repairs that include P31.
The final audit checks the same source, work, and live link for points 3 and 6.
The first-revision creator check excludes other items before the batch requests evidence.
A unique candidate with independent source proof can proceed without a model response.
Kimi can review cases with a source identifier or another exact comparison field.
The model response cannot authorize publication. Code checks the source and candidate independently.
The batch shares one request schedule for Wikidata candidate searches, authority searches, and exact item reads.
It starts at most one request per second. It waits for `Retry-After` before any new request in the batch.
It retries temporary failures up to three total attempts. It records unavailable checks when the wait exceeds the time budget.
The creator check and the MARC and authority proof still control which cases can proceed.
The preparation worker repeats the checks with the same MARC hash, authority IDs, and public revisions.
The release adds only the supported claim, with a reference to the Ktiv record.
It adds a separate reference for a live VIAF match. The final audit checks this authority record again.
It preserves existing statements and excludes conflicting claims.
Publication checks the creator and verifies the result at the new revision.
The final audit also verifies the source case and retains it in its original requirement total.
An unresolved identity, missing source passage, or corpus model decision stays open.

## Code and tests

- `admin_gila_repairs.py` provides `POST /api/admin/wikidata-gila-repairs/case-review` and `GET` on the same path.
- `wikidata_gila_case_review.py` owns the provider call and the single and batch results for `wikidata_gila_case_review`.
- `wikidata_gila_identity_evidence.py` proves the source identity and constructs exact statements.
- `wikidata_gila_gnd_evidence.py` reads cited person records and checks their IDs.
- `wikidata_gila_work_evidence.py` checks the work occurrence and constructs its scoped claim.
- `wikidata_publication_prepare_job.py` rechecks the private result against the same audit and repair cycle.
- The job service excludes private proof details from the public job result.
- `run_jobs.py` restricts job access to administrators. `run_job_params.py` restricts job start to the audit route.
- `AdminGilaRepairs.tsx`, `api/gilaRepairs.ts`, and `api/runJobs.ts` show the case review and its progress.
- `test_gila_case_review.py` checks role and place proof, exact candidate limits, and the route and worker.
- `admin-gila-repairs.spec.ts` checks progress, candidate evidence, and recovery after reload.

See [people coverage](gila-people-coverage.md) for the full MARC source copy, role audits, and scoped scribe selection.
