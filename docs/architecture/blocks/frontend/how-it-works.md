# Frontend — How it works

> Up: [Frontend](README.md)

**Routing.** `App.tsx` is a single flat `<Routes>` table — no nested route
config, no lazy-loading indirection. Public pages (`/login`,
`/request-access`, `/privacy`, …) are bare; everything else is wrapped in
`<ProtectedRoute>` which gates on `useAuth`. Run-scoped pages live under
`/runs/:runId/{overview,extraction,rdf,wikidata-studio,hmo-studio,linked-data-explorer}`;
project pages under `/projects/:projectId{,/history,/entity}`; admin under
`/admin/*`. Adding a page = one file in `src/routes/` + one line in `App.tsx`.
**New Workflow.** The `RunOverview` page provides a comprehensive overview of the run status and next steps for professors.

**Wikidata Studio workflow.** The modern review surface defaults to the
reviewed HMO Wikibase read-back and carries the selected source through build,
refresh, verification, and upload-job parameters. The page presents review,
approval, preview, and public publication as separate steps; technical legacy
projection remains available as an explicit alternate source. The item drawer
exposes a QID-bound **accept foreign modify** checkbox when `existing_qid` is
set (Rule W-99); uploads load those accepts from `WikidataItemOverride`.

**API clients.** All HTTP goes through `api/client.ts:138`'s `api` object:
same-origin `/api` prefix, `credentials: "include"` (HTTP-only session
cookie), `cache: "no-store"`, and a CSRF double-submit header
(`X-CSRF-Token` echoing the `mhm_csrf` cookie) on non-GET/HEAD. Non-JSON
200s (the SPA-fallback pathology) hard-redirect to `/login`
(`client.ts:68-82`). Call sites that must bypass the wrapper (FormData
uploads, SSE) use `csrfHeaders(method)` (`client.ts:155`). Each resource
gets one file in `src/api/` exporting types plus a namespace const
(`export const RunJobs = {...}` at `api/runJobs.ts:94`). React Query is
available (provider in `main.tsx`) but most data flows use the bespoke
polling hooks below.

**State: Zustand + selector discipline.** Three global stores
(`auth`, `runJobs`, `theme`). After three production blank-UI incidents
(React error #185 infinite re-render), the rule is: selectors return
**primitives or stable references only** — `useRunJobs((s) => s.jobs)`,
`useAuth((s) => s.user?.id)`. `runJobs.ts:23-26` explicitly marks
`jobForRun()` / `activeJobs()` as "imperative getter only — do not use
inside `useRunJobs(selector)`" because each call allocates a fresh
reference. Derivation happens outside the selector:
`selectActiveJob(jobsRecord, runId, kind)` inside `useMemo`
(`useRunJobAttachment.ts:27-30`), change detection via
`jobFingerprint(job)` (`renderStable.ts:9`).

**Stable callbacks and child→parent reporting.** Tables never do bare
`useEffect(() => onChange(newArray))` — they use `useReportDerivedIds` /
`useReportFilteredEntities`, which fingerprint the id list
(`idsFingerprint`) and only fire the callback when membership actually
changes. Callbacks handed into hooks/effects are wrapped in
`useLatestRef` (`renderStable.ts:17`) so the effect never re-runs just
because a parent re-rendered with a new arrow function. `EMPTY_SET` exists
so fallbacks never inline `new Set()`.

**Polling conventions.** Two families:

- *Job polling* — `useRunJobs.ensurePolling()` starts a shared 2 s
  `setInterval` over `GET /jobs/mine`, self-stopping when no jobs remain.
  A monotonic `refreshSeq` counter (`runJobs.ts:50`) discards out-of-order
  responses so progress never jumps backward. `upsertJob` merges push
  updates without becoming a new source of truth. `JobTray` renders at
  `z-[60]` so it stays above verify modals (`z-50`). Pages attach via
  `useRunJobAttachment(runId, kind, sync)` (attach-on-mount to jobs started
  elsewhere/earlier, plus a targeted per-job 2 s poll) or `useVerifyJob`
  for verify modals (which also load the SSE session, pass optional
  `tier_model`, tolerate early 404s on `trace.jsonl`, and rolls back its
  optimistic running state if the enqueue request rejects).
- *Entity polling* — `useApprovalStore(runId, {active})` polls
  `/extraction/entities` every 2 s while a verify modal is open
  (`active=true`) and every 30 s otherwise, with ETag/304 revalidation, a
  user-scoped browser cache tier, and a content fingerprint so React state
  only updates on real diffs. Any writer that mutates entities dispatches
  the `mhm.entities.refreshed` CustomEvent
  (`cache/extractionCache.ts:20,114`); every mounted store instance
  force-refetches on it, so tables update without prop-drilling.

**Glass design system.** `LiquidGlassCanvas` (mounted exactly once in
`main.tsx`) is a full-screen react-three-fiber canvas of refractive orbs at
z-0 with `pointer-events: none`; it no-ops under `prefers-reduced-motion`
or `?glass=off`. Every frosted surface is `<Glass>` (variants `panel` |
`drawer` | `modal` | `compact` | `pill` — `Glass.tsx:52`) or `<GlassPill>`,
both thin wrappers over `LiquidGlassSurface`, which does SVG
`feDisplacementMap` backdrop refraction on Chromium and falls back to CSS
frosted glass on Safari/Firefox. Displacement maps are memoised in
`glassMapCache.ts`. Raw `className="glass"` / `glass-pill` are deprecated
legacy aliases; form fields keep the plain-CSS `input-glass`.

**Build tooling.** Vite 5 + `@vitejs/plugin-react`; `@` aliases `src/`;
dev server proxies `/api` to the FastAPI backend on :8000 (production
serves both from one origin — no CORS). `yarn build` runs `tsc --noEmit`
first (strict mode, `noUnusedLocals`/`noUnusedParameters`). Package
manager is yarn, never npm.
