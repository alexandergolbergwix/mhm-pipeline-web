# Live administrator reference progress

The administrator repair page receives project job events.
The page also refreshes its protected summary two seconds after each completed read.
A slow request does not overlap another read.
A matching job event requests an immediate refresh.
A reconnect requests another refresh.
The page retains the last confirmed counts after a network error.
A 401 or 403 response clears the protected data.
A successful refresh clears the connection error.
An old response cannot replace a newly accepted job.

## Progress and phases

The bar counts verified correction receipts and already-clean items.
It does not count the current ordinal, queued work, or an unconfirmed write.
One panel groups the queue state, confirmed progress, and three totals.
The totals show verified corrections, already-clean items, and items remaining.
The start or resume action appears beside the required confirmation.
The action appears only when the administrator can request it.
The page distinguishes the queue, start checks, active repairs, Wikidata delays, and terminal states.
The queue message explains the wait for a worker or capacity.
The worker publishes its initial phase before a remote check.
The worker publishes counts only after the private checkpoint commits.
The terminal snapshot retains the counters, current QID, and saved retry time.
The status API tells the page whether Resume can repeat preflight or must check a previous repair.

A Wikidata delay shows the current QID and a countdown.
The countdown updates each second.
The countdown does not advance the progress bar or submit another request.
A creator check timeout before a saved plan shows a separate countdown for the automatic retry.
A past edit retry date shows whether the worker still needs a successful lag check.
The completion estimate uses the average rate of confirmed results in the latest 25% of the job's elapsed time.
The window includes Wikidata delays. It counts verified edits and already-clean items.
The status API divides the items left by that rate and returns `eta_seconds`.
The page shows the estimate only for a live job with a complete window of timed history and recent progress.
Retry and update times use `Asia/Jerusalem`.
The Queue details section shows the last API sync, last worker contact, and saved snapshot counts.
The protected summary exposes at most five recent confirmed results.
It does not expose source text or the private exact plan.

## Files and tests

- `frontend/src/api/referenceRepairs.ts`: typed summary and explicit start command.
- `frontend/src/hooks/useReferenceRepairStatus.ts`: serial refresh, event updates, recovery, and stale-response guards.
- `frontend/src/components/wikidata/ReferenceRepairProgress.tsx`: confirmed bar, phases, countdown, estimate, and recent results.
- `frontend/tests/unit/referenceRepairProgress.spec.tsx`: queue, receipt counts, countdown, and connection errors.
- `frontend/tests/unit/referenceRepairStatus.spec.tsx`: pushes, serial reads, role revocation, recovery, and stale responses.
- `frontend/e2e/admin-reference-repairs.spec.ts`: backend phase changes without another start request.
- `backend/tests/test_admin_reference_repairs.py`: bounded public counters and private-plan exclusion.
- `backend/tests/test_reference_repair_worker.py`: committed counts during a service delay.

The page shows the lag limit from the status API. The worker uses that limit for the probe and edit (W-349).

During a delay, the page shows the probe result, reported lag, edit limit, and next check time.
The worker exposes only a bounded numeric lag value from the public probe.
The saved retry time is the earliest time the worker can try the edit.
The scan of saved snapshots shows separate counts and does not change the repair bar.
