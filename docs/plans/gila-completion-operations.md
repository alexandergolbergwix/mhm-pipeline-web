# Gila completion operations

The user authorized the remaining operations after the local implementation report.
The scope includes deployment, migration 0053, live repair review, verification, and the desktop port.
GitHub actions still require separate, specific permission.

## Current evidence

- The local backend unit suite passed 2,240 tests.
- The integration and cache suites passed 92 tests.
- The full source audit has zero unexplained losses across 897 records.
- The production health and readiness endpoints return success.
- The supplied site session has the admin role.
- The exact frontend build passed on Heroku with Node 20.20.2 and Vite 5.4.21.
- The deployed Heroku release is v791 at `d6536aef`.
- The Modal runner uses the same source fix.
- Production migration 0053 passed. The rejected field is non-null with a false default.
- The Modal job runner uses the same committed backend.
- The UI fix is live in v790. A fresh browser load shows all 2,414 review items without JSON errors.
- The preview summary has 118,911 bytes for 52 jobs, down from 15,813,287 bytes.
- All 48 eligible manuscript repairs passed complete readback checks.
- Fourteen foreign-owned manuscripts remain blocked.
- The first fresh production rebuild stopped on 12 canonical author gaps.
- The final local build resolves those gaps and passes the production export gate.
- The complete source check retains 10,518 facts with zero unexplained losses.

## Sequence

1. Verify the complete branch diff against the architecture documentation.
2. Create a local commit with the code, tests, and documentation.
3. Deploy that commit to Heroku.
4. Verify the frontend build, release migration, health, and authenticated source paths.
5. Deploy the same committed backend to the Modal job runner.
6. Refresh each historical proposal before its write.
7. Use the normal ownership guard and exact claim changes.
8. Persist write intent and outcome receipts.
9. Read each affected live entity again and compare the intended result.
10. Resolve the supported source review cases and retain evidence for unresolved identities.
11. Port the shared contracts to the desktop converter without replacement of its separate edits.

## Stop conditions

A failed build or release prevents deployment completion.
A stale revision, failed ownership check, unresolved identity, or uncertain write outcome prevents the affected live write.
A count of review cases does not authorize an invented identity or a new public work.
The site session values remain in process memory and do not enter files or logs.

## Pre-deploy documentation gate

The current diff maps to extraction, RDF, Wikidata Studio, frontend, export/history, and deployment.
The architecture pages describe the changed fields, source contracts, migration, and tests.
The final audit corrected the stale W-304 pointer in `global-rules.md`.
The root instructions and README now point to W-305.
