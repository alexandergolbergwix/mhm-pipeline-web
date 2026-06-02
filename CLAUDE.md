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

### Rule W-11 — three extraction backends; one selector

`backend/app/pipeline/extraction_backend.py` exposes one
`InferenceBackend` Protocol with three implementations:

| Backend | When | Notes |
|---|---|---|
| `LocalInferenceBackend` | `EXTRACTION_MODE=local` | torch + transformers inside the FastAPI dyno. Works offline but bloats the dyno (~3 GB RAM for all four warm) |
| `HfApiInferenceBackend` | `EXTRACTION_MODE=hf-api` | Calls HF Inference Providers. **Dead end for our four models** on the free serverless tier — HF refuses to deploy custom-code repos (Provenance/Contents) and won't allocate hardware for low-traffic standard repos (Person/Genre). Kept for repos HF does deploy |
| `ModalInferenceBackend` | `EXTRACTION_MODE=modal` | Calls the deployed `mhm-ner` Modal app at `MODAL_NER_URL`. All four models in one container, shared DictaBERT base, pay-per-second. The current production answer |

`resolve_mode()` picks one from the env var; `build_backend()` lazy-imports
the right module so a Modal-only deploy never imports torch and an
hf-api-only deploy never reads weight files. New backends go through
this Protocol — no direct calls from `extraction.py` to vendor SDKs.

### Rule W-12 — shared cross-user inference cache

`backend/app/models/inference_cache.py` is the team-wide content-addressed
cache for every external inference call. Keyed by `(kind, query_hash)`
where `query_hash` is SHA-256 of canonical JSON of the query summary.
`cache_lookup_or_call(db, kind=..., query_summary=..., fetch=...)` in
`backend/app/pipeline/inference_cache.py` is the only entry point;
never write to the table directly.

Three call sites use it today:
1. **HF backend** — `extraction_backend_hf.py::_cached()` for every
   NER + genre call.
2. **Modal backend** — `extraction_backend_modal.py::_cached()` mirrors
   the HF pattern.
3. **Authority matchers** — `pipeline/authority.py::DesktopMatcher`
   has six cached wrappers (`_kima_match_place`, `_mazal_match_person`,
   `_mazal_get_details`, `_viaf_match_with_metadata`,
   `_wikidata_match_person`, `_wikidata_dates`). `_match_one` routes
   every external call through them so the first team member to
   resolve a name populates the cache; everyone else warm-hits.

TTL is per-kind via `KIND_TTL`. NER + genre + ai_verdict are
content-addressed → no expiry (`None`). Authority lookups expire at
30 days because the upstream record can change.

`skip_cache=True` is the explicit "force fresh" escape hatch. UI
exposes it as "Override cache (force fresh LLM call)" on the AI
verification modal and "Skip cache" on the extraction panel. Never
default to True — the user must opt in.

### Rule W-13 — AI verdicts persist to AuthorityMatch.payload.ai_verdict

When the AI verification SSE stream finishes,
`backend/app/routers/ai_verify.py::_persist_ai_verdicts_to_matches`
opens a fresh `session_scope` (the request-scoped DB session is
closed by the time the finally fires) and writes the verdict
summary back to each `AuthorityMatch.payload["ai_verdict"]` joined
by `candidate._match_id`. Without this the matches table shows
verdicts only for the row scoped to "single match" runs and
forgets everything after the modal closes.

The persisted summary carries: `overall`, `name_ok`, `type_ok`,
`role_ok`, `reasoning`, `model`, `judged_at`, `cache_key`,
`session_id`, `evaluator`. The matches-table column reads
`payload.ai_verdict.overall`.

### Rule W-14 — AI verify state_dir is per-RUN, not per-session

`state/ai-verify-sessions/<run_id>/` is the shared per-run state
dir. The eval-agent's verdict cache (`<state_dir>/cache/`) and
accumulated `runs/<ts>/` artefacts live here so opening the modal
again warm-hits prior Gemini judgements.

Per-session isolation goes one level deeper:
`<state_dir>/sessions/<session_id>/` holds the filtered fixture +
SSE trace audit log. Two sessions of the same run share the cache;
each session has its own audit trail.

`agent_runner.py::_resolve_session_dir(run_id, session_id)` accepts
BOTH the new layout and the legacy direct-child layout so sessions
written before this rule still load. Never write a session into
the legacy layout — that path is read-only.

### Rule W-15 — modal/ is a deploy target, not a backend dependency

`modal/modal_app.py` is the Modal app definition (deployed once via
`modal deploy modal_app.py`). It is NEVER imported by the FastAPI
backend. Communication is HTTPS only — the backend's
`ModalInferenceBackend` POSTs to `<MODAL_NER_URL>/extract`.

The Modal app vendors the desktop's `ner/` + `converter/authority/`
modules via `image.add_local_dir(..., copy=True)`. Weight artefacts
(~3 GB) are pre-baked into the image at build time via
`run_function(_bake_weights)` BEFORE the local-dir adds so a
desktop NER code edit doesn't invalidate the weight layer.

`_bake_weights` runs first because Modal's rule is "add_local_*
must be last unless copy=True". A misordered chain like
`add_local_* → run_function` is rejected at build time. The fix
that landed in c19b9f3 + 98a0ca6 is the canonical layout.

Cold start: ~30-60s for all four BERT models on a CPU-2 container.
`scaledown_window=300` keeps it warm for 5 min after the last
call. After that, $0.

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
   structural sibling of `AiVerificationModal` pinned to
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

### Rule W-17 — NER verdicts persist to ExtractionApproval.ai_verdict

Mirror of Rule W-13 (authority verdicts). When the
`NerVerificationModal`'s SSE stream finishes, the backend's
`extraction_verify.py::_persist_ai_verdicts_to_entities` opens a fresh
`session_scope` (the request session is closed by then) and joins
`candidate._entity_id` → `ExtractionApproval.id`, writing the verdict
summary into the JSONB `ai_verdict` column AND setting
`ai_verdict_at = now()`.

Summary shape (parity with `AuthorityMatch.payload.ai_verdict`):
`overall`, `name_ok`, `type_ok`, `role_ok`, `reasoning`, `model`,
`judged_at`, `cache_key`, `session_id`, `evaluator`.

The frontend's `useApprovalStore` polls `/entities` and emits
`mhm.entities.refreshed` whenever a verdict lands, so the inline
pills on the EntityTable refresh without the user reloading the page.

### Rule W-18 — Stage-2 verify state_dir per-RUN (matches Rule W-14)

`extraction-verify-sessions/<run_id>/` is the shared per-run state
dir; per-session artefacts live one level deeper under
`sessions/<session_id>/`. The eval-agent's verdict cache lives at the
state_dir root so opening the modal again warm-hits prior Gemini
judgements. The per-session subdir holds only the filtered fixture
(marc_extracted.json + ner_results.json) and the SSE trace audit log.

Same trust boundary as Rule W-15: the eval-agent is a subprocess
only; no Python imports across the FastAPI ↔ eval-agent boundary.

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

### Rule W-20 — Public endpoints carry the full spam + brute-force stack (added 2026-06-01)

Two endpoints on the web port answer unauthenticated traffic:
`POST /api/access-request` (self-service request-access form) and
`POST /api/auth/login`. Both MUST carry the defences below; the
unit + e2e suites are the regression barrier.

- **slowapi rate-limit** per client IP (extracted from
  `X-Forwarded-For`): 3/hour on `/access-request`, 10/minute on
  `/auth/login`. In-memory storage on the single Heroku dyno today;
  the upgrade path is Heroku Redis Mini behind
  `RATELIMIT_STORAGE_URI` once we scale past one web dyno.
- **Timing-parity dummy Argon2 verify** on `/auth/login`'s
  missing-user branch so response time cannot leak account
  existence.
- **`/access-request` only**: Cloudflare Turnstile token verified
  server-side + honeypot field that must stay empty + free-text
  justification of at least 40 characters + double opt-in (email
  confirmation token before the admin queue sees the row).
- **All outbound mail** routes through `EmailSender` (Resend wrapper
  at `app/services/email.py`) which enforces a per-recipient
  throttle of max 1 email per 60 s and 5 per 24 h via the
  `EmailThrottle` Postgres table. `EmailSender` NEVER raises into
  the request path — it logs and degrades to a no-op so a Resend
  outage cannot turn the form into a 5xx.
- **Strict account non-enumeration**: `/access-request` returns the
  same generic `202` body whether the email is brand new, already
  has a pending request, or already has an account. The
  already-has-an-account branch sends an out-of-band "someone tried
  to register with your email" notice email to the existing user
  instead of leaking the collision.

Tests: `backend/tests/test_access_request_router.py` (~13 cases),
`test_email_throttle.py` (6), `test_login_timing_parity.py` (3),
`frontend/e2e/access-request.spec.ts` (~8). Anything added to
either public endpoint MUST extend at least one of those suites.

### Rule W-21 — Every curator decision routes through the entity_event log (added 2026-06-02)

Five entity types are versioned by the per-entity event log in
`project_events`: `marc_record`, `extraction_entity`,
`authority_match`, `wikidata_override`, `wikibase_item`. Every
mutation (any PATCH/POST/DELETE that touches a curator-mutable field)
MUST route through `backend.app.versioning.apply_event(...)` BEFORE
the read-model table is updated. The read-model tables
(`run_records`, `extraction_approvals`, `authority_matches`,
`wikidata_item_overrides`) stay as O(1) current-state caches; the
`project_events` table is the authoritative source of truth.

Storage model: the hot tier in `project_events` keeps the last 1000
events per entity (pruned daily at 03:05 UTC via Heroku Scheduler;
the latest create or snapshot event is never deleted). The cold tier
in `entity_snapshot` keeps 3 snapshots per UTC day forever, written
at 00:05 / 08:05 / 16:05 UTC. Additionally, every 50th event on a
given entity auto-writes a snapshot so `state_at_rev` replay cost
stays bounded.

Library: `jsonpatch>=1.33` (RFC 6902). `apply_event` auto-computes
each patch via `jsonpatch.make_patch(prev, new)`. Revert is implemented
as `jsonpatch.make_patch(current, target)` applied as a fresh revert
event — old events are never mutated.

History endpoints live under `/api/projects/{id}/history/*` (timeline,
diff, at, revert, snapshots). The diff/at/revert routes gate on
project membership via `require_viewer` / `require_editor`.

Tests pinning the contract: `backend/tests/test_versioning_core.py`
(~15 cases), `test_history_router.py` (~11),
`test_versioning_integration.py` (~5),
`test_snapshot_prune_jobs.py` (~8),
`frontend/e2e/history-timeline.spec.ts` (~7). Any new mutation
surface MUST extend the integration suite with a matching
`test_*_emits_*_event` case.

Backfill (one-shot, idempotent):
`heroku run -- bash -lc "cd backend && python -m scripts.backfill_versioning"`.

### Rule W-22 — Project state + history are exportable as one JSON bundle (added 2026-06-02)

Three GET endpoints under `/api/projects/{id}/export/*` produce
`application/json` attachment downloads:

- `/export` — full bundle: current state of every entity type (MARC
  records, extraction approvals, authority matches, Wikidata
  overrides, Wikibase items) plus run metadata. Supports an
  `?entity_types=` repeat-query filter to slice by type.
- `/export/snapshots` — cold-tier archive: every `entity_snapshot`
  row for the project, optionally filtered by `entity_type` and
  `since` (YYYY-MM-DD). Survives the 1000-event prune.
- `/export/history` — full event log: either every event for one
  entity (`entity_type` + `entity_id`) or every event in the
  project.

Endpoints gate on project membership via `require_viewer`. PII
columns (email, name) are decrypted before serialisation — the
export must never leak ciphertext bytes. Response shape is defined
by Pydantic models in `backend/app/schemas/export.py`. Big exports
stream via `StreamingResponse` so the 100 MB project bundle does
not load fully into RAM, and the response carries
`Content-Disposition: attachment; filename=...` so the browser
treats it as a download.

Frontend: `frontend/src/api/export.ts` (typed client + browser
download trigger) + `ExportButton` + `ExportProjectDialog`. The
project header toolbar carries the "📥 Export…" entry-point.

Tests: `backend/tests/test_export_router.py` (8). Any addition of
a new entity type to the export bundle MUST extend
`/export?entity_types=...` and add a matching
`test_export_*_filter` case.

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
| `modal/modal_app.py` | Modal app for the four NER + genre models (deployed; not imported by backend) |
| `modal/README.md` | Modal deploy + economics |
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

# Modal — deploy the NER app (after editing modal/modal_app.py)
cd modal && modal deploy modal_app.py

# Modal — tail container logs (for cold-start debugging)
modal app logs mhm-ner

# Switch extraction to Modal (heroku)
heroku config:set EXTRACTION_MODE=modal MODAL_NER_URL=https://<workspace>--mhm-ner-mhmner-web.modal.run
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
