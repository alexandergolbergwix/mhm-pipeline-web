# Frontend — Key files

> Up: [Frontend](README.md)

| File | Purpose |
|---|---|
| `frontend/src/main.tsx` | Entry: QueryClientProvider, BrowserRouter, `initTheme()`, mounts `LiquidGlassCanvas` once behind the UI |
| `frontend/src/App.tsx` | Flat route table — public routes + `<ProtectedRoute>`-wrapped authenticated routes; calls `useAuth((s) => s.bootstrap)` on mount |
| `frontend/src/routes/` | One page component per route (`RunDetail` immediate redirect to HMO Studio, `StageExtraction`, `StageRdf`, `HmoStudio`, `WikidataStudio`, `LinkedDataExplorer`, `admin/*`, …) |
| `frontend/src/routes/RunOverview.tsx` | Professor-facing run landing page: truthful stage status, recommended next step, workflow grouping, and separate research tools |
| `frontend/src/api/client.ts` | The only fetch wrapper: `api.get/post/put/patch/del`, `ApiError`, CSRF double-submit header, HTML-fallback → login redirect |
| `frontend/src/api/*.ts` | One typed client per backend resource, exported as a namespace object (`RunJobs`, `Extraction`, …) |
| `frontend/src/stores/auth.ts` | `useAuth` Zustand store — user, bootstrap/login/logout |
| `frontend/src/stores/runJobs.ts` | `useRunJobs` Zustand store — job map, 2 s poll loop, monotonic `refreshSeq` guard, merge-not-replace active poll (keeps terminal snapshots), `selectActiveJob` helper |
| `frontend/src/stores/theme.ts` | `useTheme` — color scheme, `initTheme()` |
| `frontend/src/hooks/useApprovalStore.ts` | Polling entity store for AI Extraction (fast/slow poll + ETag + `mhm.entities.refreshed` listener) |
| `frontend/src/hooks/useRunJobAttachment.ts` | Attach a page to an in-flight background job (attach-on-mount, fingerprint-guarded sync) |
| `frontend/src/components/jobs/JobProgressInline.tsx` | Outer + nested progress bars for build/verify/upload jobs (Rules W-106 / W-113) |
| `frontend/src/components/jobs/JobTray.tsx` | Global tray; nested sub-bar when `sub_total > 0` (W-113); `wikidata_upload` uses `WikidataUploadSteps` for every target |
| `frontend/src/hooks/useVerifyJob.ts` | Verify-job lifecycle: attach/poll/session hydration, global-tray upsert, rollback on enqueue reject, and Continue from resumable interrupt (W-130) |
| `frontend/src/utils/verifyResume.ts` | `resumeOfferFromJob` / Continue button label (Rule W-130) |
| `frontend/src/api/judgeModels.ts` | `GET /api/judge-models` — tier-1 judge list for verify modals |
| `frontend/src/components/Tier1ModelSelect.tsx` | Shared tier-1 judge dropdown + `useTier1Model` hook |
| `frontend/src/utils/fetchVerifySession.ts` | Job-row snapshot fallback for multi-dyno session hydration |
| `frontend/src/hooks/useReportDerivedIds.ts` | `useReportDerivedIds` / `useReportFilteredEntities` — fingerprint-guarded child→parent reporting |
| `frontend/src/utils/renderStable.ts` | `jobFingerprint`, `idsFingerprint`, `useLatestRef`, `EMPTY_SET` — the render-stability toolkit |
| `frontend/src/utils/throttledProgressRefresh.ts` | Throttle silent mid-run reloads for bulk approve / verify |
| `frontend/src/utils/studioUploadProgress.ts` | Patch review-table rows from live upload `item_outcome` / `recent_item_outcomes` (no full-table flicker) |
| `frontend/src/cache/extractionCache.ts` | User-scoped SWR entity cache + `EXTRACTION_ENTITIES_REFRESH_EVENT = "mhm.entities.refreshed"` |
| `frontend/src/components/glass/` | Design system: `Glass`, `GlassPill`, `LiquidGlassCanvas`, `LiquidGlassSurface`, `liquidGlassMath`, `glassMapCache` |
| `frontend/src/components/extraction/`, `authority/`, `wikidata/`, `hmo/` | Feature component families (Rule W-16 / W-31 review surfaces) |
| `frontend/src/components/wikidata/WikidataItemDetailDrawer.tsx` | Studio item drawer: overrides, foreign-modify accept (W-99), push/reconcile, HMO Wikibase Item:Q link (W-122) |
| `frontend/src/components/wikidata/WikidataUploadSteps.tsx` | Two-step Wikidata upload bars (Now + ETA; Rule W-192) |
| `frontend/src/utils/formatJobEta.ts` | Shared remaining/elapsed time labels for job progress |
| `frontend/src/api/wikidataStudio.ts` | Typed Wikidata Studio client incl. `accept_foreign_modify` override fields |
| `frontend/src/api/publication.ts` | Typed Publication commands and cursor reads; a prepare response can contain a queued job before it contains a Release |
| `frontend/src/components/wikidata/WikidataPublicationPanel.tsx` | Owns the default test/live target choice, polls the prepare job, then exposes Release review and bounded Publication cursor pages; enables compatibility controls only after API unavailability |
| `frontend/src/components/hmo/HmoPublishConfirmationDialog.tsx` | Shared accessible confirmation gate before a single HMO entry is published or updated |
| `frontend/e2e/fixtures/*.ts` | Deterministic `page.route()` backend mocks per feature |
| `frontend/vite.config.ts` | `@` → `src/` alias; dev proxy `/api` → `localhost:8000` |
| `frontend/vitest.config.ts` | jsdom env, `tests/setup.ts` shims, excludes `e2e/**` |
| `frontend/playwright.config.ts` | Chromium-only, auto-starts Vite dev server, 3 local / 2 CI workers |
| `docs/testing.md` | Canonical three-layer test-pyramid documentation |

- [Publication dry-run jobs](../wikidata-studio/publication-dry-run-job.md) — asynchronous checks, progress, cancellation, and receipt refresh (W-218).
