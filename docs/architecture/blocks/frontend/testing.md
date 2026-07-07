# Frontend — Testing

> Up: [Frontend](README.md)

**Testing.** Three layers (`docs/testing.md`):

1. **Vitest + RTL** (`frontend/tests/unit/`, jsdom, `tests/setup.ts` shims
   ResizeObserver/matchMedia + auto-cleanup) — pure functions and single
   components: `renderStable.spec.ts`, `runJobsStore.spec.ts`,
   `liquidGlassMath.spec.ts`, `useReportDerivedIds.spec.ts`, ….
2. **Backend pytest** route/unit/safety suites (see backend blocks).
3. **Playwright e2e** (`frontend/e2e/`, chromium only, Vite dev server
   auto-started) — the canonical regression layer for the curator UI
   (Rule W-19). Every backend endpoint a page touches is mocked
   deterministically via `page.route()` in a per-feature fixture module:
   `installExtractionMocks(page, state)` (`e2e/fixtures/extraction-fixtures.ts:191`)
   registers ~20 routes over a mutable `MockState` built by
   `makeMockState()`, so specs assert both the click path **and** the
   captured request shape, with zero dependency on the DB, Modal, or
   Gemini. Coverage bias: every user-visible surface — chip filter, sort
   header, column popup, bulk action, modal, drawer — gets at least one
   e2e spec ("every click is a test", user directive 2026-06-01).

## Tests pinning this block

- `frontend/tests/unit/renderStable.spec.ts`, `useReportDerivedIds.spec.ts`, `runJobsStore.spec.ts` — R1–R3, R11.
- `frontend/tests/unit/liquidGlassMath.spec.ts`, `glassMapCache.spec.ts` — glass math + map memoisation.
- `frontend/tests/unit/clientCache.spec.ts`, `entityApi.test.ts`, `verifySession.spec.ts`, `waitForRunJob.spec.ts` — cache tiers, API mapping, job/session lifecycles.
- `frontend/e2e/extraction-review.spec.ts`, `authority-*.spec.ts`, `wikidata-studio.spec.ts`, `hmo-wikibase-items.spec.ts`, `history-timeline.spec.ts`, `stage-rdf.spec.ts`, `access-request.spec.ts`, `admin-panel.spec.ts`, `linked-data-explorer.spec.ts`, `smoke.spec.ts` — the R8/R9 click-path layer.
- `yarn typecheck` (strict tsc) gates every build.
