# Codex commands & skills for MHM Pipeline Web

This directory holds Codex-CLI slash commands + skills for the
collaborative, browser-based curator interface to the MHM Pipeline.

## Layout

| Path | Purpose |
|---|---|
| `commands/` | One markdown file per slash command. Invoke with `/<name>` in Codex. |
| `skills/` | Self-contained capabilities (e.g. `mhm-pipeline-web-runtime`). |

## Available commands

- `/run-backend` — start the FastAPI dev server on :8000
- `/run-frontend` — start the Vite dev server on :5173
- `/typecheck` — frontend strict TypeScript no-emit pass
- `/run-tests` — full three-layer pyramid (backend / vitest / playwright)
- `/smoke-routers` — verify every FastAPI router imports + registers
- `/sync-from-desktop` — re-mirror `backend/converter/` from the desktop pipeline (Rule W-10)

## Authoritative refs

- `CLAUDE.md` — operating manual + architectural invariants (Rules W-1
  through W-10).
- `docs/project-hierarchy-plan.md` — the canonical 5-section layout
  (AI Extraction · Authority · RDF Graph · HMO Wikibase · Wikidata
  Studio).
- `docs/testing.md` — test-pyramid documentation.
