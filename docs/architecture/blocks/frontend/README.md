# Frontend architecture + testing strategy

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

The React 18 + TypeScript SPA under `frontend/` is the curator-facing UI for
every pipeline surface: projects, runs, AI Extraction review, Authority
review, RDF, HMO Wikibase Studio, Wikidata Studio, and the Linked Data
Explorer. It is built with Vite, styled with Tailwind + the in-house
liquid-glass design system, holds client state in three small Zustand stores
plus per-feature hooks, and talks to the FastAPI backend exclusively through
a tiny cookie-authenticated fetch wrapper. Testing is a three-layer pyramid:
Vitest component/unit tests, backend pytest route tests, and Playwright e2e
specs with fully mocked backends ("every click is a test").

## Contents

- [Key files](key-files.md) — entry points, stores, hooks, glass system, test configs
- [How it works](how-it-works.md) — routing, API clients, Zustand discipline, polling, glass, build tooling
- [Testing](testing.md) — the three-layer test pyramid and the suites pinning this block
- [Rules](rules.md) — the 13 invariants (R1–R13)
- [Skills](skills.md) — pages, API clients, job UI, e2e specs, glass components, polling surfaces

## Related blocks

- [job-service](../job-service/README.md) — the backend claiming/heartbeat model the `useRunJobs` poll consumes.
- [extraction](../extraction/README.md) — the Stage-2 review surface (EntityTable et al., Rule W-16).
- [authority](../authority/README.md) — the Authority review surface (Rule W-31).
- [wikidata-studio](../wikidata-studio/README.md) — Studio build/override/upload UI.
- [hmo-wikibase-studio](../hmo-wikibase-studio/README.md) — HMO items review + upload UI.
- [research](../research/README.md) — maps, SPARQL console, Linked Data Explorer panels.
