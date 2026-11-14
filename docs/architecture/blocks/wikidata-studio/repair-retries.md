# Durable exact repairs and creator IDs

> Up: [Wikidata Studio](README.md) · Rule W-328

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
An operator repair retains its separate `gila-exact-repair` Publication and execution.
The Studio cannot select that repair as its normal Release.
`backend/tests/test_publication_router.py` checks a newer repair and a run with repairs only.
