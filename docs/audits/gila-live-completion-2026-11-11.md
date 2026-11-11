# Gila operations and UI receipt

The UI fix is live in Heroku release v792 at `394e6705`.
The first payload fix shipped in v790 at `e0e831eb`.
The production frontend passed TypeScript and Vite 5.4.21.
The latest Chrome reload shows all 2,409 review items and an enabled preview button.
The page shows no JSON parse error.
The owner history summary has 118,911 bytes for 52 jobs.
The previous complete response had 15,813,287 bytes.
The API client now includes JSON decode in one bounded GET retry budget.
A write still has one attempt.
The final warm-page fix also avoids a complete corpus read for each page.
Its SQL regression and related checks passed 25 tests.

The deployment at v789 applied migration `0053_extraction_rejection`.
The Modal runner received the same committed backend.
The health and readiness checks passed after deployment.

## Verified live repairs

The exact executor corrected 48 owned manuscript items.
It removed 13 duplicate colophons and 558 exact duplicate reference blocks.
Each write has a durable exact intent and a complete readback receipt.
No write has an uncertain outcome.
The [verified receipt](data/gila-verified-repairs-2026-11-11.csv) records each before and after revision.
The ownership guard blocked [14 foreign-owned items](data/gila-blocked-repairs-2026-11-11.csv).
The executor made no edit to those items.
Three work items remain in source review: Q141175543, Q141755231, and Q141755236.
A repair receipt covers its exact changes; it does not certify every fact on that entity.

## Fresh source check

The first production rebuild stopped on 12 canonical works without author claims.
The new source check reproduces each complete local work URI from current MARC title and author.
It uses the exact title transforms from the graph producer.
The adapter retains each distinct source author and its source records.
The local full build now passes the same export quality gate as production.
The canonical salt is `hmo-wikidata-v23`.

The complete local check covers 897 records and 2,409 proposed entities.
It reports zero validator-error items and zero unexplained losses across 10,518 source facts.
It retains 3,637 explicit review facts.
Those facts include unresolved identities, source-only evidence, and historical live gaps.
The local check does not mark these facts as corrected.
The live fact snapshot retains its original audit time.
The separate write receipts prove the 48 exact repairs.

## Desktop port and tests

The desktop port preserves author and folio occurrences, source wording, script terms, and catalogue semantics.
It preserves the separate edits that already existed in the desktop checkout.
The desktop port remains uncommitted with those separate edits.
Its design documents and source contract describe the new behavior.
The focused desktop check passed 21 tests.
The broader desktop safety suite passed 528 tests and skipped two.
Its nine failures also fail in the pre-port source snapshot.

The web unit suite passed 3,479 tests and has seven pre-existing failures.
An isolated committed snapshot reproduces all seven failures.
The new canonical, context, and related source checks passed 140 tests.
The UI checks passed nine frontend tests and 15 backend job tests.
The frontend unit check used cached Vitest 4.1.7.
Heroku verified the manifest's production frontend dependencies.

## Remaining operations

Heroku release v792 uses `394e6705`.
The Modal runner contains the source fix and the repair executor.
The production frontend build and health checks passed.
The approved-items rebuild completed with 2,409 items.
The first All matches rebuild stopped on a database pool timeout.
The separate retry completed with 2,409 items.
Both cache records have a fresh timestamp and a source digest.
The [page receipt](data/gila-ui-read-verification-2026-11-11.json) verifies both scope modes.
Each mode returned 2,409 unique items across 25 pages without a JSON decode error.
The largest page had 251,321 bytes.
The warm API probe returned 100 rows per page in 2.08, 1.64, and 1.58 seconds.
The Mac initially blocked the final screenshot because it was locked.
After the Mac unlocked, a fresh Chrome check confirmed the current table and enabled controls.
The screenshot is `state/gila-live-audit-2026-11-11/ui-v792-chrome-verified.png`.
A complete public snapshot refresh stopped on HTTP 429.
The audit retains its original snapshot time and the exact repair receipts.
Retain foreign targets and unresolved work identities for explicit curator review.
The ledger conflict is explicit: Q134926101 and Q141754492 both have P3959 `990001680850205171`.
A fresh live read confirms both targets and no redirect.
No ledger repair occurred. The curator must resolve that duplicate identity.
