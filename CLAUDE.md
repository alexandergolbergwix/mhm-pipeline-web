<!--
This file is intentionally kept as a bridge for repo-local, web-specific
notes. The shared rules live upstream in /Users/alexandergo/Documents/Doctorat/pipeline.
Read AGENTS.md first, then use the upstream CLAUDE.md for common policy.
-->

# MHM Pipeline Web - Operating Manual

This repo inherits the shared agent rules, command docs, and skills from the
desktop pipeline repository at `/Users/alexandergo/Documents/Doctorat/pipeline`.

Use that repository as the upstream source of truth for shared behavior. Keep
this file for web-only notes and for any overrides that explicitly mention
`mhm-pipeline-web`.

## Startup

1. Read `/Users/alexandergo/Documents/Doctorat/pipeline/AGENTS.md`.
2. Read `/Users/alexandergo/Documents/Doctorat/pipeline/CLAUDE.md`.
3. Read the web-local docs in `docs/` and the repo history as needed.

## Web-specific inheritance note

The local `.claude/` and `.codex/` directories are maintained as a thin web
layer. When a shared task, workflow, or rule already exists in the pipeline
repo, prefer the upstream version unless this repo adds an explicit web-only
override.

## Architectural rules (W-1…W-136)

Every rule lives in a topic file under
[docs/architecture/rules/](docs/architecture/rules/). **Read the file for the
area you are touching before you change it** — do not work from this index
alone; the one-line summaries are pointers, not the invariant.

### [ai-verify.md](docs/architecture/rules/ai-verify.md) — AI verification (verify streams, judges, verdict caches)

- **W-17** — NER verdicts persist to ExtractionApproval.ai_verdict
- **W-18** — Stage-2 verify state_dir per-RUN (matches Rule W-14)
- **W-33** (verify state) — Verify state on Heroku is writable under `/tmp`
- **W-44** — Large-scope AI verify MUST stream verdicts live via job + TRACE
- **W-46** — Tier-1 judge model registry + Qubrid Kimi path
- **W-47** — HMO schema AI verify must show ontology context to the judge
- **W-48** — HMO item AI verify needs manuscript scope + substantive descriptions
- **W-50** — HMO verify label hygiene + multi-CN MARC merge
- **W-51** — AI verdict caches are content-addressed everywhere
- **W-54** — Canonical control-number join for AI verify
- **W-58** — Wikidata AI verdict cache keys canonicalize build and verify record identities
- **W-60** — Verify workers use provider-aware tier-1 credentials
- **W-62** — Wikidata diagnostic exports MUST preserve verdict and prompt evidence
- **W-63** — Wikidata verification MUST use the item source records, never an arbitrary run record
- **W-71** — Verdict evidence, cache keys, and static QIDs MUST share one verified contract
- **W-79** — Verification context MUST distinguish semantic subtypes
- **W-80** — Diagnostic display labels MUST be complete
- **W-104** — Studio AI verify MUST inject WikiProject Manuscripts skill context
- **W-115** — Wikidata AI verify MUST use the same Studio source as the review table
- **W-116** — Wikidata AI verify MUST NOT SPARQL-reconcile the Studio corpus
- **W-124** — Wikidata AI verify MUST receive all evidence channels + WPM Data Model
- **W-126** — Incomplete AI verify MUST report partial and keep TRACE verdicts
- **W-127** — Large-scope verify MUST stay alive under dyno pressure
- **W-128** — Verify job polls MUST stay light on the web dyno
- **W-130** — Interrupted AI verify MUST be Continuable from cached verdicts
- **W-131** — Studio list payloads and verify heaps MUST stay Basic-dyno-safe
- **W-132** — Wikidata verify MUST scope MARC and release in-memory Studio payloads
- **W-133** — Wikidata verify persist MUST NOT block the eval-agent stdout reader
- **W-134** — Interrupted verify jobs MUST auto-resume on the backend
- **W-135** — Verify judge throughput MUST use safe parallelism
- **W-136** — Verdict fingerprints MUST be invariant under verify-heap slimming

### [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md) — Wikidata Studio (public projection + write path)

- **W-26** (build cache) — Wikidata Studio build result is cached in Postgres
- **W-26** (P/Q audit — number reused) — Every Wikidata P/Q constant in property_mapping.py must be verified live
- **W-27** — Wikidata Studio curator controls
- **W-30** — Wikidata upload is fail-closed: reconcile-before-create + validator gate IN the write path
- **W-57** — Wikidata Studio HMO-parity surface + write-path ledger
- **W-65** — Wikidata projections MUST expose clean labels and verifier evidence
- **W-66** — Studio build joins MUST canonicalise all MARC control-number inputs
- **W-67** — Wikidata projections MUST fail closed on unsupported semantic claims
- **W-68** — Wikidata work projection MUST be source-aware, not authority-only
- **W-69** — Work identity, author evidence, and export fields MUST remain complete
- **W-70** — Work projection MUST consume enriched content metadata
- **W-72** — Public Wikidata semantic claims MUST be evidence-gated
- **W-73** — Illustrated genre MUST NOT imply illuminated manuscript
- **W-75** — P195 MUST NOT default to the NLI collection
- **W-76** — Hebrew gershayim MUST NOT trigger quote-noise warnings
- **W-77** — Explicit catalog semantics MUST survive projection
- **W-78** — Exported authority and work identity MUST stay precise
- **W-82** — Manuscript labels MUST reflect the physical holder
- **W-98** — Wikidata projection MUST follow the WikiProject Manuscripts data model fail-closed
- **W-99** — Wikidata write path MUST smart-check existence and own-or-accept modify
- **W-100** — Project Wikibase P/Q MUST map to public Wikidata via ontology, never by ID identity
- **W-103** — Wikidata upload target is curator-chosen; default dry-run
- **W-114** — Related works MUST NOT mint evidence-less Wikidata CREATE items
- **W-117** — Wikidata Studio emits only WPM public items; summarized HMO nodes roll up
- **W-118** — Wikidata Studio read paths MUST filter HMO ontology rows from stale cache
- **W-119** — Wikidata Studio build jobs MUST NOT WDQS-reconcile the corpus
- **W-120** — Canonical Wikidata labels and work evidence MUST match legacy hygiene
- **W-121** — Canonical CREATE works MUST recover MARC 245 / known-QID evidence
- **W-122** — Wikidata→Wikibase bridges MUST be browseable Item:Q URLs
- **W-125** — Canonical Wikidata Studio MUST merge full MARC/authority enrichment

### [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md) — HMO Wikibase Studio (build, upload, canonical read-back)

- **W-41** — HMO items table upload-outcome fields + upload-lifecycle AI verification
- **W-42** — HMO item upload is SHACL-gated by default
- **W-45** — HMO item build hygiene + verify MARC correlation
- **W-52** — HMO item build must emit substantive metadata for every exportable entity
- **W-53** — HMO item build: honest-negative grounding, person heading fidelity, second-pass label hygiene
- **W-55** — Canonical ontology namespace is `https://w3id.org/mhm/ontology#`
- **W-56** — Wikibase reconcile queries the instance's own direct-property URI, namespace-agnostically
- **W-85** — HMO entity links MUST use the live project Wikibase URL
- **W-86** — Authority identifiers MUST be unique across HMO entities
- **W-89** — Canonical rollout and Authority retirement MUST be fail-closed
- **W-90** — Live HMO read-back MUST use source-URI mappings and normalize Wikibase JSON
- **W-91** — Unsupported Wikibase scalar datatypes MUST be normalized before writes
- **W-92** — Canonical projections MUST fail closed on transient cache fallback
- **W-93** — Retired Authority mutations MUST remain fail-closed and observable
- **W-94** — Canonical readiness MUST be integrity-based, not count-based
- **W-95** — HMO creation MUST gate authority and live identity before canonical replacement
- **W-96** — HMO item builds MUST reject unmapped reconciliation predicates
- **W-97** — HMO canonical read-back MUST follow deferred-link writes
- **W-101** — HMO entities MUST be multi-source enriched through fail-closed matching
- **W-102** — Four HMO pillars: Wikibase root, Wikidata map, ontology mirror, multi-source richness
- **W-109** — HMO Studio MUST resolve AuthorityMatch ID collisions in-place
- **W-111** — All HMO URL claims MUST strip MARC quote wrappers

### [jobs-and-progress.md](docs/architecture/rules/jobs-and-progress.md) — Run jobs (claim, admission, progress, publish)

- **W-38** — Run jobs are claimed, heartbeated, and reconciled
- **W-59** — Commit Wikidata verify jobs before materialising their Studio scope
- **W-61** — Rejected verify-job enqueue requests MUST reset the curator modal
- **W-64** — Verify-job progress MUST count distinct candidates, not stream events
- **W-105** — Studio “Approve all visible” MUST run as a background job
- **W-106** — All Studio / RDF builds MUST run as `run_jobs` with inline progress
- **W-107** — All Studio publish/upload paths MUST run as `run_jobs`
- **W-108** — Job-backed publish MUST refresh curator tables on terminal success
- **W-110** — Live Studio upload MUST patch changed rows, not flicker the table
- **W-112** — Pipeline job progress MUST use 1-based steps with a unit label
- **W-113** — Long pipeline steps MUST report nested sub-progress
- **W-129** — Run jobs MUST pass admission control before claim

### [authority-marc.md](docs/architecture/rules/authority-marc.md) — Authority matching + MARC ingest

- **W-23** — KIMA / VIAF / Mazal payload completeness
- **W-28** — Mazal + KIMA live in Heroku Postgres
- **W-29** — Authority payload completeness
- **W-33** (matcher routing — number reused) — Authority matcher routing, deduplication, and notes grounding
- **W-37** — Homonym abstain, scoring, and curator picker
- **W-74** — Hebrew date punctuation MUST NOT abort record normalization
- **W-81** — MARC coverage MUST be loss-aware
- **W-83** — Place authority identifiers MUST preserve their namespace
- **W-84** — Ambiguous KIMA names MUST abstain

### [rdf-ontology.md](docs/architecture/rules/rdf-ontology.md) — RDF graph + ontology

- **W-32** — Non-production provenance-event places on the maps
- **W-34** — Full HMO RDF projection
- **W-43** — RDF SHACL validation MUST NOT use RDFS inference; type nodes explicitly instead
- **W-87** — HMO RDF literals MUST be export-safe
- **W-88** — GraphBuilder MUST emit only ontology-declared properties

### [platform-infra.md](docs/architecture/rules/platform-infra.md) — Platform, caching, Heroku, external calls

- **W-15** — modal/ is a deploy target, not a backend dependency
- **W-25** — Redis L1 in front of the Postgres inference cache
- **W-39** — Every on-disk build cache needs a durable Postgres counterpart
- **W-40** — Never hold an open DB transaction across a slow/retrying external write
- **W-123** — Login MUST NOT wait on Wikibase Cloud account provisioning

### [frontend-ui.md](docs/architecture/rules/frontend-ui.md) — Frontend curator surfaces

- **W-16** — Stage 2 review surface parity with the desktop
- **W-24** — All four curator surfaces support per-field manual editing
- **W-31** — Authority Enrichment review surface parity with extraction
- **W-35** — Reusable glass components, never raw `.glass` classes
- **W-36** — Zustand selectors and parent callback effects

### [security-versioning.md](docs/architecture/rules/security-versioning.md) — Public endpoints, event log, export

- **W-20** — Public endpoints carry the full spam + brute-force stack
- **W-21** — Every curator decision routes through the entity_event log
- **W-22** — Project state + history are exportable as one JSON bundle

### [testing-and-docs.md](docs/architecture/rules/testing-and-docs.md) — Testing + docs-sync gates

- **W-19** — User-flow e2e is the canonical test surface
- **W-49** — Pre-deploy / pre-push docs sync is mandatory

When adding a rule: append it to the matching topic file, add one index
line here, and bump the `W-1…W-N` pointer in this heading plus
[AGENTS.md](AGENTS.md) and [README.md](README.md).

## Project structure

| Path | Purpose |
|---|---|
| `backend/app/main.py` | FastAPI factory, router registration |
| `backend/app/routers/` | One router per resource (runs, ai_verify, extraction, rdf, hmo_studio, wikidata_studio, …) |
| `backend/app/pipeline/` | Per-section orchestrators (extraction, rdf_build, hmo_studio, agent_runner, authority, authority_hardening) |
| `backend/app/crypto/secrets.py` | KEK + encrypted secret round-trip |
| `backend/app/auth/` | Session cookies + RBAC |
| `backend/converter/` | Byte-identical mirror of desktop converter tree |
| `backend/ontology/` | hebrew-manuscripts.ttl + shacl-shapes.ttl (HMO ontology) |
| `frontend/src/routes/` | One page per route (RunDetail compatibility redirect, StageExtraction, StageRdf, HmoStudio, WikidataStudio, …) |
| `frontend/src/components/` | Shared widgets (AgentFlowDiagram, SelectAllVisible, …) |
| `frontend/src/components/wikidata/` | Studio-specific components: `ItemValidatorBadge`, `ItemApprovalBadge` |
| `frontend/src/api/` | Per-resource API clients |
| `frontend/tests/` | Vitest unit tests |
| `frontend/e2e/` | Playwright browser tests |
| `backend/tests/` | pytest + httpx route tests |
| `modal/modal_app.py` | Modal app for the four NER + genre models (deployed; not imported by backend) |
| `modal/README.md` | Modal deploy + economics |
| `docs/project-hierarchy-plan.md` | Authoritative plan reference |
| `docs/testing.md` | Three-layer test pyramid documentation |

## Common commands

```bash
# Backend dev server
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend dev server
cd frontend && yarn dev

# Backend tests
cd backend && .venv/bin/python -m pytest tests/ -v

# Frontend unit tests
cd frontend && yarn test:unit

# Browser e2e (one-time browser install: npx playwright install chromium)
cd frontend && yarn test:e2e

# Typecheck frontend
cd frontend && yarn tsc --noEmit

# Full smoke: ensure all routers register
cd backend && .venv/bin/python -c "from app.main import app; print(len(app.routes))"

# Modal — deploy the NER app (after editing modal/modal_app.py)
cd modal && modal deploy modal_app.py

# Modal — tail container logs (for cold-start debugging)
modal app logs mhm-ner

# Switch extraction to Modal (heroku)
heroku config:set EXTRACTION_MODE=modal MODAL_NER_URL=https://<workspace>--mhm-ner-mhmner-web.modal.run

# Switch authority enrichment to Postgres (production default — Rule W-28)
heroku config:set AUTHORITY_MODE=postgres

# Import Mazal + KIMA from local SQLite into Heroku Postgres (one-time, idempotent)
# Run locally pointing at Heroku DATABASE_URL — 15 s for KIMA, ~10 min for Mazal
cd backend && DATABASE_URL=... KIMA_DB_PATH=backend/data/kima/kima_index.db \
  .venv/bin/python -m scripts.import_kima_to_postgres
cd backend && DATABASE_URL=... MAZAL_DB_PATH=.../mazal_index.db \
  .venv/bin/python -m scripts.import_mazal_to_postgres
```

## What this web app does NOT do (yet)

- Train models — pipeline (desktop) owns training.
- Run the eval-agent orchestrator (planner) as a user-facing surface.
  The planner is internal research tooling; only the per-candidate
  `ai-verify` modal is exposed.
- Live Wikidata writes without curator `upload_target=live` (or legacy
  `MORATORIUM_LIFTED=true`) — default remains dry-run (Rule W-103 / Rule 25).
- Auto-approve AI verdicts (curator always confirms; verdicts surface
  as a `✨ AI says pass` pill).

## When to update the plan / this file

Update `docs/project-hierarchy-plan.md` whenever:
- A new section is added under Project (currently 5: AI Extraction,
  Authority, RDF Graph, HMO Wikibase, Wikidata Studio).
- A backend route is added, renamed, or removed.
- A new ML model or external API is integrated.

Add a rule to the matching file in
[docs/architecture/rules/](docs/architecture/rules/) (plus one index line
above) whenever:
- A new architectural invariant emerges from an incident.
- A trust boundary changes.
- A "Stage N" creeps back into user-facing strings (delete it; cite Rule W-3).

Keep the rule text out of this file — it is the index, not the rulebook.

Also bump the `W-1…W-N` pointer in [AGENTS.md](AGENTS.md) and
[README.md](README.md) when you add a new `Rule W-N`.

A code change that alters the architecture is not complete until the
plan doc, block docs (see [AGENTS.md](AGENTS.md)), and this file are updated.

## Reading list (re-read at session start when stuck)

- `docs/project-hierarchy-plan.md`
- `docs/testing.md`
- The desktop CLAUDE.md at
  `/Users/alexandergo/Documents/Doctorat/pipeline/CLAUDE.md` (the
  source of every safety guard listed above).
- The eval-agent CLAUDE.md at
  `/Users/alexandergo/Documents/Doctorat/eval-agent/CLAUDE.md`.
