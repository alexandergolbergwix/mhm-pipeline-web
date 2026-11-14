# Gila source-review follow-up, 13 November 2026

This local follow-up extends the [implementation receipt](gila-code-implementation-2026-11-13.md).
The dependency probes confirmed that the shell could not connect to either package registry.
Node 18, Node 24, IPv4, and curl all failed before an HTTP response.
These results do not identify the external cause of the network restriction.

## Supplemental component checks

The temporary copy at `/tmp/gila-frontend-supplemental-20261113` uses local installed packages and Yarn.
The copy does not change the package manifest, lockfile, or another repository.
The tests use the actual source component, API client, setup, and Vitest configuration.
The first pass executed the source-review and Publication control suites: 14 tests passed.

| Package | Version |
|---|---|
| React and ReactDOM | 18.3.1 |
| Vitest | 2.1.9 |
| Vite | 5.4.21 |
| jsdom | 25.0.1 |
| React plugin | 4.7.0 |
| React Testing Library | 14.3.1 |

The project declares React Testing Library `^16.1.0`.
The local cache lacks that version and several packages for the full app.
These checks remain supplemental; they do not close the full frontend release gate.
The Publication suite emits React `act` warnings.

## Reproduced defect and fix

The same local item ID can occur in different runs.
The old source-review component did not isolate save callbacks when the run changed.
A late success called the obsolete save callback and requested an extra read.
A late failure showed the old error in the new run.
Two tests reproduced these exact outcomes before the fix.
Evidence: `/tmp/gila-source-scope-red.log`.

`WikidataSourceReview` now keys an inner view by both run and local item ID.
A mount guard stops save results from a closed view.
The new view starts with fresh form, notice, and busy state.
The existing guard still rejects late source reads.
The fix does not cancel a local transaction that the server accepted.
Rule W-330 defines this boundary.

## Final checks

- The two supplemental suites passed all 16 tests after the fix.
- The focused TypeScript 5.6.3 check passed against the actual React 18 declarations.
- `git diff --check` passed.
- The architecture gate covers the UI scope rule and regression tests.

The final test log is `/tmp/gila-source-scope-green.log`.
The type-check log is `/tmp/gila-source-scope-types.log`.
The local evidence receipt is `state/gila-implementation-2026-11-13/source-review-followup-receipt.json`.
The earlier backend result remains 2,880 passes with two skips; this follow-up changes no backend code.

## Open release requirements

The full frontend build, declared-dependency suite, and Playwright tests still need the missing packages.
Migration `0055_source_review_choices`, deployment, a rebuild, and source review remain open.
This follow-up made no public Wikidata edit, production migration, deployment, commit, or push.
The creator-only restriction and earlier application-origin refusal remain in force.

Trellis capture `63110b1d-cdac-449e-a921-8a069dec0562` proposes an improvement.
Its target is `doctorat/capture-d99b0063-308b-4e4b-88fd-47f71d392f9a`.
Review remains pending; the capture did not replace stable knowledge.
