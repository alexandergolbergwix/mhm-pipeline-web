# MHM Pipeline Web — Operating manual

This file is the **agent-facing operating manual** for the web port of
the MHM Pipeline. Read by Claude Code at the start of every session.

The desktop pipeline lives at `/Users/alexandergo/Documents/Doctorat/pipeline`.
The sibling eval-agent project lives at
`/Users/alexandergo/Documents/Doctorat/eval-agent`. This web app at
`/Users/alexandergo/Documents/Doctorat/mhm-pipeline-web` is the
collaborative, browser-based curator interface for the same pipeline.

---

## Session-startup procedure (MANDATORY)

1. **Read recent commits** — `git log --oneline -10`. The git log is
   the canonical "what changed".
2. **Read `docs/project-hierarchy-plan.md`** — the authoritative
   reference for the 5-section project sub-hierarchy. Anything that
   contradicts the plan is the contradiction's fault.
3. **Read this CLAUDE.md tail** for any rules added in prior sessions.
4. **Confirm the trust boundary with the eval-agent** (Rule W-1 below).

Do not skip step 2. The plan doc is the only thing that survives
context resets and prevents architectural drift.

---

## Hierarchy

```
Project
  ├─ AI Extraction           (NER + classifier; HF Hub delivery)
  ├─ Authority Enrichment    (Mazal / VIAF / Wikidata / KIMA)
  ├─ RDF Graph               (HMO ontology; Cytoscape viewer)
  ├─ HMO Wikibase Studio     (IIIF + crosswalk → wikibase.cloud)
  └─ Wikidata Studio         (reconcile · QuickStatements · upload)
        └─ AI verification    (per-run verb; opens from any section)
```

The agent is a **verb on a run**, never a top-level destination.

---

## Architectural invariants (CANNOT VIOLATE)

### Rule W-1 — eval-agent trust boundary (matches desktop Rule 48)

No Python imports across `mhm-pipeline-web/backend` ↔ `eval-agent/`.
Communication is **subprocess argv + `GEMINI_API_KEY` env var + stdout
[TRACE] / [STEP] / [STATS] lines + filesystem outputs only**. Same
contract as the desktop. The eval-agent location is resolved by
`backend/app/pipeline/agent_runner.py::locate_eval_agent` and respects
`EVAL_AGENT_ROOT`.

### Rule W-2 — no user-typed prompts in the AI verify UI

Curators pick **actions** from the server-side registry in
`backend/app/pipeline/agent_actions.py`. New actions are a Python dict
entry; the UI never accepts free-text goals. Three actions ship today:
`audit_match`, `find_duplicates`, `birth_death_check`.

### Rule W-3 — no "Stage N" in user-facing strings

Use the real section names: **AI Extraction · Authority · RDF Graph ·
HMO Wikibase · Wikidata Studio**. Never `Stage 2 / Stage 3 / …`.
Mirrors desktop Rule 53. Comments + internal docstrings may reference
stage numbers for diagnostics; UI strings may not.

### Rule W-4 — Wikidata safety (Rules 23 / 25 / 26 / 28 / 38 from desktop)

`backend/converter/wikidata/uploader.py` is **byte-identical** to the
desktop file. Never edit it in isolation; if a guard needs updating,
make the change in the desktop tree and re-mirror. The four-stage
modification guard (`_is_our_item`, `_assert_modifiable` x2,
`_would_create_identity_conflict`) is the regression barrier — the
2026-04-12 mass-merge incident is what these guards exist to prevent.

The Rule-25 moratorium gate (`MORATORIUM_LIFTED=true`) applies only to
wikidata.org. The Wikibase Cloud (mhm-hmo.wikibase.cloud) is a
separate trust boundary; only `assert=bot` + idempotent SHA compare
apply there (Rule W-5 below).

### Rule W-5 — Wikibase Cloud writer must be idempotent + bot-asserted

`backend/converter/wikibase/cloud_client.py::WikibaseCloudWriter` reads
each page's current content and SHA-compares against the new payload
before writing. Every edit POST carries `assert=bot` so an
un-bot-flagged session refuses. Don't shortcut either check.

### Rule W-6 — encrypted secrets per user, never on argv

Every long-lived secret (Gemini key, Wikidata token, Wikibase bot
password, HuggingFace token) is encrypted under the user's KEK in
`backend/app/crypto/secrets.py`. Routers unwrap on demand and pass via
env var to subprocesses. Never `--api-key=...` on argv. The
`api_keys` router (`backend/app/routers/api_keys.py`) tracks the
allowlist of valid `key_name` values.

### Rule W-7 — no top-level torch / transformers / huggingface_hub imports

Mirrors desktop Rule 2. Any module that uses these libs imports them
inside the function body:

```python
def _load_model() -> Any:
    import torch  # noqa: PLC0415
    import transformers  # noqa: PLC0415
    ...
```

`app/pipeline/extraction.py` follows this strictly. Adding a new ML
module? Same rule.

### Rule W-8 — async everywhere; sync work goes through threadpool

All FastAPI route handlers are `async def`. Synchronous CPU-bound or
I/O-bound libs (rdflib, pyshacl, sqlite, torch inference) are wrapped
in `asyncio.to_thread` or `fastapi.concurrency.run_in_threadpool`.
Never call a blocking lib directly inside a route — it pins the event
loop and the SSE keepalive dies.

### Rule W-9 — SSE streams cancel the subprocess on client disconnect

For any long-running operation that spawns a subprocess (eval-agent,
ML inference, RDF build, Wikidata upload), browser disconnect MUST
SIGTERM the child so we never keep paying for orphaned API calls.
Pattern: `asyncio.CancelledError` in the runner → `proc.terminate()` →
short wait → `proc.kill()`. See `backend/app/pipeline/agent_runner.py`.

### Rule W-10 — desktop reuse is BYTE-IDENTICAL where possible

Files in `backend/converter/` are mirrored from desktop's `converter/`
verbatim. Diffing the two trees should produce zero output for those
paths. If desktop adds a Rule, the web inherits it the next time the
files are re-mirrored. Never fork these files.

---

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
| `frontend/src/routes/` | One page per route (RunDetail, StageExtraction, StageRdf, HmoStudio, WikidataStudio, …) |
| `frontend/src/components/` | Shared widgets (AiVerificationModal, AgentFlowDiagram, MatchDetailDialog, SelectAllVisible, …) |
| `frontend/src/api/` | Per-resource API clients |
| `frontend/tests/` | Vitest unit tests |
| `frontend/e2e/` | Playwright browser tests |
| `backend/tests/` | pytest + httpx route tests |
| `docs/project-hierarchy-plan.md` | Authoritative plan reference |
| `docs/testing.md` | Three-layer test pyramid documentation |

---

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
```

---

## When to update the plan / this file

Update `docs/project-hierarchy-plan.md` whenever:
- A new section is added under Project (currently 5: AI Extraction,
  Authority, RDF Graph, HMO Wikibase, Wikidata Studio).
- A backend route is added, renamed, or removed.
- A new ML model or external API is integrated.

Update this CLAUDE.md whenever:
- A new architectural invariant emerges from an incident.
- A trust boundary changes.
- A "Stage N" creeps back into user-facing strings (delete it; cite Rule W-3).

A code change that alters the architecture is not complete until the
plan doc + this file are updated.

---

## What this web app does NOT do (yet)

- Train models — pipeline (desktop) owns training.
- Run the eval-agent orchestrator (planner) as a user-facing surface.
  The planner is internal research tooling; only the per-candidate
  `ai-verify` modal is exposed.
- Live Wikidata writes without `MORATORIUM_LIFTED=true` (Rule 25).
- Auto-approve AI verdicts (curator always confirms; verdicts surface
  as a `✨ AI says pass` pill).

---

## Reading list (re-read at session start when stuck)

- `docs/project-hierarchy-plan.md`
- `docs/testing.md`
- The desktop CLAUDE.md at
  `/Users/alexandergo/Documents/Doctorat/pipeline/CLAUDE.md` (the
  source of every safety guard listed above).
- The eval-agent CLAUDE.md at
  `/Users/alexandergo/Documents/Doctorat/eval-agent/CLAUDE.md`.
