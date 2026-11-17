# Administrator backend reference repairs

The production page is `/admin/wikidata-reference-repairs`.
The Admin sidebar links to this page.
`AdminRoute` blocks non-administrators before it mounts the page.
The repair-plan route and its API also require an administrator.
The Studio header hides its repair link from other users.

## Backend controls

- `GET /api/admin/wikidata-reference-repairs`: bounded status and the reviewed scope digest.
- `POST /api/admin/wikidata-reference-repairs/start`: explicit consent and the exact scope digest.

Both routes use `require_admin` and current project access.
The normal session and CSRF middleware remain active.
The POST also requires editor access and the original administrator for this sealed scope.
Extra fields, commands, QIDs, and source changes are forbidden.
An active or completed job does not create another job.
An uncertain or blocked checkpoint requires inspection before another start.
The generic job endpoint cannot create this job kind.
Non-administrators cannot list, read, or cancel this job through the generic job routes.

## Durable job

The private endpoint creates `wikidata_reference_repair` in `run_jobs`.
The existing claim, heartbeat, admission, progress, and cancellation services own the job.
The job uses the upload slot and stays on Heroku.
It does not use Modal or a local process.
The administrator can close the browser after the job starts.

`gila_reference_seed.json` contains the existing reviewed queue and its stopped local checkpoint.
It contains public entity data and source hashes, not credentials.
The original successful build receipt supplies the actor and project.
The current plan retains its original Publication identity and retry state.
The worker stores the completed prefix and current exact plan in private job parameters.
It commits the current plan before a public write.
Completed plans leave the checkpoint; verified receipts remain.
The public job serializer excludes these private parameters.
Temporary files are a working copy, not the durable checkpoint.
A restart restores the current exact plan from Postgres.

A session advisory lock permits one reference queue per run across hosts.
The lock uses a direct Postgres connection in autocommit mode.
It does not use a PgBouncer transaction pool.
Future local CLI invocations use the same lock.
The old local process was stopped before the seed transfer.
The local handoff marker forbids another local control start.

## Write boundary

The worker rechecks the administrator role, job state, project access, and source before each write.
Creator UID 6935067 remains mandatory.
Foreign-created items remain read-only relation targets.
The normal reference queue retains `maxlag=5`, saved retry dates, and exact Publication intents.
Unknown outcomes stop the queue.
The six Studio corrections, source imports, identity decisions, and foreground exceptions remain excluded.
A verified receipt does not certify all nine Gila requirements.

## Deployment

`GILA_REFERENCE_CLOUD_ENABLED` defaults to false.
Enable it only after the local runner has stopped and the backend release is ready.
The feature requires the existing server Publication credential.
The new job kind and parameter use existing columns; no migration is required.
This change requires a Heroku release. It does not require a Modal release.

## Files and tests

- `backend/app/routers/admin_reference_repairs.py`: administrator API and idempotent start.
- `backend/app/pipeline/wikidata_reference_repair_job.py`: checkpoint restore, role checks, and progress.
- `backend/app/publication/reference_queue_lease.py`: cross-host exclusion through direct Postgres.
- `backend/app/data/gila_reference_seed.json`: reviewed handoff scope and current sealed plan.
- `frontend/src/components/AdminRoute.tsx`: role gate before a protected page mounts.
- `frontend/src/routes/admin/AdminReferenceRepairs.tsx`: backend controls and status.
- `test_admin_reference_repairs.py`: role, scope, start, generic-path, and checkpoint tests.
- `test_reference_repair_worker.py`: restored plan and pre-write checkpoint tests.
- `test_reference_cloud_checkpoint.py`: role revocation, cancellation, and lease tests.
- `admin-reference-repairs.spec.ts`: mocked administrator controls and non-administrator redirects.
