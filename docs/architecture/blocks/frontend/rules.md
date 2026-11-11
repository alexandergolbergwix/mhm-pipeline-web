# Frontend — Rules

> Up: [Frontend](README.md)

1. **R1 — Zustand selectors MUST return primitives or store-held references.** NEVER select a function call or a freshly built array/object (`s.jobForRun(...)`, `Object.values(s.jobs)`). *Why:* a new reference per render spins an infinite re-render loop — the React error #185 incidents (Rule W-36).
2. **R2 — Derive job/entity views with `selectActiveJob` inside `useMemo`, guarded by `jobFingerprint`.** *Why:* keeps derivation out of the selector while still updating only on real status/progress changes.
3. **R3 — Child→parent reporting MUST go through `useReportDerivedIds` / `useReportFilteredEntities`; callbacks fed to hook options MUST be `useCallback`-stable or wrapped in `useLatestRef`.** *Why:* an unguarded `useEffect` + `onChange(newArray)` pair is the other half of the #185 loop.
4. **R4 — Long-running job UI MUST use `useRunJobAttachment` or `useVerifyJob`; NEVER hand-roll `jobForRun` selectors plus overlapping poll effects.** *Why:* the hooks centralise attach-on-mount, fingerprint dedup, and poll teardown; ad-hoc copies regress all three.
5. **R5 — All HTTP goes through `api/client.ts` (or `csrfHeaders()` for raw fetch).** *Why:* it carries the session cookie, CSRF double-submit token, no-store cache policy, and the stale-HTML → login recovery; a bare `fetch` silently loses all four.
6. **R6 — New frosted surfaces MUST use `<Glass>` / `<GlassPill>`; NEVER add raw `className="glass"`, per-feature glass wrappers, or duplicate `LiquidGlassSurface` logic.** *Why:* Rule W-35 — one refraction implementation, one place to fix browser fallbacks.
7. **R7 — `LiquidGlassCanvas` is mounted exactly once (in `main.tsx`).** *Why:* it is a full-screen WebGL canvas; duplicates multiply GPU cost and z-index bugs.
8. **R8 — Every user-visible UI surface gets at least one Playwright spec that exercises the click path AND asserts the captured backend call shape.** *Why:* Rule W-19 — e2e is the canonical regression layer; unit tests are reserved for pure functions.
9. **R9 — E2E specs NEVER hit a real backend: all endpoints are mocked via `page.route()` fixture installers.** *Why:* determinism and isolation from Modal, Gemini, and the database.
10. **R10 — Entity mutations MUST be followed by dispatching `mhm.entities.refreshed` (via `extractionCache`), not by prop-drilling refresh callbacks.** *Why:* every mounted `useApprovalStore` refetches on the event, keeping all tables in sync for free.
11. **R11 — Applying poll responses MUST be guarded against reordering** (monotonic `refreshSeq` in `runJobs`, content fingerprints elsewhere). *Why:* out-of-order 2 s poll responses made job progress visibly jump backward (`runJobs.ts:43-50`).
12. **R12 — yarn only; `yarn build` runs strict `tsc --noEmit` first; no `any`, no unnecessary `as`.** *Why:* global project standard; typecheck is part of the build gate.
13. **R13 — Optimistic verify-job state MUST roll back when `RunJobs.start` rejects.** `useVerifyJob.start` MUST reset `running` and `jobId`, then rethrow so the modal can show its API error and offer a retry. *Why:* a Heroku H12 before `create_job` left the Wikidata modal showing **Stop** with no job, flow, or verdicts (Rule W-61).
14. **R14 — Job-backed upload/build panels MUST deliver a terminal snapshot to `sync` and refresh review tables without mid-run flicker (Rules W-108 / W-110).** `useRunJobAttachment` MUST keep the tracked job id through the final poll/`upsertJob`, and `jobFingerprint` MUST change when `result`/`error` lands. Live upload mid-run MUST patch only changed rows from `progress.recent_item_outcomes` / `item_outcome` (via `studioUploadProgress.ts`) — NEVER unmount the table with `setLoading(true)`. A row under write MUST show `upload_outcome=processing` (status may change from `processing` → terminal without requiring a new local_id). Terminal succeed/fail MAY do a silent full reload. Bulk approve / verify mid-run MAY silent-reload (throttled) but MUST keep the table mounted when rows are already present. Active AI-verify scope rows without an overall MUST show a judging pill. *Why:* full `load()` + `!loading` gates flickered the whole HMO/Wikidata review UI during publish.
15. **R15 — Job progress counters MUST carry an honest unit and message (Rules W-112 / W-113).** Phase pipelines (e.g. HMO item build) use 1-based `processed/total` with `unit` (`steps`) and a step message; long steps also carry `sub_processed`/`sub_total`/`sub_unit`/`sub_message`. The tray shows `N / M steps · n / m entities · message`, and `JobProgressInline` renders a nested bar when `sub_total > 0`. `wikidata_upload` jobs (dry-run, test, and live) render `WikidataUploadSteps` — two labeled bars, Now, and ETA — in the progress modal, the upload panel, and the job tray. When a job payload carries a `steps[]` plan (e.g. the HMO item upload), `JobProgressInline` and the job tray MUST render the shared per-step strip (`JobStepsStrip`), label the counter `(overall)`, and tooltip the step-local message so the two scales never read as one number (`wikidata_upload` keeps its own `WikidataUploadSteps` widget instead). While nested progress is active (`sub_total > 0`), the tray's label line MUST show the nested counters (`n / m unit`) and `sub_message` next to the frozen overall — never only the truncated message. The running step chip shows `current_label` when the phase names an inner pass, so the assembly step shows the pass name and the item count. *Why:* skip-cache rebuild showed `0/3` then a stuck-looking `1/3` during authority refresh; live writes previously showed only the generic nested bar while test.wikidata used the two-step UI; the HMO upload showed `18695/55623 item links added` next to `37207 / 74135` with no hint they measure different scopes.
16. **R16 — Incomplete verify UI MUST NOT invent an eval-agent error (Rule W-126).** When `outcome=partial` or `judged < total`, `useVerifyJob` reports an early-stop / incomplete-scope message (or the job's `runner_error`), never a generic “eval-agent error”. *Why:* job 341a976e had no `runner.error` yet the modal blamed eval-agent.
17. **R17 — Mid-run VerdictsTable may be empty while the counter advances (Rule W-128).** Hydrate from the slim terminal job snapshot; do not require live TRACE mid-run on the 512 MB dyno. *Why:* job ecfdcf29 showed VERDICTS (0) while 53/313 was already judged.

18. **R18 — Interrupted verify MUST offer Continue (Rule W-130).** `useVerifyJob` exposes `resumeOffer` / `continueFromPause` (forces `override_cache=false`); Wikidata / HMO / NER modals show **Continue verification (N/M done)** when the latest job is resumable. Resume discovery uses active jobs first, then `listForRun(kind, limit=5)` — never the full run job history. Verify modals MUST NOT call `onVerdictsLanded` / full table reload on mid-run progress ticks (W-131). *Why:* after dyno restart, curators had no path to finish the remaining scope; full `/jobs` list + Studio 500-page load R14'd the Basic dyno.
19. **R19 — Active job polls MUST NOT wipe terminal job snapshots from `useRunJobs`.** `refresh()` merges `listMine(active=true)` into the existing map; it never replaces the map with only active ids. Upload progress modals resolve `upload_target` from params/progress/result with a sticky fallback and MUST NOT default missing evidence to **live** (title + badge stay on test/dry-run). *Why:* after cancel/finish, the empty active poll deleted the job every 2 s and `resolveUploadTarget` fell through to live, flickering Test ↔ Live Wikidata in the modal.
20. **R20 — Live verdict rows MUST show complete metadata and a valid outcome (Rule W-210 / W-211).** `VerdictsTable` reads `judge_id`, `evaluator_id`, `confidence`, `cache_key`, and `judged_at` from the event envelope, with nested fallbacks for older snapshots. A provider-error row uses `abstain`, hides candidate confidence, and shows a retry instruction. A missing row uses `not verified`. *Why:* compact Heroku job snapshots showed valid rows with blank judge, time, and confidence fields, while provider failures appeared as `unknown` with candidate confidence `1.0`.
22. **R22 — Research chat MUST show a wait indicator while `busy` and stream text is empty.** Disable Send with a visible opacity change. *Why:* Modal AG-UI and tool calls can sit silent for tens of seconds; the log looked frozen after the user message.
23. **R23 — Research chat MUST map pydantic-ai `RUN_ERROR` strings to a curator sentence.** *Why:* Rule W-231 — `Exceeded maximum output retries (1)` is not a curator-facing message.
24. **R24 — A page that waits on a run job MUST poll until the job row is terminal.** `waitForRunJob` has no default deadline. A caller passes `timeoutMs` only for a bounded test. *Why:* the Wikidata Studio page reported "Background job timed out" after 20 minutes while job `e87334c3` was still running on Modal (2026-10-01).

25. **R25 — A studio-build conflict MUST keep the studio page on screen.** A 409 `studio_build_in_progress` or `studio_build_failed` shows the job progress on the review page. The page must not replace itself with the sentence "Wikidata Studio build is running in the background." *Why:* that sentence was the only content on the page while a build was still running (2026-10-01).
26. **R26 — Research reports preserve table cells and split panels shrink within the viewport (W-298).** Parse supported malformed pipe rows without losing their first cell. Use table view by default, and treat unsafe integers and digit strings with leading zeroes as text. *Why:* the canvas showed raw pipe text, numeric conversion could change control numbers, and a split panel could overflow.
27. **R27 — Owner-only preview is not a write or authorization (W-303).** Show
    job status, scope, item outcomes, and any deferred-connection outcomes.
    Do not patch the review table's upload status from preview progress. Label
    `would_update` as eligibility to attempt an update, not a guarantee.
    *Why:* preview results do not reserve or approve a later live write.
28. **R28 — The flat reference view does not establish source grouping (W-304).**
    The current `Snak` type and legacy statement view use `references`, not
    `reference_blocks`. The displayed count is a snak count, not a source count.
    *Why:* flattening loses the boundaries between distinct sources.
29. **R29 — A loading overlay MUST NOT block the screen longer than 8 seconds.**
    `LoadingOverlay` auto-hides after `AUTO_HIDE_AFTER_MS` (8 s) and calls
    `onDismiss`; the previously loaded state stays visible while the fetch or
    build continues, and progress remains in `JobProgressInline` and the job
    tray. This applies to every usage (Wikidata Studio items panel, the studio
    route's "Updating Wikidata items…" overlay, Stage RDF). The studio's
    first-load `LoadingPanel` in `WikidataStudio.tsx` follows the same cap and
    falls back to one status line plus inline job progress. *Why:* a studio
    build could cover the review screen with "Loading Wikidata items…" for
    minutes while the current state was already loaded behind it (2026-11-10).
30. **R30 — A running studio build MUST be tracked by the page that waits for
    it.** `loadStudioBuild` reports the 409-resolved job id through `onJob`;
    Wikidata Items (`conflictJobId`) and the studio route
    (`setStudioBuildTrackedId`) attach to it. While the build runs and the
    build has no items, the panel shows "Build in progress — records appear
    here when the build finishes" plus `JobProgressInline` — never a bare
    "0 items" table. *Why:* the 409-wait hid the job id from the panel, so a
    first build looked like an empty dataset after the overlay cap (2026-11-10).
31. **R31 — Visible studio items update in place while a build runs.** The
    items panel silently re-fetches at most once a minute on build progress
    ticks (`createThrottledProgressRefresh({minMs: 60_000, minDelta: 500})`)
    and swaps the rows in place — the table never unmounts (Rule R14). The
    build writes its cache row before the enrichment phases end, so the new
    data lands mid-run; previously loaded items stay visible until then.
    *Why:* a rebuild showed an empty "0 items" panel with no live update
    until the very end (2026-11-10).
32. **R32 — Studio list pages stay small (Rule W-131).** `fetchAllStudioItems`
    walks `wikidata-studio/items/page` in chunks of `STUDIO_MAX_PAGE_SIZE`
    (100) trimmed rows (`trim_studio_list_item` drops the item-level bulk
    fields the list never reads). Never raise the chunk size without
    measuring the serialized page. *Why:* 500-row chunks of untrimmed rows
    reached ~8 MB, the response truncated mid-JSON, and the review table
    showed "Unterminated string in JSON" with no data (2026-11-10).
33. **R33 — Preserve explicit extraction rejection (W-305).** The drawer and
    bulk Reject action transmit `rejected=true`. The entity fingerprint includes
    rejection. *Why:* a pending entity and a rejected entity require different review behavior.

34. **R34 — Preview restore and JSON retries remain bounded (W-306).**
    Request job summaries first, then one selected result.
    Check the returned detail against the current user and preview mode.
    Include body decode in the existing GET retry budget.
    Never repeat a write after an incomplete response.
    *Why:* a 15.8 MB job list accompanied the screenshot's JSON errors.

35. **R35 — Small responses also require bounded server reads (W-309).**
    A warm Studio page checks the cache ID without the complete cache payload.
    Only an initial backfill can read the full item blob.
    *Why:* the previous page initialization loaded the corpus for every chunk.
