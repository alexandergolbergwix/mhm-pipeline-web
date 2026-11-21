# Kimi review of manuscript cases

Open a production place or named scribe case in [Manuscript Audit and Repair](admin-gila-repairs.md).
Select **Review with Kimi** to check the current MARC record against live Wikidata candidates.
The case shows progress, the result, the MARC excerpts, and the candidate revision.
Open the same case to see the saved review or resume progress updates.

The worker sends the bounded MARC excerpts and public candidate evidence to Qubrid.
It uses `moonshotai/Kimi-K2.6` through the Chat Completions API.
It uses `QUBRID_API_KEY` or its `QUABRID_API_KEY` alias and the optional `QUBRID_BASE_URL`.
The case needs one current source record and the latest completed audit.
The worker checks access, the audit ID, and the record hash again before it saves the result.

The system confirms a scribe match when an intact MARC occurrence has the scribe role and explicitly identifies the current Wikidata person.
For places it can also confirm a unique geographic match when MARC 751 names the place, region, and country for the production role.
The system compares the names with the live administrative links.
Kimi can select a candidate or ask for more evidence.
The system classifies a match as a candidate if the independent checks cannot confirm it.
A corpus choice about whether to combine manuscript and work items stays with the project.

The case review saves evidence in the job result.
A repair release follows the separate source and Publication checks.
The review has no authority to edit Wikidata or change the audit outcome.

## Code and tests

- `admin_gila_repairs.py` provides `POST /api/admin/wikidata-gila-repairs/case-review` and `GET` on the same path.
- `wikidata_gila_case_review.py` owns the proof, provider call, and `wikidata_gila_case_review` job result.
- `run_jobs.py` restricts job access to administrators. `run_job_params.py` restricts job start to the audit route.
- `AdminGilaRepairs.tsx`, `api/gilaRepairs.ts`, and `api/runJobs.ts` show the case review and its progress.
- `test_gila_case_review.py` checks role and place proof, exact candidate limits, and the route and worker.
- `admin-gila-repairs.spec.ts` checks progress, candidate evidence, and recovery after reload.
