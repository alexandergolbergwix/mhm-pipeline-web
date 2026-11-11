# Gila operations and UI receipt

The UI fix is live in Heroku release v790 at `e0e831eb`.
The production frontend passed TypeScript and Vite 5.4.21.
A fresh Chrome reload shows all 2,414 review items and an enabled preview button.
The page shows no JSON parse error.
The owner history summary has 118,911 bytes for 52 jobs.
The previous complete response had 15,813,287 bytes.
The API client now includes JSON decode in one bounded GET retry budget.
A write still has one attempt.

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

Deploy the final source-boundary fix to Heroku and Modal.
Complete the fresh production rebuild and check its stored output.
Retain foreign targets and unresolved work identities for explicit curator review.
Do not infer a missing ledger mapping from a title.
