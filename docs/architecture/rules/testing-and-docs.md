# Testing + docs-sync gates

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

### Rule W-19 — User-flow e2e is the canonical test surface

`frontend/e2e/` Playwright specs are the canonical regression layer
for the curator UI. Backend endpoints are mocked deterministically via
`page.route()` (see `fixtures/extraction-fixtures.ts`) so the suite
runs in full isolation from Modal, Gemini, and the database.

Coverage rule: every user-visible UI surface (chip filter, sort
header, column popup, bulk action, modal, drawer) gets at least one
e2e spec that exercises the click path AND asserts the captured
backend call shape. Unit tests are reserved for pure functions
(`marc_structured_index.classify`, normalisation helpers). The
"every click is a test" bias mirrors the user directive on
2026-06-01.

### Rule W-49 — Pre-deploy / pre-push docs sync is mandatory (added 2026-07-09)

Before **any** `git push`, Heroku deploy, `gh` action, or `modal deploy` that
ships code from this repo, the agent MUST run the
[pre-deploy-docs-sync](.codex/skills/pre-deploy-docs-sync/SKILL.md) gate:

1. `git status` + full branch diff — enumerate every changed path.
2. Map paths through [docs/architecture/task-index.md](docs/architecture/task-index.md)
   to the affected `docs/architecture/blocks/<block>/` pages.
3. Apply [docs-architecture-sync](.codex/skills/docs-architecture-sync/SKILL.md)
   — update `key-files`, `how-it-works`, `rules`, `skills`, `tests`, and bump
   `W-1…W-N` pointers when `Rule W-N` was added.
4. Report `Docs: <paths>` or `Docs: verified current` in the deploy/push summary.

This is the **final audit** on top of [docs-on-code-change](.cursor/skills/docs-on-code-change/SKILL.md)
during implementation. "Docs were done earlier" is not sufficient without
re-checking the diff about to ship. User permission for each push remains
required separately (never auto-push).
