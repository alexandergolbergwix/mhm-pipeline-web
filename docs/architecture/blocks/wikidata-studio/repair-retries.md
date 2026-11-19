# Durable exact repairs and creator IDs

> Up: [Wikidata Studio](README.md) · Rules W-328, W-338, W-344, W-345

`publication/historical_repairs.py` projects an exact repair plan into the existing Publication module.
The plan retains the full public before snapshots, exact proposals, source proofs, run, actor, and authorized QIDs.
`execute_gila_repairs.py --apply` uses the SQL Publication repository.
Old plans without full before snapshots require a new preflight.
The executor never uses a new item or a foreign update action.

The normal module retains the immutable release, approval, plan, intent, checkpoint, and result receipt.
The CLI resumes queued or paused executions through `ResumeCommand`.
It does not create a second retry queue or a new job kind.
Each send checks the current source, exact QID, creator proof, and remote revision again.
Each repair uses `baserevid` and sends only the exact changed claims.
Each success requires a full public read with the expected entity and response revision.
The completion receipt also checks that the current entity fingerprint matches the durable successful intent.

`converter/wikidata/retry_policy.py` defines `BULK_MAXLAG = 5`.
It parses numeric and HTTP-date `Retry-After` values.
The Publication gateway retains the delay in `WriteOutcome.retry_after_seconds`.
The core adds a bounded exponential delay and jitter without shortening the server delay.
The SQL repository commits the intent result and `ExecutionAction.next_attempt_at` together.
The claim query excludes a future retry date, including after a process restart.
An early CLI resume leaves that action paused.

Only a confirmed API rejection can enter a delayed retry.
A timeout or lost response remains `outcome_unknown`.
The repair gateway recovers success only from the exact expected full public entity.
An unchanged or unrelated public entity does not authorize a blind resend.
This rule also applies after a worker interruption.

The final uploader guard reads the current authenticated user ID and the first revision's user ID.
The guard requires the same positive integer IDs, canonical name, exact page, and visible first revision.
A cached creator name or ownership flag cannot replace this proof.
The owner-only preview also checks the first revision's user ID.
The historical Gila queue additionally requires `Alexander Goldberg IL`, user ID `6935067`.
Generic product writes retain the existing creator-only policy for the acting account.
No foreign approval, identifier match, or source choice grants ownership.

Tests cover delayed eligibility in the SQL repository, early resume, lost responses, foreign targets, hidden creators, and user-ID mismatches.
The main suites are `test_publication_historical_repairs.py`, `test_publication_repository.py`, and `test_wikidata_creator_uid.py`.
The [MediaWiki maxlag guidance](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter) defines the bulk request policy.

The audit service maps `pre_send_retryable` to `repair_retry` in the 16-character `operation` column (W-333).
The JSON receipt retains the complete status and retry metadata.
The transport retains the API rejection code in its error text.
An audit failure does not cancel a durable intent or authorize a new plan.
Resume the same execution after its retry date.
`backend/tests/test_wikibase_audit.py` checks the column limit and the unchanged receipt.

The Studio uses `GET /api/runs/{runId}/wikidata-publications/latest`.
That route selects only the `mhm-wikidata` profile (W-334).
An operator repair retains its separate `gila-exact-repair` or `gila-foreground-reference` Publication and execution.
The Studio cannot select that repair as its normal Release.
`backend/tests/test_publication_router.py` checks a newer repair and a run with repairs only.

## Automatic continuation (W-338)

`publication/repair_retry_runner.py` adds a bounded wait loop around the existing Publication resume command.
The CLI enables this loop with `--apply --wait-seconds 1800`.
Without `--wait-seconds`, the CLI makes one advance and returns the current receipt.
The limit must be finite and non-negative.
The option requires `--apply` when its value exceeds zero.

The loop copies the reviewed plan once and retains its Publication and execution.
The SQL adapter restores the retry date from the matching execution action and attempt.
The action state must also match the latest intent state.
It accepts only confirmed `pre_send_retryable` intents with complete timezone-aware retry dates.
It waits for a future date and resumes an already-due action within the finite budget.
A slow commit can finish after the saved date; that date remains valid.
It stops for a blocked action, an unknown outcome, or absent retry metadata.
It never replaces a date with a guessed shorter delay.
Each resume repeats the existing source, creator, revision, and exact-claim checks.
The exact repair scope still requires creator user ID `6935067`.
The loop does not change `maxlag=5`, the transport, or the guarded write path.
The [foreground mode](foreground-reference-repair.md) uses a separate profile and cannot enter this retry runner.

The loop commits the repository checkpoint before each wait.
It releases the SQL connection and sleeps for at most 30 seconds per step.
The CLI prints `retry_wait`, the execution ID, and the saved next retry date.
A monotonic clock bounds the process time between Publication advances.
The loop returns the saved pause if the next retry exceeds the time budget or that budget has expired.
An API call that already started can finish after the budget.
A process stop retains the durable intent for a later resume.
This foreground command creates no daemon, schedule, route, or job kind.

`test_publication_historical_repairs.py` covers execution reuse, deadline limits, cancellation, changed proof, due dates after slow commits, elapsed budgets, and unknown outcomes.
`test_gila_repair_cli.py` checks the CLI limits before authentication.

The exact gateway retains bounded lag evidence through `pipeline/wikidata_retry_diagnostics.py` (W-345).
A versioned JSON envelope uses the existing intent `detail` field and needs no migration.
`RepairRetryWait.diagnostics` restores the code, UTC time, host, type, scaled lag, raw lag, and retry delay.
The [sequential queue](reference-repair-queue.md) exposes this evidence as `service_delay` and retains its exact checkpoint after a finite wait limit.

## Recovery checks (W-347)

The backend checks again after 30 seconds if the reported lag is above 5 and at most 6.
It uses 60 seconds for all other probe results and honors a longer `Retry-After`.
A [regression test](../../../../backend/tests/unit/test_publication_historical_repairs.py) checks a 25-second recovery window, high lag, and a longer delay.
