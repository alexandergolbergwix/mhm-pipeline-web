# Frontend curator surfaces

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

### Rule W-16 — Stage 2 review surface parity with the desktop

The AI Extraction page surfaces a rich entity-review UI that mirrors
the desktop's `ExtractionEditor`. Eleven independent capabilities,
each owned by one component under `frontend/src/components/extraction/`:

1. **10-column entity table** (`EntityTable.tsx`): Record · Entity ·
   Type ▾ · Role ▾ · Source · Conf. · Model Conf. · Exists in · AI
   verdict · Approved · Edit/View. Inline `<select>` for Type/Role;
   double-click Entity opens edit modal; checkbox writes
   `ExtractionApproval` immediately.
2. **Three-dimensional filter chips** (`EntityFilterChips.tsx`):
   Source / Type / Role. AND across dimensions, OR within. Free-text
   search across entity text + control number.
3. **Per-column distinct-value popup** (`ColumnFilterPopup.tsx`,
   right-click header). Parity with desktop Rule 49 §E.
4. **Sticky action bar** (`EntityActionsBar.tsx`): live counts,
   Select-all-visible, Approve/Reject selected, Auto-approve…,
   Verify selected / all visible with AI.
5. **Auto-approve rule builder** (`AutoApproveRuleBuilder.tsx`):
   min_confidence slider, source/type/not_role multi-checkboxes,
   require_ai_pass / respect_ai_fail toggles, live preview count via
   `/auto-approve/preview`, Apply via `/auto-approve`.
6. **Edit modal** (`EntityEditModal.tsx`): side-by-side editor +
   MARC source text with the current span highlighted yellow.
7. **MARC source drawer** (`MarcSourceDrawer.tsx`): right-side slide-in
   `<aside>`. Yellow primary highlight on the active entity, blue
   secondary highlights on the record's other entities. Esc-to-close
   + pin checkbox. Reads `/marc-source/{cn}`.
8. **NER AI verification modal** (`NerVerificationModal.tsx`):
   structural sibling of the shared verification shell pinned to
   `/extraction/ai-verify`. Reuses `AgentFlowDiagram` + `VerdictsTable`
   evaluator-agnostically. Auto-loads last session on open.
9. **AI verdict pill** (`AiVerdictPill.tsx`): 5-state inline pill
   (pass / partial / fail / abstain / unknown) with reasoning tooltip.
10. **Exists-in badge**: 4-state grounded / wrong_field / novel /
    unknown, derived from `MarcStructuredIndex.classify(...)` on the
    backend per-row. Powers the curator's at-a-glance scan for
    "matched MARC", "in wrong field", or "genuinely new info from
    free-text notes".
11. **Approval store hook** (`useApprovalStore`): fast-poll (2s) while
    a verify modal is open, slow-poll (30s) otherwise, emits a custom
    `mhm.entities.refreshed` DOM event on every diff so the entity
    table re-renders without prop-drilling.

The legacy per-record collapsible body is preserved under a
`<details>` element below the new review surface for users who prefer
the by-MS grouping.

### Rule W-24 — All four curator surfaces support per-field manual editing (added 2026-06-03)

- **NER**: `override_text`, `override_type`, `override_role` in
  `extraction_approvals`; API key mapping lives in
  `frontend/src/api/extractionApprovals.ts` (UI sends `{type, role,
  text}` → backend `{override_*}`).
- **Authority**: `AuthorityMatchEdit` PATCH
  (`/matches/{id}/edit`) wired to `AuthorityMatchEditDialog`.
- **Wikidata Studio**: `ItemOverridePayload` PATCH wired to
  `ItemOverrideDialog`; override applied at next build.
- **HMO Studio**: `RecordEdit` PATCH wired to `MarcFieldEditorDialog`
  via `GET /runs/{id}/records` picker; requires RDF + manifest
  rebuild to apply downstream.

### Rule W-31 — Authority Enrichment review surface parity with extraction (added 2026-06-09)

The Authority candidates page historically surfaced a rich entity-review UI that mirrored
the extraction EntityTable layout. This is now a migration-history record only:
the standalone route and Authority mutations are retired (HTTP 410 by default),
while the matcher and read-only rows remain for HMO creation, provenance, and
audit. The former five capabilities (removed after migration telemetry confirmed no live use) were:

1. **9-column candidates table** (`AuthorityTable.tsx`): Record · Entity ·
   Role · Source · Conf. · Guards · AI verdict · Approved · Edit. Sortable
   headers with `CONF_ORDER` / `VERD_ORDER` custom orderings; per-column
   `ColumnFilterPopup` (right-click or ▾ button); free-text search inputs
   directly in the Record and Entity header cells; guard-flag chips rendered as
   `⚠ flagname` with `title={guardExplain(g)}`.
2. **Per-column distinct-value popup**: reuses `ColumnFilterPopup` from
   `frontend/src/components/extraction/`. Active filter chips with
   clear-per-column + "Clear all". OR within a column, AND across columns.
3. **`AuthorityDetailDrawer`** (`AuthorityDetailDrawer.tsx`): right-side
   fixed drawer (640 px), z-40, translateX animation. Pin + Esc-to-close.
   4 scrollable cards: Match (IDs, preferred names, KIMA geo), Confidence &
   Sources (badge + reasoning + source chips + guard chips), Dates (ms/birth/death
   years), AI Verdict (verdict pill + reasoning + model). Owns its edit dialog
   (`AuthorityMatchEditDialog`) and MARC popup internally. Replaces the old
   5-tab `MatchDetailDialog` modal.
4. **`AuthorityAutoApproveRuleBuilder`** (`AuthorityAutoApproveRuleBuilder.tsx`):
   rule modal with confidence/source/entity-kind/min-source-count controls,
   `require_ai_pass` + `respect_ai_fail` toggles, and debounced live preview
   (350 ms) via `POST /runs/{id}/matches/auto-approve/preview`. Apply calls
   `POST /runs/{id}/matches/auto-approve`. `AuthorityAutoApproveRule` Pydantic
   schema lives in `backend/app/schemas/runs.py`.
5. **`onFilteredChange` callback**: `AuthorityTable` reports its current
   filtered match IDs to `RunDetail` via `onFilteredChange(ids)` on every
   `display` change (via `useEffect`), keeping the toolbar count badge and
   "Verify all visible" scope in sync with the table's internal filter state.

Backend helper `_apply_auto_approve_rule()` in `backend/app/routers/runs.py`
filters unapproved matches by confidence_levels, sources (intersection),
entity_kind, min_source_count, and AI verdict gates.

### Rule W-35 — Reusable glass components, never raw `.glass` classes (added 2026-06-17)

Every frosted / liquid-glass surface in the React UI must use the shared
components under ``frontend/src/components/glass/``:

| Need | Component | Variants |
|---|---|---|
| Panels, cards, modals, drawers | ``<Glass>`` | ``panel`` (default), ``drawer``, ``modal``, ``compact`` |
| Chips, badges, stat pills | ``<GlassPill>`` | (always ``variant="pill"``) |
| Full-page ambient background | ``<LiquidGlassCanvas>`` | mounted once in ``main.tsx`` |

**Do not**:

- Add ``className="glass"`` or ``className="glass-pill"`` on new UI — those
  CSS classes are deprecated legacy aliases.
- Create per-feature glass wrappers or DOM enhancers that upgrade elements at
  runtime.
- Duplicate ``LiquidGlassSurface`` / displacement-map logic outside the glass
  module.

**Do**:

```tsx
import {Glass, GlassPill} from "@/components/glass";

<Glass as="section" className="p-6 space-y-4">…</Glass>
<Glass variant="drawer" className="fixed right-0 …">…</Glass>
<GlassPill className="px-3 py-1 text-xs">…</GlassPill>
<GlassPill as="label" className="…">…</GlassPill>
```

Form fields keep ``input-glass`` (plain CSS, not liquid refraction).
Layout/tint tokens live in ``.glass-shell*``; refraction is applied inside
``LiquidGlassSurface``.

---

### Rule W-36 — Zustand selectors and parent callback effects (added 2026-06-20)

Three production blank-UI incidents (React error #185) traced to the same
failure mode. These invariants prevent recurrence:

**Zustand selectors**
- Select **primitives** from the store: ``useRunJobs((s) => s.jobs)``,
  ``useAuth((s) => s.user?.id)``. Never ``useRunJobs((s) => s.jobForRun(...))``,
  ``useRunJobs((s) => s.activeJobs())``, or ``useRunJobs((s) => Object.values(s.jobs))``
  — each call allocates a new reference and can spin an infinite re-render loop.
- Derive active jobs with ``selectActiveJob(jobsRecord, runId, kind)`` inside
  ``useMemo``, keyed by a ``jobFingerprint`` when syncing progress.

**Table → parent reporting**
- Never ``useEffect`` + ``onFixableChange(newArray)`` without a content
  fingerprint guard. Use ``useReportDerivedIds`` /
  ``useReportFilteredEntities`` from ``frontend/src/hooks/useReportDerivedIds.ts``.
- Parent callbacks passed into tables must be ``useCallback``-stable.

**Long-running job UI**
- Use ``useRunJobAttachment`` or ``useVerifyJob`` — never hand-rolled
  ``jobForRun`` selectors plus overlapping poll effects.
- Hook option callbacks (``onFailed``, ``onComplete``, ``sync``) must be refs
  or ``useCallback``; never inline arrows in objects consumed by effect deps.

Shared helpers: ``frontend/src/utils/renderStable.ts``.

---
