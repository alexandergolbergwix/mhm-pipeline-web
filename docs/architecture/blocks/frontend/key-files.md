# Frontend — Key files

> Up: [Frontend](README.md)

| File | Purpose |
|---|---|
| `frontend/src/main.tsx` | Entry: QueryClientProvider, BrowserRouter, `initTheme()`, mounts `LiquidGlassCanvas` once behind the UI |
| `frontend/src/App.tsx` | Flat route table — public routes + `<ProtectedRoute>`-wrapped authenticated routes; calls `useAuth((s) => s.bootstrap)` on mount |
| `frontend/src/routes/` | One page component per route (`RunDetail`, `StageExtraction`, `StageRdf`, `HmoStudio`, `WikidataStudio`, `LinkedDataExplorer`, `admin/*`, …) |
| `frontend/src/api/client.ts` | The only fetch wrapper: `api.get/post/put/patch/del`, `ApiError`, CSRF double-submit header, HTML-fallback → login redirect |
| `frontend/src/api/*.ts` | One typed client per backend resource, exported as a namespace object (`RunJobs`, `Extraction`, …) |
| `frontend/src/stores/auth.ts` | `useAuth` Zustand store — user, bootstrap/login/logout |
| `frontend/src/stores/runJobs.ts` | `useRunJobs` Zustand store — job map, 2 s poll loop, monotonic `refreshSeq` guard, `selectActiveJob` helper |
| `frontend/src/stores/theme.ts` | `useTheme` — color scheme, `initTheme()` |
| `frontend/src/hooks/useApprovalStore.ts` | Polling entity store for AI Extraction (fast/slow poll + ETag + `mhm.entities.refreshed` listener) |
| `frontend/src/hooks/useRunJobAttachment.ts` | Attach a page to an in-flight background job (attach-on-mount, fingerprint-guarded sync) |
| `frontend/src/hooks/useVerifyJob.ts` | Verify job lifecycle (four verify kinds, incl. `hmo_item_verify`): poll/attach, hydrate from `progress.session_snapshot` |
| `frontend/src/utils/fetchVerifySession.ts` | Job-row snapshot fallback for multi-dyno session hydration |
| `frontend/src/hooks/useReportDerivedIds.ts` | `useReportDerivedIds` / `useReportFilteredEntities` — fingerprint-guarded child→parent reporting |
| `frontend/src/utils/renderStable.ts` | `jobFingerprint`, `idsFingerprint`, `useLatestRef`, `EMPTY_SET` — the render-stability toolkit |
| `frontend/src/cache/extractionCache.ts` | User-scoped SWR entity cache + `EXTRACTION_ENTITIES_REFRESH_EVENT = "mhm.entities.refreshed"` |
| `frontend/src/components/glass/` | Design system: `Glass`, `GlassPill`, `LiquidGlassCanvas`, `LiquidGlassSurface`, `liquidGlassMath`, `glassMapCache` |
| `frontend/src/components/extraction/`, `authority/`, `wikidata/`, `hmo/` | Feature component families (Rule W-16 / W-31 review surfaces) |
| `frontend/e2e/fixtures/*.ts` | Deterministic `page.route()` backend mocks per feature |
| `frontend/vite.config.ts` | `@` → `src/` alias; dev proxy `/api` → `localhost:8000` |
| `frontend/vitest.config.ts` | jsdom env, `tests/setup.ts` shims, excludes `e2e/**` |
| `frontend/playwright.config.ts` | Chromium-only, auto-starts Vite dev server, 3 local / 2 CI workers |
| `docs/testing.md` | Canonical three-layer test-pyramid documentation |
