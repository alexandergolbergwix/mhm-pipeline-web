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
