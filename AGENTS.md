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

**→ Read [CLAUDE.md](CLAUDE.md) first** for the full incident-annotated
architectural rules (Rules W-1…W-55). Each rule records a real production
incident plus the invariant that closes it — check it before touching RDF
build/SHACL, Wikidata Studio writes, HMO Wikibase uploads, auth/rate-limit
surfaces, or the job/cache/versioning plumbing. This `AGENTS.md` is the
navigation layer; `CLAUDE.md` is where the "why" lives.

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
playbooks for the common tasks. Incident-annotated rule **details**
(W-1…W-55) stay in [CLAUDE.md](CLAUDE.md).

### Skill: keep docs in sync with every code change

Reading a block's docs before changing it (above) only works if the *last*
agent wrote back to them. Treat an out-of-date doc as a bug: docs and code
land in the **same** change, never as a follow-up.

**Mandatory gate:** before marking any code change complete, read and follow
[`.cursor/skills/docs-on-code-change/SKILL.md`](.cursor/skills/docs-on-code-change/SKILL.md)
(Cursor) or
[`.codex/skills/docs-on-code-change/SKILL.md`](.codex/skills/docs-on-code-change/SKILL.md)
(Codex). That gate invokes the detailed workflow in
[`.codex/skills/docs-architecture-sync/SKILL.md`](.codex/skills/docs-architecture-sync/SKILL.md).
Skip only for question-only replies with zero file edits.

| Layer | Update when |
|---|---|
| `docs/architecture/blocks/<block>/` | Any file, route, job kind, model field, or block invariant changes. Follow `.codex/skills/docs-architecture-sync/SKILL.md`. |
| [CLAUDE.md](CLAUDE.md) | A new incident-driven invariant → add `Rule W-N` (full prose). |
| [AGENTS.md](AGENTS.md) | Agent entry guidance changes: rule-range pointer, block index, cross-cutting workflows, doc-sync policy. |
| [README.md](README.md) | Operator quickstart or documentation table when the high-level surface changes. |

Block rules (R-series per block) live in each block's `rules.md`; do not
duplicate them here.

### Skill: pre-deploy / pre-push docs sync (mandatory before ship)

**Before every Heroku deploy, `git push`, or any GitHub-facing action**, read and
follow:

- Cursor: [`.cursor/skills/pre-deploy-docs-sync/SKILL.md`](.cursor/skills/pre-deploy-docs-sync/SKILL.md)
- Codex: [`.codex/skills/pre-deploy-docs-sync/SKILL.md`](.codex/skills/pre-deploy-docs-sync/SKILL.md)

That gate runs **after** code is complete and **before** you ask for or execute
push/deploy. It uses [task-index.md](docs/architecture/task-index.md) to map the
branch diff to `docs/architecture/blocks/<block>/` pages, then applies
[docs-architecture-sync](.codex/skills/docs-architecture-sync/SKILL.md) to fill
any gaps. Do not push with doc drift — even if docs were updated mid-session,
re-verify against the full diff. User permission for each push is still required
separately.

## AI verification — tier-1 judge

Every verify modal (authority, NER, Wikidata Studio, HMO items, HMO schema) and
the HMO upload pre/post-verify checkboxes expose a **Tier-1 judge** dropdown.
Models are listed by `GET /api/judge-models` from
`eval-agent/config/tier1_models.yaml`. Job/SSE params carry `tier_model`.
Gemini uses the curator's Settings key (or server `GEMINI_API_KEY`); Qubrid
Kimi uses server `QUBRID_API_KEY` only. Non-Gemini models run linear judging
(no agentic tool-loop). See eval-agent block **R16** and **Rule W-46** in
[CLAUDE.md](CLAUDE.md).

## HMO Wikibase Schema — AI verify

The global **HMO Wikibase Schema** panel (`/hmo-wikibase-schema`) judges every
ontology class/property from the bootstrap report (~387 rows). The eval-agent
`hmo_wikibase_schema` evaluator **must** receive `description`, `aliases`, and
OWL metadata (`property_kind`, `rdfs:range`) in its prompt — `filter_schema_entries`
enriches rows from `ontology_schema_reader.schema_entry_metadata_by_uri()` before
the fixture is written. Verdict cache keys include those fields so stale
"missing description" judgements invalidate after prompt fixes (**Rule W-47**,
eval-agent **R17**). After changing datatype inference or ontology comments,
re-run schema AI verify with **override cache** (or a fresh session) and compare
exports. Rubric: `eval-agent/config/rubrics/hmo_wikibase_schema.md`.

## HMO Wikibase Items — curator surface

On **HMO Wikibase Studio** (`HmoStudio`), item **build**, **upload**, and
**review** share one page: a lifecycle bar directly above the review table
always exposes **Rebuild (skip cache)** and **Reupload (update existing)**.
The toolbar also exposes **Verify with AI** (pre-upload audit) and
**Autofix with AI** (live-QID compare; scoped to rows that already have a
QID), each with an optional **Tier-1 judge** picker when enabled. After
autofix, open a row for **Apply AI fix** / **Apply fix & push**.
The table's **Data status** column shows `new (not uploaded)`, `will update
existing`, or `updated` per row. Deep dive:
[docs/architecture/blocks/hmo-wikibase-studio/](docs/architecture/blocks/hmo-wikibase-studio/README.md).

## Local measurement re-verify (offline curator ops)

To measure the true post-fix baseline of a Studio build/label/rubric change
**before deploying** — e.g. how many previously-`partial`/`fail` items a Rule
W-53 (HMO) or Wikidata Studio fix now flips to pass — use the
**measurement-only** harness instead of the real production rebuild:

- Skill: [`.codex/skills/local-measure-verify/SKILL.md`](.codex/skills/local-measure-verify/SKILL.md)
- Script: [`backend/scripts/local_measure_verify.py`](backend/scripts/local_measure_verify.py)

It reads a run's data from `DATABASE_URL` **read-only**, rebuilds the Studio
items in a local scratch dir with the current (possibly undeployed) code, and
re-judges a chosen scope (`--scope non-passing` by default) with an eval-agent
tier-1 model (`--tier-model`, Qubrid Kimi K2.5 by default), writing a
before/after report. **No DB / cache / live-wiki writes.** Channel-agnostic
(`--channel hmo|wikidata`); add a `Channel` subclass to cover a new Studio
surface. The write-back curator-ops path
(`scripts/rebuild_run_rdf_and_items.py` + `hmo_item_verify_fixup_loop.py
--persist-verdicts`, or the Studio UI) is separate and must be confirmed
explicitly. Qubrid `QUBRID_API_KEY` and prod `DATABASE_URL` are fetched via
`heroku config:get … -a mhm-pipeline-web`.

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
