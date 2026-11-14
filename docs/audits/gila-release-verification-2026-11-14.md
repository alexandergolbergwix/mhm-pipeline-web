# Gila release verification, 14 November 2026

The release succeeded. Full Gila acceptance remains incomplete.
The audit made no public Wikidata edits.
The [release inputs](gila-release-2026-11-14.md) describe the authorized scope.

## Release receipt

| Check | Result |
|---|---|
| Source commit | `c0b75cc8aae64bf54f63532494609b3e99bae254` |
| GitHub push | `codex/gila-code-repair` succeeded |
| Heroku | `mhm-pipeline-web`, release `v800`, succeeded |
| Frontend build | Strict TypeScript check and Vite build passed |
| Migration | `0055_source_review_choices (head)` |
| Application health | `/api/healthz` and `/api/readyz` returned HTTP 200 |
| Modal | `mhm-jobs` deployment succeeded; health reported no absent keys |
| Build versions | `source-aware-works-v24`, `hmo-wikidata-v28` |

## Automated checks

The backend suite passed 4,618 tests, with six skips and nine warnings.
The source regression group passed 63 tests.
The deployed frontend unit suite initially passed 239 tests and failed two assertions.
The two assertions described old contracts: the default deferral choice and reference-only reuse.
The suite passed all 241 tests after exact test-only overlays in an isolated dyno.
Those local test corrections do not change the deployed application.

The full browser suite uses Playwright 1.60.0 on the official supported Noble image.
It uses the release source and the actual packages from Heroku v800.
It uses mocked backend responses and no production credentials.
Its result remains pending.
Some tests for the retired Authority page already failed because their old controls no longer exist.
These failures prevent a full frontend pass.

## Source and public audit

The read-only source audit covered 897 records and produced 2,409 native proposal items.
It reported zero items with validation errors.
The normal Studio rebuild uses the wider canonical project projection; its count has a different scope.
The public audit read complete data and creator evidence for 2,167 items.
Alexander created 2,071 of these items; other accounts created 96.
The audit used Wikidata user ID `6935067` for Alexander.
The uploader must obtain fresh creator and revision proof before any later edit.

The independent duplicate counter agreed with the production selector on every claim.
It found 44,739 repeated reference blocks across 16,349 claims on 1,603 Alexander-created items.
It also found 457 repeated blocks on 17 items from other creators.
The project must preserve those foreign items.
The counter ignores a different valid retrieval day when the complete source identity matches.
It preserves distinct source metadata and uncertain retrieval values.

The current snapshots for `Q141175529`, `Q141747600`, and `Q141953242` have zero repeated reference blocks.
Other public items still require repair.
`Q141175530` still lacks the expected author link through `P50`.

## Remaining acceptance work

The completion packet reports 8,399 verified cases, 5,055 manual cases, 1,617 repair cases, and eight unchecked cases.
It also reports 38,497 unchecked source passages.
These counts overlap; they are not counts of distinct manuscripts.
One source record lacks a public target in the current ledger-backed audit.
The physical-manuscript/work model still requires an explicit scholarly decision.
The current project uses separate items; Gila requested both classes on one item.
Source review must resolve identity, role, bibliography, content, and exact page scope before acceptance.
Each public correction needs its exact source evidence and a current revision receipt.

The deployed UI exposes all nine requirement states and the source passages that lack proof.
It exposes no consent control that permits a foreign-item edit.
The normal Studio rebuild is active as job `140b0e5d-7a54-49d9-a3fb-808ce376f3cd`.
Its final cache and UI checks remain pending.
No upload or Publication action occurred.

## Local evidence

The operation receipt is `state/gila-release-2026-11-14/release.json`.
The completion packet is `state/gila-release-2026-11-14/acceptance/`.
The same directory contains the complete public snapshot, creator evidence, and duplicate cases.
The audit evidence stays outside Git because it contains large source records.
The local `watch_verification.py` process tracks the two active checks for up to two hours.
It updates `release.json` and writes `terminal-checks.json` after both checks end.
It has read-only database access and makes no public edits.
