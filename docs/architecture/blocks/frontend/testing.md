# Frontend — Testing

> Up: [Frontend](README.md)

`frontend/tests/unit/extractionRejection.spec.ts` checks explicit Reject payloads and rejected-only changes on a poll.

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

- `frontend/tests/unit/renderStable.spec.ts`, `useReportDerivedIds.spec.ts`, `runJobsStore.spec.ts`, `wikidataUploadProgressModal.spec.ts`, `useVerifyJob.spec.ts`, `verifyResume.spec.ts`, `throttledProgressRefresh.spec.ts`, `studioUploadProgress.spec.ts`, `jobProgressInline.spec.tsx`, `jobTray.spec.tsx`, `waitForRunJob.spec.ts`, `canvasState.spec.ts`, `ResearchChat.spec.tsx` — R1–R4, R11, R13–R25 plus Research Assistant AG-UI canvas reducer, chat wait dots, and pydantic-ai retry humanize. A studio-build 409 keeps the job id from the response payload (R25).
- `frontend/e2e/wikidata-upload-panel.spec.ts` — compatibility dry-run/test
  radios and historic live two-step progress modal + job tray.
- `frontend/e2e/wikidata-owner-preview.spec.ts` — mocked job request, preview
  results, upload-status isolation, and missing-live-credential error (W-303).
  The synthetic item and connection outcomes do not assert backend eligibility.
- `frontend/e2e/wikidata-studio-theme.spec.ts` — light/dark theming of the
  Publication panel (no hardcoded dark surfaces, W-35), header theme toggle,
  and scheme persistence across reload.
- `frontend/e2e/wikidata-publication.spec.ts` — default Publication target
  controls do not render beside compatibility upload controls. It also checks
  that **Resolve N items with AI** requests `blocked` scope. That scope can
  also assess non-blocked UPDATE actions; see [automatic resolution](../wikidata-studio/automatic-resolution.md).
- `frontend/tests/unit/liquidGlassMath.spec.ts`, `glassMapCache.spec.ts` — glass math, `glassSafeInset` (R21), map memoisation.
- `frontend/tests/unit/clientCache.spec.ts`, `entityApi.test.ts`, `verifySession.spec.ts`, `waitForRunJob.spec.ts` — cache tiers, API mapping, job/session lifecycles; `waitForStudioBuild` progress streaming + studio-build 409 copy; a running job is polled past 20 minutes (R24).
- `frontend/e2e/extraction-review.spec.ts`, `authority-*.spec.ts`, `wikidata-studio.spec.ts`, `hmo-wikibase-items.spec.ts`, `history-timeline.spec.ts`, `stage-rdf.spec.ts`, `access-request.spec.ts`, `admin-panel.spec.ts`, `linked-data-explorer.spec.ts`, `smoke.spec.ts` — the R8/R9 click-path layer.
- `yarn typecheck` (strict tsc) gates every build.

The Publication option and deferred-claim view use
`tests/unit/wikidataPublicationPanel.spec.tsx` and
`e2e/wikidata-publication.spec.ts` (W-214).
The tests use HTTP substitutes and make no Wikidata writes.

`frontend/e2e/wikidata-complete-scope.spec.ts` checks complete review scopes (W-215).
It uses paginated HTTP fixtures with 236 and 736 items.
It checks AI filter counts, approval counts, and the item IDs in verification requests.

- `apiClientRetry.spec.ts` checks incomplete GET recovery, the shared four-attempt
  budget, and one POST attempt after an incomplete response (W-306).

`wikidataItemDataStatus.spec.ts` checks receipt states and count totals. `wikidataReviewRefresh.spec.ts` checks terminal jobs and scope exclusions. `wikidata-studio.spec.ts` checks readable upload filters and header counts (W-316).

`wikidataStudioPagination.spec.ts` checks that obsolete cursor walks stop and the current load retains all pages (W-318).

The upload-count E2E case uses reduced motion. The case tests table state and avoids an unrelated GPU canvas on a small test dyno.

The Wikidata fixture serves `/items/page` with cursor pages and server totals. The item-detail route must not catch that page endpoint.


The count unit test covers a current source-scoped person choice as reference only. A stale build remains unconfirmed. The cursor fixture serves `/items/page` with a proper cursor response.

The upload-state unit test also accepts the bounded table `reference_only` flag. The table omits the full authority evidence and private choice proof (W-321).

The Wikidata drawer and Publication specs reject historical foreign approvals and retain reference-only reuse (W-324).

`WikidataPublicationAiReview.tsx` cannot submit historical foreign consents; its report remains advisory (W-324).

Source review: `tests/unit/wikidataSourceReview.spec.tsx` checks exact proof requests and stale-source errors. `e2e/wikidata-source-review.spec.ts` checks the drawer flow and the absence of public writes. The shared Studio fixture supplies an empty source-review response for other drawer tests.

W-330: the source-review unit suite also checks a late save success and failure after a run change with the same local ID.
The [supplemental test receipt](../../../audits/gila-source-review-followup-2026-11-13.md) records the offline dependency limits.
`frontend/e2e/fixtures/wikidata-fixtures.ts` supplies the shared mocked Studio and source-review responses.
