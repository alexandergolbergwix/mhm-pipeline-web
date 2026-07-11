# MHM Pipeline Web — System Design (deep dive)

> Up: [AGENTS.md](../../AGENTS.md) · Siblings: [project-hierarchy-plan.md](../project-hierarchy-plan.md), [testing.md](../testing.md)
>
> Navigation hub for the system documentation. Each **logical block** links to
> a directory of *skills-and-rules* docs: what the block does, how it works,
> the **Rules** (invariants a change must not break) and the **Skills**
> (step-by-step playbooks). Start every block at its `README.md`.

## What the system is

MHM Pipeline Web is the web counterpart of the desktop Hebrew-manuscripts
pipeline. It takes MARC catalogue records of Hebrew manuscripts through five
curator-reviewed stages and publishes the results as linked data:

```
Project
  ├─ AI Extraction         NER + genre models (Modal) over MARC records
  ├─ Authority Enrichment  Mazal / KIMA / VIAF / Wikidata entity resolution
  ├─ RDF Graph             HMO-ontology (CIDOC-CRM) graph build
  ├─ HMO Wikibase Studio   items + schema on a private wikibase.cloud instance
  └─ Wikidata Studio       guarded, fail-closed writes to real Wikidata
       └─ AI verification  eval-agent verdicts, reachable from every stage
```

Every stage follows the same shape: a **pipeline module** does the work inside
a **background job**, results land in **Postgres read-models** with a
multi-tier **cache** in front, curators review/override in a rich **React
UI**, and every curator mutation is **event-versioned**.

## Runtime topology

```
Browser (React/Vite SPA)
   │ HTTPS /api/*  (session cookie + CSRF)
FastAPI on Heroku dynos (multi-dyno; slug FS read-only, /tmp writable)
   ├─ Heroku Postgres  — source of truth: read-models, event log, caches,
   │                     Mazal/KIMA authority tables, run_jobs
   ├─ Heroku Redis     — L1 inference cache + slowapi rate-limit storage
   ├─ Modal (HTTPS)    — 4 NER models + genre classifier (never imported)
   ├─ eval-agent       — tier-1 LLM judge subprocess (Gemini / Qubrid Kimi; never imported)
   ├─ External APIs    — VIAF SRU, Wikidata SPARQL/API, Wikibase Cloud, Resend
   └─ Heroku Scheduler — snapshots, event prune, cache prune
```

Two hard **trust boundaries** recur everywhere: Modal and the eval-agent are
reached only over HTTPS / subprocess I/O — the backend never imports their
Python. And `backend/converter/` is a byte-identical vendored mirror of the
desktop repo, synced by script, never edited ad hoc.

## Cross-cutting spine

Shared by all five stages; understand these first:

1. **Background jobs** — every slow operation is a `run_jobs` row claimed,
   heartbeated, and reconciled. → [Job Service](blocks/job-service/README.md)
2. **Caching tiers** — Redis L1 → Postgres → fetch, plus fingerprint-keyed
   durable build caches. → [Caching](blocks/caching/README.md)
3. **Event versioning** — `apply_event(...)` before every read-model update.
   → [Versioning & Export](blocks/versioning-export/README.md)
4. **AI verification** — eval-agent verdict channels for every stage.
   → [Eval Agent](blocks/eval-agent/README.md)

## Logical blocks

| Block | Doc | One-liner |
|---|---|---|
| Job Service | [blocks/job-service/](blocks/job-service/README.md) | Claimed/heartbeated/reconciled background jobs over `run_jobs`; all job kinds |
| Eval Agent (all variants) | [blocks/eval-agent/](blocks/eval-agent/README.md) | AI-verify channels (authority, NER, Wikidata, HMO item/schema), sessions, verdicts |
| AI Extraction | [blocks/extraction/](blocks/extraction/README.md) | MARC ingest, NER backends (Modal/local/HF), review surface, approvals |
| Authority Enrichment | [blocks/authority/](blocks/authority/README.md) | Matcher routing, guards, homonym abstain, Mazal/KIMA in Postgres |
| RDF Graph | [blocks/rdf-graph/](blocks/rdf-graph/README.md) | HMO-ontology graph build, enrichment merge, coverage, durable TTL |
| Research surface | [blocks/research/](blocks/research/README.md) | Corpus analytics, provenance/movement maps, pathfinding, saved queries |
| HMO Wikibase Studio | [blocks/hmo-wikibase-studio/](blocks/hmo-wikibase-studio/README.md) | Schema bootstrap, item build/reconcile/upload to Wikibase Cloud |
| Wikidata Studio | [blocks/wikidata-studio/](blocks/wikidata-studio/README.md) | Fail-closed Wikidata writes: validator moat, reconcile-before-create |
| Caching | [blocks/caching/](blocks/caching/README.md) | Redis L1 + Postgres inference cache + fingerprinted durable build caches |
| Versioning & Export | [blocks/versioning-export/](blocks/versioning-export/README.md) | `project_events` log, snapshots, history API, JSON export bundles |
| Platform & Security | [blocks/platform-security/](blocks/platform-security/README.md) | Auth/RBAC, public-endpoint defence stack, secrets, DB discipline |
| Frontend | [blocks/frontend/](blocks/frontend/README.md) | SPA architecture, Zustand discipline, glass design system, test pyramid |
| Deployment & Ops | [blocks/deployment/](blocks/deployment/README.md) | Heroku/Modal deploys, env vars, scheduler jobs, imports, migrations |

## System-wide pages

- [Global rules (G1–G8)](global-rules.md) — invariants that apply to every block.
- [End-to-end data flow](data-flow.md) — MARC upload → published linked data.
- [Task index](task-index.md) — "you are asked to X → read Y first".

The incident-annotated rule history (W-1…W-64) lives in the repo root
[CLAUDE.md](../../CLAUDE.md); block docs restate the ones that matter locally.
