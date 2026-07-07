# AI Extraction — Review UI

> Up: [AI Extraction](README.md)

## How it works — 6. Review UI (Rule W-16 — 11 capabilities)

`frontend/src/components/extraction/`: 10-column `EntityTable` (inline
type/role selects, double-click edit, immediate approval checkbox),
`EntityFilterChips` (Source/Type/Role — AND across dimensions, OR within,
plus free-text search), `ColumnFilterPopup` (right-click header distinct
values), `EntityActionsBar` (live counts, select-all-visible, bulk
approve/reject, auto-approve, verify with AI), `AutoApproveRuleBuilder`
(sliders + live preview via `/auto-approve/preview`), `EntityEditModal`
(editor beside MARC source with span highlight), `MarcSourceDrawer`
(reads `/marc-source/{cn}`; yellow primary + blue secondary highlights,
Esc/pin), `NerVerificationModal` (SSE eval-agent session, reuses
`AgentFlowDiagram` + `VerdictsTable`), `AiVerdictPill` (5-state), the
Exists-in badge (reads the snapshotted `exists_in`), and `useApprovalStore`
(2 s fast-poll while a verify modal is open, 30 s otherwise; emits
`mhm.entities.refreshed` on every diff).
