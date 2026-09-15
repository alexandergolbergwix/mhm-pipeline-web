# Plan: Wikidata Studio light-mode fix + interface simplification

Status: implemented
Date: 2026-09-15
Repo: `mhm-pipeline-web`
Surface: `/runs/:runId/wikidata-studio` (modern review surface, the default)
Production example: run `48ba6c13-115c-4763-bff1-c08b9031b518`
(https://mhm-pipeline.org/runs/48ba6c13-115c-4763-bff1-c08b9031b518/wikidata-studio)

---

## 1. Problem report

Alexander reports two problems on the production page:

1. **The UI stays dark when the app is in light mode.** Parts of the
   Wikidata Studio page keep a near-black background after the curator
   switches the color scheme to Light.
2. **The UI is overcomplicated.** The page stacks four tool layers, hides
   the review table behind a collapsed section, and repeats the same
   counts and controls in several places.

## 2. Root-cause analysis (verified in code)

### 2.1 Why light mode still shows dark surfaces

The app themes everything through CSS custom properties on
`<html data-theme="…">` (`frontend/src/styles/index.css:7-110`, store:
`frontend/src/stores/theme.ts`). Light mode also carries remaps for
legacy Tailwind opacity utilities (`index.css:316-384`). Two Publication
components bypass that system with hardcoded dark colors:

| Offender | File:line | Class | Light-mode result |
|---|---|---|---|
| Publication panel root | `frontend/src/components/wikidata/WikidataPublicationPanel.tsx:259` | `bg-slate-950` | No remap exists for `bg-slate-950` → the whole Publication section stays near-black |
| Result-summary card | `frontend/src/components/wikidata/WikidataPublicationControls.tsx:139` | `bg-black/10` | No remap exists for `bg-black/10` (only `/15 /20 /30 /40 /60` are remapped) → dark card inside an already-dark panel |

This also breaks Rule W-35 ("Reusable glass components, never raw
hardcoded surfaces"; see `docs/architecture/rules/frontend-ui.md:109`):
the Publication panel root is a plain `<section>` with a hardcoded
background instead of `<Glass>` or a themed token.

Secondary findings from the same audit (cosmetic, fix in the same pass):

| Finding | File:line | Note |
|---|---|---|
| `text-accent` is not defined anywhere | `WikidataPublicationControls.tsx:223`, `WikidataPublicationAiReview.tsx:129` | Tailwind emits no rule → links render unstyled. Replace with `link-accent` (defined at `index.css:277`). |
| The publication panel `bg-white/5` hero card and `border-white/10` cards | `WikidataPublicationControls.tsx:135,159,162,180,193,209,320` | These ARE remapped to green-tinted light values, but on top of `bg-slate-950` they still read as dark. Fixing the root removes the complaint. |

Everything else on the page (item table, drawer, upload panel, verify
modal) already uses `Glass`, tokens, or remapped utilities. The plan
includes a grep audit step to prove no other offender hides in files I
did not read (`StatementTableView`, `WikidataUploadPanel`, verify modal).

### 2.2 Why the page feels overcomplicated

Current vertical layout of the modern surface
(`frontend/src/components/wikidata/WikidataItemsPanel.tsx:370-666`):

```
1. Header: "Wikidata Items" + description
2. <details> "Source tools and exports"        ← 10 buttons collapsed
   (Export JSON, Export CSV, Import overrides, Section export menu,
    Refresh, Verify non-passing, Verify visible, Autofix with AI,
    Approve all visible)
3. <details> "Source settings and rebuild"     ← collapsed
   (All/Approved-only pill, Rebuild, Skip cache, Approved items only,
    approved count, QS download)
4. Publication panel                           ← always expanded
   (target radios, defer checkbox, Prepare, step nav, controls,
    entity page table, job progress)
5. <details> "Browse and edit source items"    ← COLLAPSED when the
   (the actual review table)                      Publication panel exists
6. Fallback upload panel (only when Publication API is unavailable)
```

Pain points:

- The **review table — the main work surface — is item 6 and collapsed**
  (`open={!publicationActive}`, `WikidataItemsPanel.tsx:599`). A curator
  must click a disclosure to see the rows they came to review.
- **Three separate `<details>` accordions** hide the daily controls; a
  curator cannot see what is available without opening all three.
- **Two different "approved" concepts sit in two places**: the
  `All matches / Approved only` scope pill (build scope) and the
  `Approved items only` checkbox (upload scope). Both appear twice — in
  the route shell (`WikidataStudio.tsx:410-424`) and in the panel
  (`WikidataItemsPanel.tsx:475-520`).
- **Counts repeat**: item total shows in the header, in the table
  footer, in the publication hero, and in the entity page.
- **AI actions scatter**: Verify non-passing / Verify visible / Autofix
  live in the collapsed exports row, while the per-item Verify/Autofix
  live in the drawer. Nothing labels them as one group.
- Exports (JSON/CSV/TTL/QS/import) mix with workflow actions (verify,
  approve) in one row.

## 3. Goals and non-goals

### Goals

- G1: Every Studio surface follows the selected color scheme. No
  hardcoded dark background remains on the page.
- G2: The theme switch is reachable on every page (header), not only on
  Settings.
- G3: The review table is visible by default at the top of the workflow.
- G4: One primary action per step is visually obvious; advanced and
  export tools live in exactly one collapsed "Advanced" area.
- G5: All existing backend contracts, job flows, and testids used by
  specs stay stable, or migrate with their specs in the same change.

### Non-goals

- No backend route, job kind, model field, or API payload changes.
- No removal of the legacy sidebar mode (AGENTS.md preserves it; a
  retirement decision is a follow-up, see §11).
- No change to Publication gates, digests, consent, or copy semantics
  (Rules W-212…W-223 stay intact).
- No new Rule W-N: this is a UI defect fix + layout rework, not a
  production-write hardening incident.

## 4. Design

### 4.1 Theme fix (Phase 1 — uncontroversial)

1. Replace the Publication panel root with the shared Glass surface:
   - `WikidataPublicationPanel.tsx:259`
   - from: `<section className="rounded-xl border border-biu-sky/20 bg-slate-950 p-4 space-y-4">`
   - to: `<Glass as="section" className="p-4 space-y-4" data-testid="wikidata-publication-panel">`
   - `Glass` already resolves both themes (`index.css:157-168`) and is
     the W-35-compliant surface.
2. Replace the dark result-summary card:
   - `WikidataPublicationControls.tsx:139`
   - from: `bg-black/10`
   - to: `surface-inset` (token, `index.css:265-267`; dark = white 4%,
     light = green 5%).
3. Replace the unstyled `text-accent` links with `link-accent`
   (`WikidataPublicationControls.tsx:223`, `WikidataPublicationAiReview.tsx:129`).
4. Audit for stragglers before marking Phase 1 done:

   ```bash
   rg -n "bg-slate-9|bg-black/1?\b|text-accent|#0f|#00|#000" \
     frontend/src/components/wikidata frontend/src/routes/WikidataStudio.tsx
   ```

   Fix every hit with the matching token (`surface-inset`,
   `text-warn`, `link-accent`, `badge-*`). Known-safe hits (remapped
   utilities such as `bg-white/5`) stay.

Acceptance: with `data-theme="light"`, no element on
`/runs/:id/wikidata-studio` has a computed background darker than the
page background; with `data-theme="dark"` the page looks unchanged.

### 4.2 Theme toggle in the header (Phase 2)

`ThemeToggle` (`frontend/src/components/ThemeToggle.tsx`) currently
mounts only on Settings (`frontend/src/routes/Settings.tsx:78`). Add it
to the global header so the fix is discoverable:

- File: `frontend/src/components/Layout.tsx` (header, next to the user
  block, `Layout.tsx:29-37`).
- Render `<ThemeToggle />` before the user name span. Hide the name on
  small screens as today.
- The toggle already persists to `localStorage["mhm-color-scheme"]` and
  applies `data-theme` synchronously (`theme.ts:34-41`). No store
  changes.

Acceptance: switching the header toggle re-themes the current page
instantly and survives reload.

### 4.3 Interface simplification (Phase 3)

Target layout — three zones, top to bottom:

```
┌──────────────────────────────────────────────────────────────┐
│ A. REVIEW                                                    │
│    Status line: 183 items · 96 approved · 4 need attention   │
│    [All matches | Approved only]  [Rebuild]   …(Advanced ▾)  │
│    ┌ Review table (always visible) ─────────────────────┐    │
│    │ search · approve · verify · autofix · open drawer  │    │
│    └────────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────────┤
│ B. PUBLISH  (Glass panel — the only target selector, W-213)  │
│    1 Prepare → 2 Review & check → 3 Publish                  │
│    one primary button per state + progress + entity page     │
├──────────────────────────────────────────────────────────────┤
│ C. ADVANCED (one <details>)                                  │
│    exports (JSON/CSV/TTL/QS) · import · skip-cache ·         │
│    upload-approved-only · source switch · legacy sidebar     │
└──────────────────────────────────────────────────────────────┘
```

Control-by-control mapping (current → new home):

| Current control | Current place | Action | New home |
|---|---|---|---|
| Review table | `<details>` "Browse and edit source items" (collapsed) | Unwrap; always visible | Zone A |
| Table search / columns / pagination | table | keep | Zone A |
| Approve all visible | collapsed "Source tools and exports" | Move to Zone A toolbar (primary) | Zone A |
| Verify non-passing / Verify visible / Autofix | collapsed exports row | Group as three labeled buttons in Zone A toolbar, keep testids | Zone A |
| All matches / Approved only pill | settings details + route shell (duplicate) | Keep once, in Zone A toolbar | Zone A |
| Rebuild | settings details | Keep in Zone A toolbar (secondary) | Zone A |
| Skip cache checkbox | settings details | Move under Advanced | Zone C |
| Approved items only (upload scope) | settings details | Move under Advanced; the Publication panel computes its own scope from `approvedOnly` today, so the checkbox stays as-is but is demoted | Zone C |
| Export JSON/CSV, Section export menu, Import, QS download | exports details | Consolidate in Advanced under one "Export & import" group | Zone C |
| Refresh | exports details | Drop (Rebuild + auto-load cover it); keep `refreshToken` logic | removed |
| Source switch (legacy/canonical) + Legacy sidebar toggle | route shell "View options" details | Keep; move into Advanced as "Data source" | Zone C |
| Publication panel | always expanded between tools and table | Zone B, unchanged internals | Zone B |
| Status counts (4 copies) | header/table/publication/entity page | One status line in Zone A; publication keeps only its own Release counts | Zone A |

Implementation detail:

- File `WikidataItemsPanel.tsx`: reorder JSX zones; delete the two
  `<details>` wrappers ("Source tools and exports", "Source settings and
  rebuild"); keep every `data-testid` and label text that specs assert
  (`wikidata-items-verify-nonpassing`, `wikidata-items-verify-ai`,
  `wikidata-items-autofix-ai`, `wikidata-items-approve-visible`,
  `wikidata-rebuild-skip-cache`, `wikidata-upload-approved-only`,
  `wikidata-qs-download`, `wikidata-items-export-*`,
  `wikidata-items-import-btn`, `wikidata-item-lifecycle-bar`).
  Testids migrate with their elements — specs get path updates only.
- File `WikidataStudio.tsx`: the modern branch (`:272-317`) drops its
  duplicate approved-only pill and description paragraph; the shell
  keeps only the page title + link back to the run. Legacy-branch
  (`reviewMode === "legacy"`) stays untouched.
- The `open={!publicationActive}` behavior dies with the wrapper; the
  table is mounted unconditionally (also satisfies frontend R14 — no
  table unmount/remount flicker during jobs).
- `onPublicationActiveChange` wiring stays (it gates the fallback upload
  panel, Rule W-213 invariant 5).

### 4.4 Copy changes (Phase 3, same files)

- Zone A heading: "Review records" with the one-line status:
  `{total} items · {approved} approved · {nonPassing} need attention`.
- Advanced summary label: "Advanced: source, exports, rebuild".
- Publication panel keeps its current researcher wording ("Prepare your
  items, review the result, then publish.") — no rename of gates.

## 5. Implementation phases

### Phase 1 — Theme fixes (small, safe)

Files:

1. `frontend/src/components/wikidata/WikidataPublicationPanel.tsx`
   - root section → `<Glass>` (import from `@/components/glass`).
2. `frontend/src/components/wikidata/WikidataPublicationControls.tsx`
   - `bg-black/10` → `surface-inset`; `text-accent` → `link-accent`.
3. `frontend/src/components/wikidata/WikidataPublicationAiReview.tsx`
   - `text-accent` → `link-accent`.
4. Apply §4.1 audit grep; fix any further hits with tokens.

Acceptance:

- `yarn tsc --noEmit` passes.
- New e2e spec `frontend/e2e/wikidata-studio-theme.spec.ts` passes
  (see §6). It asserts, in light mode, the panel's computed
  `background-color` and `color`, toggles Dark via the header toggle
  and asserts the dark values, reloads and asserts persistence.
- Manual check on `yarn dev` against run `48ba6c13` in both themes.

### Phase 2 — Header theme toggle

Files: `frontend/src/components/Layout.tsx`.

Acceptance: toggle visible on all pages; theme persists; no layout
regression at 375 px width (flex-wrap already on the header).

### Phase 3 — Panel restructure

Files: `frontend/src/components/wikidata/WikidataItemsPanel.tsx`,
`frontend/src/routes/WikidataStudio.tsx`.

Steps:

1. Reorder zones A → B → C per §4.3.
2. Unwrap the review-table `<details>`; keep `WikidataItemTable` and its
   hooks (`useReportDerivedIds`, judging pills) exactly as-is.
3. Build the Zone A toolbar from the existing buttons (same handlers,
   same testids); delete the Refresh button.
4. Build Zone C as one `<details>`; move skip-cache, upload-scope,
   exports, import, source switch, legacy-sidebar toggle into it.
5. Strip the route-shell duplicates in the modern branch.
6. Update copy per §4.4.

Acceptance:

- All updated e2e specs pass (§6).
- No change in network behavior: same `fetchAllStudioItems` calls with
  `list_view=true` (Rule W-215), same job attachment hooks, same
  verify/upload flows.
- Manual pass: approve → verify → prepare → check → publish dry-run
  against mocked backend (e2e covers it), and one real run read-only.

### Phase 4 — Docs sync + cleanup (mandatory gate)

Per `.codex/skills/docs-on-code-change/SKILL.md` and
`docs-architecture-sync`:

- `docs/architecture/blocks/frontend/how-it-works.md` — update the
  "Wikidata Studio workflow" paragraph: table always visible, Advanced
  consolidation, header theme toggle.
- `docs/architecture/blocks/wikidata-studio/README.md` + a one-paragraph
  update in `production-publication.md` (panel is a Glass surface; zone
  order Review → Publish → Advanced). No gate text changes.
- `docs/architecture/blocks/frontend/key-files.md` — no file list change
  (no files added/removed except the new spec); add the theme spec to
  `tests.md` if the page lists e2e specs.
- `AGENTS.md` — no rule-range change (no new W-N); no edit needed.
- `CLAUDE.md` — no new rule; no edit needed.
- Delete this plan file? No — keep it; mark Status: implemented with the
  commit hash when done.

## 6. Test plan

### Existing specs that pin this surface (must stay green)

| Spec | Pins | Phase-3 impact |
|---|---|---|
| `frontend/e2e/wikidata-publication.spec.ts` | Publication flow, testids `publication-*`, details labels "Publication details and manual actions", "Advanced AI tools" | No testid changes in Controls; only panel-shell classes change → expect green without edits |
| `frontend/e2e/wikidata-item-table.spec.ts` | clicks "Source tools and exports", "Browse and edit source items" | Update: remove those two clicks; table is visible immediately |
| `frontend/e2e/wikidata-complete-scope.spec.ts` | fetchAll pagination, filter counts | No flow change → green; verify |
| `frontend/e2e/wikidata-item-drawer.spec.ts` | drawer actions | untouched |
| `frontend/e2e/wikidata-upload-panel.spec.ts` | fallback upload panel after 404/405/410 | keep behavior; verify |
| `frontend/e2e/wikidata-studio.spec.ts` | legacy/modern toggle paths | update if the toggle moves into Advanced |
| `frontend/tests/unit/wikidataPublicationControls.spec.tsx` | controls logic + testids | unchanged |
| `frontend/tests/unit/wikidataPublicationPanel.spec.tsx` | defer-connections flow | unchanged |

### New/changed tests

1. New `frontend/e2e/wikidata-studio-theme.spec.ts`:
   - light default → Publication panel computed styles are light tokens;
   - header toggle → dark values; reload → persists;
   - full-page screenshot artifact for visual review.
2. Update `wikidata-item-table.spec.ts` navigation helper (§ table above).
3. Optional unit: assert `WikidataItemsPanel` renders the table without
   user interaction (new test in `tests/unit/`, `.spec.tsx` per repo
   convention).

### Manual QA matrix

| Check | Light | Dark |
|---|---|---|
| Page background, header, table, drawer | readable green-tinted paper | unchanged |
| Publication panel + hero + result card | same tokens as page | unchanged |
| Verify modal + upload modal | readable | unchanged |
| Job tray | readable | unchanged |
| 375 px, 768 px, 1440 px widths | no overflow | no overflow |

### Commands

```bash
cd frontend
yarn tsc --noEmit
yarn test:unit
yarn test:e2e e2e/wikidata-studio-theme.spec.ts e2e/wikidata-publication.spec.ts \
  e2e/wikidata-item-table.spec.ts e2e/wikidata-complete-scope.spec.ts \
  e2e/wikidata-upload-panel.spec.ts e2e/wikidata-studio.spec.ts
yarn build
```

Wrap long runs with `quiet-run.sh` per the repo context-engineering rule.

## 7. Rules compliance

| Rule | How the plan respects it |
|---|---|
| W-35 (glass components) | Panel root moves to `<Glass>`; tokens replace raw dark classes |
| W-36 / frontend R1-R3 | No new selectors or effect/callback patterns; existing hooks untouched |
| R14 | Table stays mounted; no `setLoading` unmount flicker |
| R15 / W-61 | Verify modal + tray wiring untouched |
| W-103 / W-212 / W-213 | Publication stays the only target selector; fallback panel logic unchanged |
| W-215 | Same `fetchAllStudioItems` pagination; table visible by default helps, not hurts |
| W-27 (curator controls) | No control is removed except the redundant Refresh; everything else relocates |
| R12 | yarn only; `tsc --noEmit` gate in `yarn build` |

## 8. Risks

| Risk | Mitigation |
|---|---|
| E2E churn from moving controls | Keep testids + label text; move, do not rename; update only navigation helper clicks |
| Hidden behavioral coupling to `open={!publicationActive}` | The flag only chose the table's initial open state; unwrapping removes the coupling; fallback upload panel still keys off `routeUnavailable` |
| Light-mode regressions in components I did not open | §4.1 audit grep + full-page screenshot in the new spec |
| Verify buttons becoming less discoverable after regrouping | Zone A toolbar shows the three AI buttons with counts; drawer keeps per-item actions |

## 9. Decisions I need from Alexander before Phase 3

1. Approve the Review → Publish → Advanced zone order (§4.3 sketch)?
2. Drop the redundant Refresh button — OK?
3. Keep the legacy sidebar mode for now (recommended) — confirm?
4. Theme toggle lives in the header on every page — confirm?

Phases 1-2 (theme fix + header toggle) need no decision and can start
immediately after plan approval.

## 10. Rollout

- No deploy or push happens without explicit permission (global rule).
- After approval: implement phases in order, run §6 commands, update
  docs (Phase 4), then present the diff summary and ask about
  commit/deploy.

## 11. Follow-ups (out of scope here)

- Retire or hide the legacy sidebar mode after telemetry confirms no
  use (needs its own decision + doc update).
- Consider a single "AI actions" dropdown if the toolbar still feels
  crowded after real use.
- Sweep the whole app (not just Studio) for `bg-slate-9*`-style
  hardcodes; same token treatment.
