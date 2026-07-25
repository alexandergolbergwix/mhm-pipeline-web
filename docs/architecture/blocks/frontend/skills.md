# Frontend — Skills

> Up: [Frontend](README.md)

### Skill: add a new page/route
1. Create `frontend/src/routes/MyPage.tsx` (default export).
2. Add one `<Route>` line in `App.tsx` — wrap in `<ProtectedRoute>` unless genuinely public.
3. Run-scoped pages take `useParams<{runId: string}>` and follow the `/runs/:runId/<section>` pattern.
4. Use `<Glass>` surfaces for panels; add an e2e spec + fixture routes for every interactive control (R8).

### Skill: add an API client
1. Create `frontend/src/api/<resource>.ts`: exported request/response interfaces + `export const MyResource = { list: () => api.get<...>(...), ... }`.
2. Import `api` (and `ApiError` if you branch on status) from `@/api/client` — never call `fetch` directly (R5).
3. For FormData/SSE, use raw fetch with `credentials: "include"` and spread `...csrfHeaders(method)`.
4. Add the mocked routes for it in the relevant `e2e/fixtures/*.ts` installer.

### Skill: attach a long-running job UI
1. Backend job kinds live in `api/runJobs.ts` (`RunJobKind`) — extend the union + `JOB_KIND_LABELS` first.
2. In the page: `const {activeJob, setTrackedJobId} = useRunJobAttachment(runId, "my_kind", sync)` where `sync` is `useCallback`-stable (or rely on the hook's internal `useLatestRef`). On start: `upsertJob(started); setTrackedJobId(started.id); ensureJobPolling()`. On succeed in `sync`, reload the review table (do not rely on active-only job polls alone — Rule W-108).
3. After POSTing the job-start endpoint, call `setTrackedJobId(job.id)` and `ensureJobPolling()`.
4. For verify modals specifically, use `useVerifyJob` (handles session loading + early-404 tolerance).
5. Never read `s.jobForRun(...)` inside a selector (R1); render progress from `activeJob.progress`.

### Skill: add an e2e spec
1. Pick or create the feature fixture in `e2e/fixtures/` — a `makeMockState()` builder plus an `installMocks(page, state)` that registers every `page.route()` the page will touch (including `/api/auth/me`, `/api/jobs/mine*`).
2. Mutating routes should record the request body onto `state` so the spec can assert the exact payload shape.
3. Spec skeleton: `installMocks` → `page.goto(...)` → drive clicks → assert DOM **and** captured calls.
4. Run with `cd frontend && yarn test:e2e` (`--ui` to step through). One-time: `npx playwright install chromium`.

### Skill: use the glass components
```tsx
import {Glass, GlassPill} from "@/components/glass";

<Glass as="section" className="p-6 space-y-4">…</Glass>          // panel (default)
<Glass variant="drawer" className="fixed right-0 …">…</Glass>     // slide-in drawer
<Glass variant="modal" role="dialog" aria-modal="true">…</Glass>
<GlassPill className="px-3 py-1 text-xs">42 approved</GlassPill>
```
Variants set radius/bezel/thickness presets (`Glass.tsx:52`); override per-prop only when a preset genuinely doesn't fit. Form inputs keep `input-glass` (plain CSS). Pass `refraction={false}` to skip the SVG displacement map on perf-sensitive lists.

### Skill: add a fast/slow-poll entity surface
Follow `useApprovalStore`: content fingerprint before `setState`, ETag/If-None-Match revalidation, `active ? 2000 : 30000` interval via chained `setTimeout` (not `setInterval`), an `inFlight` ref for backpressure, and a `window.addEventListener` on the feature's refresh CustomEvent.

### Skill: distinguish HMO and public QIDs

HMO Studio tables label project identifiers as `Wikibase QID (local)` and show
Wikidata/VIAF links in a separate authority column. Keep these namespaces
separate in filters, exports, and curator explanations.

In HMO Studio, link mapped items to the project Wikibase `/wiki/Item:Q…` URL.
Display ontology source URIs as metadata only; they may resolve to repository or
Git-LFS content rather than a live entity.
