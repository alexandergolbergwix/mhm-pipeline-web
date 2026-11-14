# Gila exact reference repairs, 14 November 2026

The reference preview is ready. Wikidata currently defers the canary repair because of `maxlag`.
The command confirmed no public edit and no foreign edit.
The [release report](gila-release-verification-2026-11-14.md) describes Heroku v800 and the initial audit.

## Fresh source and Publication checks

The normal Studio rebuild succeeded with 2,409 items.
A page refresh removed the old source freshness timeout.
The freshness gate retains its original checks and timeout.

A new normal Release stopped at the duplicate-work identity gate.
Two local works, `QDraft_Work_439` and `QDraft_Work_471`, share `Q2740944`.
Their Hebrew labels use different forms: `תיקון חצות` and `תקון חצות`.
The identity case retains both proposals and their source records for review.
This check cannot prove identity from those labels alone.

## Exact reference preview

The first corpus counter found duplicate references on 1,603 Alexander-created items.
The exact source and target checks retained this scope:

| Scope | Items | Claims | Repeated blocks |
|---|---:|---:|---:|
| Source-bound preview | 1,591 | 16,328 | 44,696 |
| Redirect or absent exact proposal | 3 | — | — |
| Absent or outside-run source proof | 9 | — | — |
| Foreign items excluded | 17 | — | 457 |

The preview changes reference arrays only.
It preserves claim values, ranks, qualifiers, and distinct source identities.
Each live preflight obtains fresh source, revision, creator, and bot-exclusion proof.
The historical scope requires creator user ID `6935067`.
The normal Publication module retains the immutable plan, action, intent, and retry date.

## Canary and external delay

The canary is `Q141747170`: 24 claims contain 66 repeated reference blocks.
The authenticated preflight passed.
Wikidata rejected each attempted send before an edit with code `maxlag`.
The command resumed the same durable execution and retained the five-second bulk limit.
Eight delayed resume attempts left the execution paused.
A further attempt at 08:53 UTC received the same confirmed rejection before an edit.

At 08:22 UTC, a fresh read reported revision `2568087865`, equal to the before revision.
The full entity still matched the before entity.
Read-only POST probes reported 20.5, 11.7, and 18.65 seconds of query-service lag.
At 09:06 UTC, another POST reported 13.3 seconds, above the five-second bulk limit.
The last probe returned `Retry-After: 5`.
The remaining 1,590 preview items received no write attempt.
At 08:54 UTC, fresh reads found no reference or description repair candidates for the three user examples.
Their revisions were `Q141175529`: `2568311986`, `Q141747600`: `2568314383`, and `Q141953242`: `2568312787`.
Both reported title claims now have one reference each.
The person description no longer repeats manuscript roles and record IDs.
The canary still had 66 repeated blocks at revision `2568087865`.

The paused Publication is `76a05f17-1d76-4750-9731-2c38e2eedc7b`.
Its execution is `f7a22e54-0990-4e4c-a8b3-06413d4ad484`.
Resume that execution after the service delay; an unknown outcome cannot authorize a new send.

## Code corrections

The audit tried to store the 18-character `pre_send_retryable` state in a 16-character operation column.
Rule W-333 maps that operation to `repair_retry` and retains the full JSON receipt.
The transport now retains the API rejection code in its error text.
A regression test reproduced the database contract failure before the fix.
The real deferred retry audit then succeeded in Postgres.

The Studio `latest` query also selected the operator repair as a normal Publication.
Rule W-334 restricts that query to the `mhm-wikidata` profile.
Its route test reproduced the wrong Publication selection before the fix.
The repair history remains intact.

The browser fixtures now retain Vite source requests, mock current reads, and pass unmatched requests to earlier mocks.
The drawer tests check four legacy approval cases separately and assert the test-wiki push target.
The Publication tests use the current default deferral option.
Functional browser tests request reduced motion; a two-test probe passed in 13.3 seconds.
The earlier single drawer check took 52.4 seconds.

## Checks and limits

The final backend suite passed 4,620 tests, with six skips and nine warnings.
The focused route, audit, transport, and retry group passed 67 tests.
The transport and audit files passed Ruff.
The frontend build and all 241 unit tests passed.
All eight Publication browser tests passed in a serial run.
The upload panel passed seven tests; the corrected live-submit check passed separately.
The other 46 Wikidata browser checks passed in 17.3 minutes. All 62 current cases passed across the scoped runs.
The original whole frontend browser suite exceeded its time limit and has no complete pass.

Eight unchecked cases lack a resolved public QID or require source identity and catalogue-status review. Other source review cases and unchecked passages remain open.
These code fixes do not establish complete Gila acceptance.
The follow-up code still requires a new push and deployment.

## Evidence

The exact preview, excluded cases, immutable canary plan, receipts, and fresh read are under `state/gila-exact-reference-repairs-2026-11-14/`.
The browser results and the original completion packet are under `state/gila-release-2026-11-14/`.
The operator uses the existing configured Publication resolver; credentials remain in process memory.
