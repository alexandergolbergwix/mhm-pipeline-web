# Wikidata Manuscript Audit and Repair

Select **Manuscript audit and repair** in the Admin sidebar to open `/admin/wikidata-gila-repairs`.
Open **How repairs work** for the source review, release, and dry run steps.
The API requires the administrator role and current access to the Gila project.
The project and run come from the server reference seed.

## Live audit

1. Build the canonical Wikidata Studio source.
2. Start the audit. The page shows the current phase and live Wikidata read count.
3. Open any of the nine requirements. Filter the cases by status, QID, catalog ID, or source text.

`POST /api/admin/wikidata-gila-repairs/start` creates one durable `wikidata_gila_audit` job per run.
The job uses the build slot and the normal job heartbeat and cancel controls.
The generic start route refuses this job kind.
Only administrators can read or cancel it.
The page polls `GET /api/admin/wikidata-gila-repairs` for phase and summary.
The API keeps the last finished report available during a new audit.
The job tray checks the private job detail after the audit leaves the active list.
Select **View** in the tray to return to the audit page.

The worker reads the saved canonical build, current MARC records, and the production QID ledger.
The worker uses the five-minute job limit for each Studio freshness check (W-354).
If the source is missing or stale, the worker stops before it reads Wikidata.
Select **Rebuild source and run audit** to build the canonical source from approved records (W-355).
The page shows progress from the Studio build job.
Keep the tab open so the page can start the audit after the build succeeds.
If the build fails, the page shows its error and keeps the last audit report.
The page shows a separate phase for the check before the worker saves the report.
The worker streams the canonical build and keeps its audit fields.
It checks current public revisions for the project items, audited reference scope, and linked work and person items.
The Action API supplies complete entities in batches of up to 50.
The worker keeps batch data in a temporary directory and caches at most 24 complete items in memory.
The report records historical proposal proof without copies of its claim payloads.
Missing or redirected items cannot supply a verified record.
The scope covers known QIDs. A wider duplicate search needs separate evidence.

The worker uses the Gila comparison and completion packet.
It checks that the same build and source records remain current before it saves the report.
It stores the case inventory in compressed private job parameters.
`GET /api/admin/wikidata-gila-repairs/cases` returns up to 50 cases per request from the current report.
A source gap, unresolved identity, or unchecked passage remains visible.
The job summary contains counts and requirement states.

## Repair

Use **Review source** to save an evidence-backed decision in the normal Studio source review.
Rebuild the canonical source and run the audit again after a source change.
Missing source evidence, possible duplicates, and choices about the manuscript model require review.
The audit needs public proof before it can confirm a correction.

Use **Prepare repair release** for a source-supported case with a confirmed target.
`POST /api/admin/wikidata-gila-repairs/prepare` accepts a case ID and audit ID.
The server checks the current source, canonical build, item, and target.
An anonymous first-revision check refuses items with a different or unknown creator before preparation.
The Publication job seals one existing item and checks the audited build again.
The operator uses the normal Publication review, dry run, and publish flow.
Only an update to the exact selected QID can pass.
The gateway requires fresh proof that both the first revision and the active Wikidata account have UID `6935067`.
A different, hidden, or unavailable creator remains outside the write scope.
The worker applies the same guard before each send.

Use **Remaining references** for the reviewed citation scope.
The reference queue has its own progress and verified receipts.
Run a new audit after publication to check live results across the nine requirements.

## Files

- `admin_gila_repairs.py` provides the role gates, status, cases, and preparation routes.
- `wikidata_gila_audit_job.py` owns the audit, live batch reads, and cases.
- `wikidata_reference_reader.py` validates complete public snapshots.
- `wikidata_publication_prepare_job.py`, `publication/runtime.py`, and `publication/wikidata_gateway.py` enforce the scoped release.
- `AdminGilaRepairs.tsx` and `gilaRepairs.ts` provide the live console.
- `test_admin_gila_repairs.py`, `test_publication_source_projection.py`, and `admin-gila-repairs.spec.ts` check the routes, scope, and user controls.
