# MHM Pipeline Web - Agent Instructions

## High-level system design

MHM Pipeline Web is a FastAPI (Heroku) + React/Vite web app that takes MARC
catalogue records of Hebrew manuscripts through five curator-reviewed stages
and publishes them as linked data:

```
Project
  ├─ AI Extraction         NER + genre models (Modal, HTTPS-only)
  ├─ Authority Enrichment  Mazal / KIMA / VIAF / Wikidata resolution
  ├─ RDF Graph             HMO-ontology (CIDOC-CRM) graph build
  ├─ HMO Wikibase Studio   items + schema on wikibase.cloud
  └─ Wikidata Studio       fail-closed, guarded writes to real Wikidata
       └─ AI verification  eval-agent (subprocess-only) verdicts, every stage
```

Every stage shares the same spine: slow work runs as claimed/heartbeated
**background jobs** (`run_jobs`), results land in **Postgres read-models**
behind a Redis→Postgres **cache stack**, curators review in a rich UI, and
every curator mutation is **event-versioned** (`project_events`).

**→ Deep dive: [docs/architecture/system-design.md](docs/architecture/system-design.md)**
— the navigation hub. It holds the runtime topology, the end-to-end data
flow, the global rules (G1–G8), and links to a *skills-and-rules* doc per
logical block:

Each block is a directory of ≤100-line pages; start at its `README.md`:

| Block | Doc |
|---|---|
| Job Service | [docs/architecture/blocks/job-service/](docs/architecture/blocks/job-service/README.md) |
| Eval Agent (all verify variants) | [docs/architecture/blocks/eval-agent/](docs/architecture/blocks/eval-agent/README.md) |
| AI Extraction | [docs/architecture/blocks/extraction/](docs/architecture/blocks/extraction/README.md) |
| Authority Enrichment | [docs/architecture/blocks/authority/](docs/architecture/blocks/authority/README.md) |
| RDF Graph | [docs/architecture/blocks/rdf-graph/](docs/architecture/blocks/rdf-graph/README.md) |
| Research surface | [docs/architecture/blocks/research/](docs/architecture/blocks/research/README.md) |
| HMO Wikibase Studio | [docs/architecture/blocks/hmo-wikibase-studio/](docs/architecture/blocks/hmo-wikibase-studio/README.md) |
| Wikidata Studio | [docs/architecture/blocks/wikidata-studio/](docs/architecture/blocks/wikidata-studio/README.md) |
| Caching | [docs/architecture/blocks/caching/](docs/architecture/blocks/caching/README.md) |
| Versioning & Export | [docs/architecture/blocks/versioning-export/](docs/architecture/blocks/versioning-export/README.md) |
| Platform & Security | [docs/architecture/blocks/platform-security/](docs/architecture/blocks/platform-security/README.md) |
| Frontend | [docs/architecture/blocks/frontend/](docs/architecture/blocks/frontend/README.md) |
| Deployment & Ops | [docs/architecture/blocks/deployment/](docs/architecture/blocks/deployment/README.md) |

System-wide pages: [global rules](docs/architecture/global-rules.md) ·
[data flow](docs/architecture/data-flow.md) ·
[task index](docs/architecture/task-index.md).

Before changing a block, read its `README.md` + `rules.md`: the rules are the
invariants your change must not break, and `skills.md` has step-by-step
playbooks for the common tasks. The incident-annotated rule history
(W-1…W-41) stays in [CLAUDE.md](CLAUDE.md).

### Skill: keep `docs/architecture` in sync with every code change

Reading a block's docs before changing it (above) only works if the *last*
agent wrote back to them. Treat an out-of-date architecture page as a bug:
docs and code land in the **same** change, never as a follow-up. Before
finishing any change that adds, renames, or removes a file, route, job
kind, model field, or invariant, read and follow:

- `.codex/skills/docs-architecture-sync/SKILL.md`

## Inherited rules

This repository inherits its shared agent rules and reusable workflows from:

- `/Users/alexandergo/Documents/Doctorat/pipeline/AGENTS.md`
- `/Users/alexandergo/Documents/Doctorat/pipeline/CLAUDE.md`
- `/Users/alexandergo/Documents/Doctorat/pipeline/.claude/commands/`
- `/Users/alexandergo/Documents/Doctorat/pipeline/.codex/commands/`
- `/Users/alexandergo/Documents/Doctorat/pipeline/.codex/skills/`

Use the pipeline repo as the upstream source of truth for shared rules,
commands, and skills. This web repo adds only web-specific overrides and
bridge notes.

## Local rule

When a pipeline rule and a local web rule conflict, follow the local web rule
only if it explicitly applies to `mhm-pipeline-web`; otherwise inherit the
pipeline rule unchanged.

## Bar-Ilan presentation deck (upstream)

PPTX speaker-note edits live in the **pipeline** repo. Before any deck work read:

- `/Users/alexandergo/Documents/Doctorat/pipeline/.codex/skills/bar-ilan-pptx/SKILL.md`
- `/Users/alexandergo/Documents/Doctorat/pipeline/.codex/skills/pptx-toolkit/SKILL.md`

Hard rules: Hebrew notes = spoken teleprompter (read aloud); preserve **RTL +
Arial** via `edit_pptx_deck.py` / `set_slide_notes` / `--fix-notes` — never raw
`python-pptx` `notes_text_frame.text`.

## Linked Data Explorer

The Linked Data Explorer Overview tab must aggregate linked-data entities
across the local RDF Graph, Wikidata Studio/Wikidata reconciliation data, and
the project Wikibase when a Wikibase endpoint is configured. Do not treat the
Overview counts as RDF-only counts.
