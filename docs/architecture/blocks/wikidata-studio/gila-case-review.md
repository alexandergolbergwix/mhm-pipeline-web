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

The original MARC record establishes the name, role, or place.
For a scribe, the system requires a Wikidata ID on the exact MARC occurrence or an exact VIAF or NLI ID.
The ID can come from the occurrence or a high-confidence approved authority match for the same record, name, and role.
The batch also searches live VIAF when the record ties a name and both life dates to one scribe.
The VIAF record must match all three values. Wikidata must link to that exact VIAF ID.
A name match alone leaves the identity open.
For a place, MARC 751 must specify the production role and a unique place, region, and country.
The system looks up those names through the current public administrative links.
An item for the holding library or a related place cannot supply the production role.

**Repair all eligible items** checks supported open cases after the audit and reference queues.
The first-revision creator check excludes other items before the batch requests evidence.
Kimi selects a candidate from the public results or asks for more evidence.
Code checks the source and candidate independently.
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
- `wikidata_publication_prepare_job.py` rechecks the private result against the same audit and repair cycle.
- The job service excludes private proof details from the public job result.
- `run_jobs.py` restricts job access to administrators. `run_job_params.py` restricts job start to the audit route.
- `AdminGilaRepairs.tsx`, `api/gilaRepairs.ts`, and `api/runJobs.ts` show the case review and its progress.
- `test_gila_case_review.py` checks role and place proof, exact candidate limits, and the route and worker.
- `admin-gila-repairs.spec.ts` checks progress, candidate evidence, and recovery after reload.
