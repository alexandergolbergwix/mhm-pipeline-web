# Gila release inputs, 14 November 2026

The user requested one push, the application deployment, and full verification after deployment.
This document records the reviewed release inputs; it does not assert a successful remote release.
The operation receipt is `state/gila-release-2026-11-14/release.json`.

## Scope

- Push the Gila repair branch `codex/gila-code-repair` to its configured GitHub origin.
- Deploy that commit to Heroku app `mhm-pipeline-web` through the normal build and release phases.
- Deploy the same committed backend through `modal/modal_jobs.py`.
- Verify health, migration state, build versions, current source proposals, and fresh public revisions.

The changes include creator-only guards, reference normalization, source-specific choices, original MARC occurrences, bibliography, durable repairs, and their tests.
Migration `0055_source_review_choices` follows `0054_person_identity_choice`.
The build versions are `source-aware-works-v24` and `hmo-wikidata-v28`.
The public Gila repair scope remains restricted to `Alexander Goldberg IL`, user ID `6935067`.
An application deployment does not authorize a foreign Wikidata edit.

## Pre-deploy checks

The reviewed backend paths match the [acceptance receipt](gila-acceptance-2026-11-14.md).
The full backend result is 4,618 passes, six skips, and nine warnings.
The source regression group passed 63 tests.
The frontend supplemental result remains separate from the full frontend release checks.
The local registry request still fails before an HTTP response.
The normal Heroku build must pass the strict frontend build before release.

The pre-deploy docs gate maps the complete staged change to these blocks:

- Wikidata Studio and Publication
- Extraction and original MARC evidence
- Frontend
- Versioning and export
- Job service
- Eval Agent
- RDF graph
- Deployment

The block docs name the new routes, source-choice field, migration, exact scope guards, retries, exports, and test fixtures.
The agent entry points use Rules W-1–W-332.
The change adds no second retry queue or job kind.

## Verification boundary

Remote operation results must come from the actual push, deploy, release output, and health checks.
Record failures and uncertain results explicitly.
Do not bypass a renewed application access refusal through secrets, database access, or another session.
Full Gila acceptance still needs current source evidence, current public reads, and exact correction receipts.
