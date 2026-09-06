# Publication dry-run job

`POST .../wikidata-publications/{publication_id}/advance` with `type=dry_run`
returns a queued operation. Its operation ID is a `run_jobs` ID.
The request verifies run access and resolves the user's saved wiki credential.
It stores the original command and an encrypted private credential grant.
The grant scope starts with `dry-run:`. The worker rejects other command types.
The generic job endpoint rejects this job kind.

`wikidata_publication_dry_run_job.py` runs the command through `PublicationRuntime`.
The core checks source currency, the Approval Set, and its digest before remote reads.
It checks cancellation before the first read and between pages of 50 entities.
It commits each page before progress updates. Only the completed plan has a receipt.
The existing job service owns admission, claims, heartbeats, and failure reports.
A remote error fails the job. An inconclusive identity check blocks the plan.
Neither result permits a write. A retry starts a new dry-run job.

`WikidataPublicationPanel.tsx` polls the job until it reaches a terminal status.
It preserves the Release while the job runs and shows `JobProgressInline`.
It also updates the global job tray and provides a cancel button.
On completion, it reads the Publication summary and recalculates Publish readiness.
On page return, it attaches to the active dry-run for that run.
Prepare uses the same repeated poll; it no longer stops after the first response.

Tests: `backend/tests/test_publication_router.py` checks queued responses, both
credential targets, worker completion, failed reads, blocked plans, cancellation,
and stale digests. `frontend/e2e/wikidata-publication.spec.ts` checks multiple job
polls before the valid receipt permits Publish.


## Restore and cache (W-219)

`GET .../wikidata-publications/latest` returns the latest sealed Release for the run.
The UI restores its target, profile, approvals, plan, and receipt.
It reads the latest dry-run job and restores its progress and error when the Publication matches.
This restore is read-only. A page refresh must not create another job.

A normal dry-run request reuses a completed current plan for the same account.
Reuse requires matching source, Release, Approval Set, and receipt digests.
It also requires an unexpired receipt and no Execution.
The response contains the saved Publication with no new operation.
`Override cache (fresh Wikidata checks)` sets `force_refresh=true` and queues a new job.
Failed plans remain failed. The UI shows counts and up to 50 blocked actions.
The cache stores completed plans; an interrupted dry-run requires a new check.

Route tests verify restore, reuse without remote reads, and explicit override.
Browser tests verify refresh restore and the override command.
