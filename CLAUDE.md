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

### Rule W-33 — Verify state on Heroku is writable under `/tmp` (added 2026-06-17)

On Heroku dynos the slug filesystem is read-only. All three verify
channels (`ai-verify-sessions`, `extraction-verify-sessions`,
`wikidata-verify-sessions`) MUST persist session traces + eval-agent
`runs/` artefacts via `resolve_verify_state_dir()` in
`agent_runner.py`, which defaults to `EVAL_AGENT_STATE_DIR` or
`/tmp/mhm-eval-agent-state` when `DYNO` is set.

`scripts/start.sh` exports both `EVAL_AGENT_ROOT` (bundled
`eval-agent/`) and `EVAL_AGENT_STATE_DIR`. `scripts/release.sh`
fails fast if `locate_eval_agent()` cannot find `eval_agent/cli.py`.

Postgres `inference_cache` (kind `ai_verdict`) remains the durable
cross-dyno verdict tier; `/tmp` state is ephemeral per dyno lifetime
but sufficient for in-session replay and eval-agent subprocess I/O.
Completed background verify jobs also embed ``session_snapshot`` in
``run_jobs.result`` so session GET handlers survive multi-dyno routing.
While a verify job is still running, ``verify_job.py`` also writes a
partial ``session_snapshot`` into ``run_jobs.progress`` on every streamed
event so ``useVerifyJob`` can hydrate ``VerdictsTable`` live without
reading the worker dyno's ``/tmp`` trace.

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
  `/auth/login`. Storage backend: Heroku Redis Mini (provisioned
  2026-06-03, `$3/month`) via `RATELIMIT_STORAGE_URI`; falls back
  to in-process memory when `REDIS_URL` is absent (dev/CI).
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

Heroku Scheduler job schedule summary:
- 00:05 / 08:05 / 16:05 UTC — `scripts.run_snapshot` (entity snapshots)
- 02:05 UTC — `scripts.run_prune_inference_cache` (expired cache rows)
- 03:05 UTC — `scripts.run_prune_events` (1000-event-cap prune)

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

### Rule W-26 — Wikidata Studio build result is cached in Postgres (added 2026-06-03)

`backend/app/models/wikidata_studio_cache.py::WikidataStudioCache`
caches the last successful `build_studio` result per `(run_id,
approved_only)` keyed by `input_fingerprint` (SHA-256 over
records + matches + entities + overrides, computed by
`pipeline/wikidata_studio.py::compute_build_fingerprint`).

On every call to `POST /wikidata-studio/build`:
1. Load raw data, compute fingerprint.
2. If `WikidataStudioCache` hit with same fingerprint → return
   cached `result_items`, `quickstatements`, `summary` instantly.
3. On miss, run full `build_items_for_run`, upsert cache row.

Cache is invalidated automatically whenever any input changes
(new approvals, new matches, overrides edited). Never invalidate
manually — changing the input data is sufficient.

Migration: `0015_wikidata_studio_cache`.

### Rule W-25 — Redis L1 in front of the Postgres inference cache (added 2026-06-03)

`backend/app/cache/redis_client.py` exposes `get_redis()` — a lazy
singleton that returns `None` when `REDIS_URL` is absent (dev/CI
falls back to Postgres-only with no code-path change).

`backend/app/pipeline/inference_cache.py::cache_lookup_or_call`
now uses a two-tier lookup:

1. **Redis GET `ic:{kind}:{hash}`** (<1 ms, in-memory)
2. **Postgres SELECT** (backfills Redis on hit)
3. **`fetch()`** (Modal / VIAF / Wikidata / …) — writes both tiers

Redis TTL policy: `ner.*` and `genre.classify` — no expiry
(content-addressed). `ai_verdict` — 7-day hot window; Postgres
holds 90-day ground truth. `authority.*` — 24 h hot window;
Postgres ground truth is 90 days (mazal), 30 days (viaf/wikidata),
or 180 days (kima).

`hit_count`/`last_hit_at` Postgres UPDATEs are skipped for Redis
hits (intentional — that UPDATE was the main warm-run latency cost).

`close_redis()` is called in the lifespan teardown in `main.py`.
The `slowapi` rate limiter shares the same Redis instance via
`RATELIMIT_STORAGE_URI`.

**Do not bypass this layer** for new inference call sites. All new
external calls (new authority APIs, new LLM calls) MUST go through
`cache_lookup_or_call`; the Redis tier is automatic.

### Rule W-23 — KIMA / VIAF / Mazal payload completeness (added 2026-06-03)

- `payload.cluster_ids` MUST be populated from VIAF `meta`
  (gnd/lc/isni/bnf/j9u) — never `{}` when VIAF matched.
- `payload.preferred_name_lat` uses VIAF authority form > Mazal >
  MARC heading fallback.
- `payload.preferred_name_heb` stores Mazal Hebrew preferred name
  when Mazal details are available.
- `payload.kima_*` fields store the full KIMA index row (id, heb,
  rom, lat, lon, geonames) for place matches via
  `DesktopMatcher._kima_enrich_place`.
- `payload.sources` MUST include `"kima"` when KIMA resolved the
  place — not attributed to `"wikidata"` alone when only KIMA
  supplied the QID.

### Rule W-26 — Every Wikidata P/Q constant in property_mapping.py must be verified live (added 2026-06-04)

The 2026-06-04 property audit found three silent catastrophes in
`backend/converter/wikidata/property_mapping.py`:

1. **`Q_PALIMPSEST = "Q179808"`** was the **Palme d'Or** (Cannes film
   award). Every palimpsest manuscript was tagged `P31 = Palme d'Or`.
   Correct QID: `Q274076`.
2. **`P_NUMBER_OF_FOLIOS = "P7416"`** — P7416 "folio(s)" is a STRING
   **citation qualifier** ("this statement references folio 15r of source
   X"), not a count property. Folio count must use P1104 (number of pages)
   with unit Q107256474 (leaf). P7416 as a folio-reference qualifier on
   colophon/scribal-intervention statements is correct.
3. **P50 directly on manuscript items** — Property:P50 has an explicit
   Wikidata constraint: *"use exemplar of (P1574) to connect the manuscript
   to the work(s) it contains; never connect directly the manuscript to the
   author(s)"*. The correct chain: `manuscript → P1574 → work → P50 → person`.

**Invariants enforced by the moat layer (`item_validator.py`):**

- `P50_ON_MANUSCRIPT` (error): P50 on any `entity_type="manuscript"` item.
- `P7416_AS_QUANTITY` (error): P7416 with `value_type="quantity"`.
- `P31_WRONG_QID` (error): any P31 value in `_KNOWN_BAD_P31_QIDS`
  (Q179808, Q5 on manuscripts, …).

**Regression tests** (14 cases in `backend/tests/unit/test_item_validator.py`):
`TestP50OnManuscript`, `TestP7416AsQuantity`, `TestP31WrongQid`,
`TestBuilderNeverViolatesNewChecks`.

**When adding or modifying a P/Q constant:**
1. Open `https://www.wikidata.org/wiki/Property:PXXX` (or `/QXXX`).
2. Read the **Statements** panel: confirm the label, data type, and
   any scope-note constraints match your intended use.
3. For `P_*` constants: check the *item of property constraint* notes —
   these are the community's formal "never use this on X" rules.
4. Add a `_KNOWN_BAD_P31_QIDS` entry and a unit test if you're removing
   a wrong constant so it can never silently sneak back in.
5. Never add a constant derived from memory or documentation alone —
   always fetch the live page. Constants can change (merges, redirects).

### Rule W-27 — Wikidata Studio curator controls (added 2026-06-04)

Five new controls were shipped in one session; any future Studio work must
respect these invariants:

**Force-rebuild toggle** (`?force_rebuild=true` on `GET /wikidata-studio`):
Bypasses the `WikidataStudioCache` fingerprint lookup and always runs a
full rebuild. The cache-write at the end still fires so the next normal
GET is served from cache. UI: "Skip cache (force fresh build)" checkbox
next to the Rebuild button.

**Validator badge** (`validation_issues` in build response):
`item_validator.validate_item(item)` is called for every built item inside
`_build_sync`; issues are returned as `[{code, severity, message}]` on each
serialised item. The frontend renders a red/yellow dot in the sidebar and
inline alert chips in the ItemPanel header. This surfaces the moat-layer
checks (P50_ON_MANUSCRIPT, P31_WRONG_QID, etc.) to curators before upload.

**Item-level approval** (`approved: bool | None` on `ItemOverridePayload`):
Stored in `wikidata_item_overrides.payload.approved`. The QS download
(`?approved_only=true`) and upload (`POST /upload` with
`upload_approved_only=true`) filter to approved items only when the flag is
set. UI: `ItemApprovalBadge` (○ / ✓) in sidebar and ItemPanel header.

**Inline statement exclude** (`remove_statements` via PATCH):
Each statement row in the ItemPanel gets a ✗ Exclude / Undo button.
Clicking immediately PATCHes `{remove_statements: [...current, i]}` on the
override and strikes through the row optimistically. No new DB column — this
reuses the existing `remove_statements` list in `ItemOverridePayload`.

**Approved-items-only upload/QS gate**:
"Approved items only" checkbox + live "N of M approved" count badge in the
Studio header. When active, QS download appends `&approved_only=true` and
upload POST includes `upload_approved_only: true`.

DB migration: `0016_item_override_approved` adds `approved BOOLEAN` to
`wikidata_item_overrides` (nullable; null = not reviewed).

### Rule W-28 — Mazal + KIMA live in Heroku Postgres (added 2026-06-05)

**Production default since 2026-06-05.** Mazal (2.5 M authority records +
5.3 M name-index rows, ~980 MB SQLite → ~600 MB Postgres) and KIMA
(48 K places + 129 K name-index rows, 15 MB) are imported into the same
Heroku Essential-1 Postgres instance via:

```bash
# One-time import — run locally pointing at Heroku DATABASE_URL
# Idempotent (TRUNCATE + re-import).
cd backend && DATABASE_URL=... KIMA_DB_PATH=backend/data/kima/kima_index.db \
  .venv/bin/python -m scripts.import_kima_to_postgres   # ~15 s

cd backend && DATABASE_URL=... MAZAL_DB_PATH=.../mazal_index.db \
  .venv/bin/python -m scripts.import_mazal_to_postgres  # ~10 min
```

`backend/app/pipeline/authority_backend.py` now has three implementations:

| Class | `AUTHORITY_MODE` | Notes |
|---|---|---|
| `PostgresAuthorityBackend` | `postgres` | Direct Postgres SELECT over `mazal_*` / `kima_*` tables. **Production.** |
| `LocalAuthorityBackend` | `local` | Local SQLite via `asyncio.to_thread`. Works in dev when SQLite files are present. |
| `ModalAuthorityBackend` | `modal` | Legacy Modal HTTPS endpoint. Kept as fallback; no longer deployed. |

Set on Heroku:
```bash
heroku config:set AUTHORITY_MODE=postgres
```

**Postgres tables (created by migration `0018_authority_pg_tables`):**
- `mazal_authorities` — nli_id PK, entity_type, preferred_name_heb,
  preferred_name_lat, dates, aleph_id
- `mazal_name_index` — normalized_name (hash idx), nli_id, entity_type,
  script
- `kima_places` — kima_id PK, primary_heb/rom, wikidata_id, viaf_id,
  geonames_id, mazal_nli_id, lat, lon
- `kima_name_index` — normalized_name (hash idx), kima_id, script

Hash indexes are used for both `normalized_name` columns — exact-match
only, no btree 8191-byte limit. A corrupt KIMA name_index entry
(~900 KB blob) is silently skipped by the import script.

The inference cache (`Rule W-12`) still sits ABOVE this layer —
`authority.mazal` (90-day Postgres, 24 h Redis) and `authority.kima`
(180-day Postgres, 24 h Redis) wrap every Postgres lookup, so the first
resolution populates the cache and subsequent calls hit Redis or the
inference_cache table without touching `mazal_*`/`kima_*`.

**Within-request double-call elimination** is preserved:
`_mazal_match_person` and `_kima_match_place` store full detail dicts in
`_mazal_detail_cache`/`_kima_detail_cache` (per-instance) so
`_mazal_get_details`/`_kima_enrich_place` don't issue a second SELECT.

### Rule W-29 — Authority payload completeness (added 2026-06-05)

Every `AuthorityMatch.payload` produced by `DesktopMatcher._match_one`
MUST carry these fields when available:

| Field | Source | Used by |
|---|---|---|
| `viaf_uri` | VIAF match | RDF `owl:sameAs`, Wikidata P214 |
| `wikidata_uri` | Wikidata QID | RDF `owl:sameAs` |
| `preferred_name_lat` | VIAF > Mazal > MARC | RDF labels, Wikidata preferred label |
| `preferred_name_heb` | Mazal > `wikidata_he_label` | RDF labels, Wikibase Hebrew label |
| `cluster_ids` | VIAF cluster (gnd/lc/isni/bnf/j9u) | Wikidata identifier statements |
| `mazal_aleph_id` | Mazal details | NLI identifier in Wikidata (P8189) |
| `mazal_dates_raw` | Mazal dates string | Debug / date-resolver fallback |
| `wikidata_he_label` | SPARQL `rdfs:label@he` | `preferred_name_heb` fallback |
| `wikidata_en_description` | SPARQL `schema:description@en` | Wikidata/Wikibase description |
| `kima_id/heb/rom/lat/lon/geonames/viaf_id` | KIMA row | RDF place nodes (WGS84, GeoNames) |

New `_wikidata_enrich_qid` makes one SPARQL call per confirmed QID for
`he_label` + `en_description` + P214 VIAF cross-reference. Cached 30 days
under `authority.wikidata / op=enrich_qid`. VIAF cross-enrichment from
Wikidata P214 fills `viaf_id` when the VIAF SRU matcher returned no hit.

### Rule W-24 — All four curator surfaces support per-field manual editing (added 2026-06-03)

- **NER**: `override_text`, `override_type`, `override_role` in
  `extraction_approvals`; API key mapping lives in
  `frontend/src/api/extractionApprovals.ts` (UI sends `{type, role,
  text}` → backend `{override_*}`).
- **Authority**: `AuthorityMatchEdit` PATCH
  (`/matches/{id}/edit`) wired to `AuthorityMatchEditDialog`.
- **Wikidata Studio**: `ItemOverridePayload` PATCH wired to
  `ItemOverrideDialog`; override applied at next build.
- **HMO Studio**: `RecordEdit` PATCH wired to `MarcFieldEditorDialog`
  via `GET /runs/{id}/records` picker; requires RDF + manifest
  rebuild to apply downstream.

### Rule W-30 — Wikidata upload is fail-closed: reconcile-before-create + validator gate IN the write path (added 2026-06-08)

Context: the 2026-06-07 bulk-deletion request (Epìdosis, ~5,948 items)
targets the OTHER April failure mode — mass *duplicate* creation and
non-notable items — distinct from the mass-*merge* that Rule W-4's
four-stage guard prevents. The 2026-06-08 audit found the create path
unguarded: the `/reconcile` endpoint was decorative (it mutated
in-memory items that `/upload` discarded by rebuilding fresh), manuscripts
reconciled by P8189 (which they never carry — they use **P3959**) so they
could never dedup, `reconciler._query` swallowed SPARQL errors as "absent"
→ CREATE, and `validate_item` ran only at build time as a UI badge.

Invariants now enforced in `backend/app/pipeline/wikidata_upload.py`
(NOT in the byte-identical `uploader.py` — Rule W-4):

- **Reconcile happens INSIDE `upload_items` / `_upload_sync`**, not just in
  the `/reconcile` preview. `_prepare_for_upload(items, reconciler)` is the
  single gate every item passes before any write (dry-run and live alike, so
  the dry-run preview is truthful). The `/reconcile` endpoint is PREVIEW
  ONLY and must never be relied on to change upload behaviour.
- **Each creatable entity type has a dedup path.** Manuscripts reconcile by
  P3959 (`reconcile_manuscript_by_identifiers` → P3959 then shelfmark);
  persons go through the conflict-checked `reconcile_person_by_identifiers`
  (cross-identifier verification — the anti-conflation guard from
  2026-04-13); works go through `reconcile_work_by_label_and_author` (rejects
  a candidate whose P50 author differs). The raw first-match path is gone.
- **Fail closed on lookup error.** `reconciler._query` raises
  `ReconciliationUnavailableError` (NOT `[]`) on any network/429/5xx. An item
  whose lookup can't complete is BLOCKED (`status="blocked"`), never created —
  a transient WDQS outage must never be read as "no existing item".
- **`validate_item` is a HARD GATE before write**, not advisory. Any
  ERROR-severity issue (NO_IDENTIFIER, KOVETZ_PLACEHOLDER, P50_ON_MANUSCRIPT,
  P31_WRONG_QID, …) blocks the write regardless of create-vs-update and
  regardless of UI approval state.

Tests pinning the contract: `backend/tests/unit/test_wikidata_upload_guards.py`
(15 cases). Any new external-write path or reconcile change MUST extend it.

**Residual gap (NOT yet closed):** the QuickStatements download
(`/quickstatements.txt`) emits raw CREATE lines with no reconcile/validate
gate — a curator pasting it into the QuickStatements tool bypasses these
guards. The `item_approved_only` filter is the only control there. Close
this (or document the manual-review requirement) before any bulk QS import.

### Rule W-31 — Authority Enrichment review surface parity with extraction (added 2026-06-09)

The Authority candidates page surfaces a rich entity-review UI that mirrors
the extraction EntityTable layout. Five independent capabilities owned by
`frontend/src/components/authority/`:

1. **9-column candidates table** (`AuthorityTable.tsx`): Record · Entity ·
   Role · Source · Conf. · Guards · AI verdict · Approved · Edit. Sortable
   headers with `CONF_ORDER` / `VERD_ORDER` custom orderings; per-column
   `ColumnFilterPopup` (right-click or ▾ button); free-text search inputs
   directly in the Record and Entity header cells; guard-flag chips rendered as
   `⚠ flagname` with `title={guardExplain(g)}`.
2. **Per-column distinct-value popup**: reuses `ColumnFilterPopup` from
   `frontend/src/components/extraction/`. Active filter chips with
   clear-per-column + "Clear all". OR within a column, AND across columns.
3. **`AuthorityDetailDrawer`** (`AuthorityDetailDrawer.tsx`): right-side
   fixed drawer (640 px), z-40, translateX animation. Pin + Esc-to-close.
   4 scrollable cards: Match (IDs, preferred names, KIMA geo), Confidence &
   Sources (badge + reasoning + source chips + guard chips), Dates (ms/birth/death
   years), AI Verdict (verdict pill + reasoning + model). Owns its edit dialog
   (`AuthorityMatchEditDialog`) and MARC popup internally. Replaces the old
   5-tab `MatchDetailDialog` modal.
4. **`AuthorityAutoApproveRuleBuilder`** (`AuthorityAutoApproveRuleBuilder.tsx`):
   rule modal with confidence/source/entity-kind/min-source-count controls,
   `require_ai_pass` + `respect_ai_fail` toggles, and debounced live preview
   (350 ms) via `POST /runs/{id}/matches/auto-approve/preview`. Apply calls
   `POST /runs/{id}/matches/auto-approve`. `AuthorityAutoApproveRule` Pydantic
   schema lives in `backend/app/schemas/runs.py`.
5. **`onFilteredChange` callback**: `AuthorityTable` reports its current
   filtered match IDs to `RunDetail` via `onFilteredChange(ids)` on every
   `display` change (via `useEffect`), keeping the toolbar count badge and
   "Verify all visible" scope in sync with the table's internal filter state.

Backend helper `_apply_auto_approve_rule()` in `backend/app/routers/runs.py`
filters unapproved matches by confidence_levels, sources (intersection),
entity_kind, min_source_count, and AI verdict gates.

### Rule W-32 — Non-production provenance-event places on the maps (added 2026-06-14)

Web counterpart of desktop **Rule 60**. The corpus-movement + single-MS
provenance maps now plot custody events beyond production: acquisition
(MARC 541 $b), conservation/exhibition (MARC 583 $j), and institutional
ownership (named collections → seat). One additive record channel,
`record["provenance_events"]` (`{type, place_text, agent_name, year,
year_earliest, year_latest, source_field, lat, lon, wikidata_id,
certain}`), flows ingest → KIMA → RDF → maps. Production stays in
`record["place"]` — never regressed.

- **Ingest** (`app/pipeline/marc_ingest.py`): `_extract_provenance_events`
  reuses the vendored desktop `FieldHandlers` helpers so the `.mrc` path
  (desktop `extract_all_data`) and the TSV/JSON collapsed-key path emit
  byte-identical events. `extract_named_entities` yields a `*_place`-role
  place entity per event.
- **Authority** (`app/pipeline/authority.py`): `is_place` accepts any
  `*_place` role → KIMA fires. After a KIMA miss, the Ashkenazi gazetteer
  (`app/pipeline/ashkenazi_gazetteer.py` + `data/ashkenazi_communities.json`)
  fills the diaspora gap; a coord-bearing place survives the no-id drop.
  `research_geo_enrich.institution_place` resolves collection/library seats
  (P159→P276→P131→P625; abstains on humans). The router chains
  `owner_place` → `institution_place`; owner loops accept `organization`.
- **RDF** (`app/pipeline/rdf_build._merge_authority_ids` +
  `converter/rdf/graph_builder._add_provenance_events`): KIMA coords are
  written back onto the matching event; a CIDOC `E8/E10/E7` node +
  `P7_took_place_at` → `E53_Place` with `wgs84:lat/long` (+ `owl:sameAs`)
  + `P4_has_time-span` is emitted, gated on coords (**never fabricated**).
  `hm:mentions_place` makes `query_geography` surface it.
- **Maps**: `research_provenance_map.build_provenance_map` emits typed +
  dated `acquisition`/`conservation`/`exhibition` stops; `corpus_movement`
  adds `event_places` and folds them into the corpus place facet. Frontend
  `ProvenanceMapPanel.tsx` (`KIND_COLOR`/`KIND_LABEL`/`Legend`) +
  `api/research.ts` (`MapStopKind`, `CorpusEventPlace`) gained the kinds.

Existing runs must **rebuild RDF** for the Geography tab to pick up the
new places; the DB-sourced movement maps benefit immediately. 561
free-text NER is deferred (no ML). Tests:
`tests/test_provenance_events_ingest.py` (10),
`test_provenance_events_rdf.py` (5), `test_institution_place.py` (9),
`test_ashkenazi_gazetteer.py` (9), + Rule-60 cases in
`test_provenance_map_guards.py` and `test_corpus_movement.py`.

### Rule W-33 — Authority matcher routing, deduplication, and notes grounding (added 2026-06-17)

Five invariants established during the authority-enrichment fix session
(supervisor review, Gilla's 2026-06 feedback):

**Matcher routing by entity kind:**
- Person matchers (`_mazal_match_person`, VIAF, Wikidata person-name SPARQL) must
  NOT fire for place or work entities. The guard is in `_match_one` via `is_place`
  and `normalized_kind != "work"` checks.
- Place entities first run KIMA (`_kima_match_place`), then Mazal place
  (`_mazal_match_place_authority`). Person matchers are bypassed.
- Work entities (kind="work") call `_mazal_match_work` only; VIAF and Wikidata
  person matchers are not invoked.
- `kima_payload.mazal_nli_id` is backfilled into `mazal_id` when the Mazal place
  lookup misses but KIMA has a linked NLI ID.

**MARC $d dates for homonym resolution:**
- `_collapse_marc_subfields` captures `$d` from MARC 100/600/700 fields and
  propagates them as `dates` on author/contributor/subject entity dicts.
- `PostgresAuthorityBackend.match_person(dates=...)` tries an exact name + dates
  match first, then falls back to the main_marc_tag-ordered query.
- Postgres ORDER BY: `CASE a.main_marc_tag WHEN '100' THEN 1 … END` so the
  אישיות record (tag 100) is always preferred over the נושא record (tag 150)
  when both share the same normalized name. Requires migration 0020 + re-import.

**Mazal personality guard (`guard_mazal_subject_heading`):**
- Fires when `main_marc_tag != '100'` for a person author/contributor entity.
  Downgrades confidence to "medium" and stamps flag `mazal_subject_not_personality`.
  Does NOT fire for subject-role entities (600), which may legitimately hit tag 150.
- `HardeningContext` now carries a `role` field; `apply_hardening_guards` passes it
  to the guard. The `prelim["payload"]["main_marc_tag"]` slot is populated from
  `mazal_details` so the guard reads the actual Mazal match metadata.

**Deduplication (ingest + run):**
- `extract_named_entities` dedupes by `(normalize(text), kind)` with role-priority
  merge (author > contributor > subject > place). The replaced role is recorded in
  `alt_roles` on the winning entity for audit. Different kinds (place vs person)
  with the same name text are kept as separate entities — they are not collapsed.
- `run.py execute_run` uses a `(control_number, normalize(text), kind, role)` key to
  prevent inserting duplicate `AuthorityMatch` rows within a single ingest run.
- Re-enrich upsert key in `runs.py` includes `role` so author and subject rows for
  the same entity text are updated independently.

**Notes / colophon / work-title grounding:**
- `_collapse_marc_subfields` detects colophon keywords (קולופון, colophon, …) in
  `500$a` and sets `colophon_text`. `_extract_colophon_fields` then extracts
  `colophon_year` (Hebrew gematria or Gregorian) and `colophon_scribe` (patronymic).
- `_extract_work_mentions` scans `500$a` raw text for `כולל:` / `ובו:` / `מכיל:`
  patterns and emits `work_mentions` list. These flow into `extract_named_entities`
  as `{kind: "work", role: "contained_work"}` entities.
- `MarcStructuredIndex` keys now include `notes`, `colophon_text`, `colophon_year`,
  `colophon_scribe`, `work_mentions` so the Exists-in badge reflects note-sourced hits.
- `extraction.py` now wires `filter_person_role_dedup` (collapse same-name NER
  multi-segment duplicates) and `filter_with_marc_grounding` (stamps grounded /
  exists_in fields) after the existing post-filters.

**Mazal Postgres schema:**
- Migration 0020 adds `main_marc_tag TEXT` to `mazal_authorities`.
- `mazal_index.py` parse_record now ingests 150/450 (subject) tags as
  `entity_type='subject'` and records the primary heading tag in `main_marc_tag`.
- `import_mazal_to_postgres.py` detects whether the source SQLite has the new column
  (idempotent: old SQLite files still import cleanly with `main_marc_tag=NULL`).
- Re-import command after migration:
  `cd backend && DATABASE_URL=... MAZAL_DB_PATH=... .venv/bin/python -m scripts.import_mazal_to_postgres`

**Curator re-enrich playbook:**
After deploying this change, run Authority re-enrich on reviewed runs to refresh
matches without re-uploading MARC files:
`POST /api/runs/{run_id}/authority/re-enrich?skip_cache=true`
(Available via the run detail page → Authority tab → "Re-enrich" button.)

Tests pinning the contract:
`backend/tests/unit/test_authority_routing.py` (11),
`backend/tests/unit/test_authority_supervisor_examples.py` (13),
`backend/tests/unit/test_colophon_structured.py` (6),
`backend/tests/unit/test_notes_work_extraction.py` (5).
Any new matcher route, dedup policy change, or note-extraction pattern MUST extend
at least one of these suites.

### Rule W-37 — Homonym abstain, scoring, and curator picker (added 2026-06-25)

When multiple Mazal **personality** rows share a normalized name and MARC `$d`
does not disambiguate, the matcher **abstains** rather than guessing:

- `homonym_scoring.pick_mazal_candidate` scores candidates (+100 tag 100,
  +50 date overlap, +20 ms_year plausibility, penalties for fuzzy / tag 150).
  **Abstain** when top two scores are within 15 points and neither has date overlap.
- On abstain: empty `mazal_id`, no VIAF SRU / Wikidata label search for persons,
  flag `homonym_unresolved`, `payload.homonym_candidates` (top 5).
- Curator resolution: `GET/POST …/matches/{id}/candidates` + `pick-candidate`;
  Authority detail drawer **Pick** control.
- `is_short_name_homonym` (stage3_guards): ≤2 tokens or ≤12 Hebrew letters;
  Mazal corroboration only when tag 100 + (date overlap OR single personality).
- Auto-approve blocks: `homonym_unresolved`, `short_name_homonym`,
  `mazal_subject_not_personality`, `viaf_date_mismatch`, `cross_source_conflict`,
  `wikidata_disagrees`.
- VIAF: skip SRU when Mazal personality confirmed; `guard_viaf_date_mismatch` strips
  `viaf_id`; crosscheck enabled in match path unless `MHM_DISABLE_WIKIDATA_CROSSCHECK=1`.
- Editorial notes: `_extract_editorial_metadata` → `editor_names`, edition fields,
  `role=editor` entities, searchable via note-index.

Tests: `test_homonym_scoring.py`, `test_viaf_mazal_guards.py`,
`test_editorial_extraction.py`, extended supervisor examples,
`frontend/e2e/authority-homonym-picker.spec.ts`.
Sync `stage3_guards.py` edits to desktop `converter/authority/` when changed.

### Rule W-38 — Run jobs are claimed, heartbeated, and reconciled (added 2026-07-04)

The background job runner (`run_job_service.py`) is multi-dyno safe via
row ownership, not a queue broker:

- **Ownership**: `run_jobs.claimed_by` stores `WORKER_ID`
  (`{DYNO-or-hostname}:{uuid8}`). `_try_claim_job` is a single atomic
  conditional UPDATE — a job is claimable iff `queued`, or `running` and
  (unowned | mine | same-dyno prefix | heartbeat stale). Never flip a row
  to `running` outside this claim path.
- **Heartbeat**: a lifespan-started `run_job_maintenance_loop()` ticks
  every 60 s — bump `updated_at` on rows whose asyncio task is alive here,
  then `fail_stale_jobs()` (`STALE_JOB_AFTER` = 5 min), then respawn
  queued orphans older than `ORPHAN_GRACE`. Workers no longer need to
  call `update_job_progress` merely to stay alive.
- **Dedup**: partial unique index `uq_run_jobs_active_kind` on
  `(run_id, kind) WHERE status IN ('queued','running')` (migration 0030);
  `create_job` catches the `IntegrityError` race and re-raises
  `ActiveJobError` → the existing 409-attach protocol.
- `claimed_by` is intentionally NOT in `serialise_job` — API/frontend
  payloads are unchanged.

Tests: `tests/unit/test_run_job_recovery.py` (claim/heartbeat/tick/race
cases). Any change to claiming, staleness, or the maintenance loop MUST
extend that suite.

### Rule W-39 — Every on-disk build cache needs a durable Postgres counterpart (added 2026-07-06)

Incident: `GET /hmo-studio/coverage` kept returning 409
`hmo_coverage_in_progress` for the same run over and over. The report
itself was fine (the background job legitimately takes 9-14 minutes —
it parses the run's RDF TTL via rdflib twice), but
`hmo_pipeline.cache_coverage_report` only wrote the result to the
dyno's local filesystem. Every Heroku deploy or dyno restart wipes that
cache (`claimed_by` on the job rows confirmed a fresh `WORKER_ID` each
time), so the *same run* paid the full 9-14 minute rebuild repeatedly
even though its RDF graph never changed.

Fix: `app/models/hmo_coverage_cache.py::HmoCoverageCache` — one row per
`run_id`, keyed by a SHA-256 fingerprint of the RDF TTL bytes (migration
`0032_hmo_coverage_cache`). `GET /coverage` now falls back to this
durable cache (and re-seeds the on-disk file) before enqueueing a
rebuild job; the background job write-throughs both caches on success.

**General invariant**: any per-run build result cached only under
`backend/state/runs/{run_id}/...` (on-disk) MUST also have a Postgres
write-through, mirroring `RdfArtifact` (the original TTL-restore fix,
2026-07-04) and `HmoStudioItemCache`/`WikidataStudioCache` (which
already did this correctly). A cache that only survives on local disk
is not a cache on Heroku — it is state that silently evaporates on
every deploy and forces a full, possibly multi-minute, rebuild.

Tests: `backend/tests/test_hmo_studio_coverage_router.py`
(`test_coverage_restores_from_durable_db_cache_on_disk_miss`,
`test_coverage_stale_db_cache_still_enqueues_rebuild`).

### Rule W-40 — Never hold an open DB transaction across a slow/retrying external write (added 2026-07-06)

Audit trigger: `hmo_item_upload_job.py` runs `upload_items_for_run` (up
to ~7800 sequential Wikibase Cloud writes) inside one long-lived
`session_scope()` for the whole job — that part is the established
pattern (mirrors `authority_re_enrich_job.py`) and is fine, because
each item's DB write commits immediately after it's made. The bug was
one level down: `hmo_item_reconcile.py::reconcile_item` ran a Postgres
SELECT (`_source_uri_property_pid`) and left that transaction **open**
while it then made a SPARQL reconcile call and, on a miss, the caller
made a `create_item`/`update_item` call — `cloud_client.py`'s
`_post_with_retry` does up to 6 attempts with exponential backoff
(1s→2s→4s→8s→16s→30s, ~61s of sleep alone, more with request
timeouts on top). That is squarely the failure mode `app/db.py`'s
2-minute `idle_in_transaction_session_timeout` backstop documents
verbatim ("a request handler that opens a session, runs one query,
then hangs forever on... an external HTTP call with no timeout") — a
slow/flaky Wikibase Cloud response could get the connection killed by
Postgres mid-job, crashing the whole upload instead of just failing
one item.

Fix, two parts:
1. `reconcile_item` now takes an optional `pid=` kwarg. A caller
   looping over many entities calls the new `resolve_source_uri_pid(db)`
   **once** before the loop and passes it through — the schema-level
   property id doesn't change during a run, so re-querying it ~7800
   times was also pure waste. `hmo_item_upload.py::upload_items_for_run`
   does this now.
2. When `pid` isn't pre-resolved (the single-item preview endpoint,
   `hmo_studio_items.py::reconcile_hmo_item`), `reconcile_item` commits
   right after the SELECT and before making any network call — the
   transaction is never open across slow I/O, regardless of caller.

**General invariant**: a background job (or request handler) that
interleaves a DB read/write with a slow external call (HTTP with
retry/backoff, subprocess, SSE stream) must close out its transaction
(`commit`/`rollback`) *before* the slow call, not rely on the
2-minute idle-in-transaction timeout to eventually clean up — that
timeout is a backstop against bugs, not a scheduling mechanism.

Tests: `backend/tests/unit/test_hmo_item_reconcile.py`
(`test_pid_lookup_transaction_is_closed_before_the_sparql_call`,
`test_pre_resolved_pid_skips_the_redundant_lookup`),
`backend/tests/test_hmo_item_upload.py`
(`test_live_upload_resolves_reconcile_pid_once_not_per_entity`,
`test_dry_run_never_resolves_reconcile_pid`).

---

### Rule W-41 — HMO items table upload-outcome fields + upload-lifecycle AI verification (added 2026-07-07)

The HMO Wikibase Items review table (`HmoItemTable.tsx`) surfaces the *last
actual upload attempt* per item, not just the binary would_create/created
`status` derived from `wikibase_entity_mappings` presence:

- **`OPERATION_ADOPT`** (`backend/app/models/wikibase_cloud_write.py`) is a
  distinct audit-log operation from `OPERATION_CREATE`: it's recorded when
  `hmo_item_upload.py`'s reconcile-match branch links an entity to an
  *already-existing* live Wikibase item (found via `hmo_source_uri` SPARQL
  reconcile) instead of creating a brand-new one. Without this distinction
  "new entity" and "linked to a pre-existing item" were indistinguishable in
  the audit log — `outcome_message` also records the reconcile match reason.
- **`fetch_latest_wikibase_writes(db, run_id, *, channel, target_kind)`**
  (`backend/app/services/wikibase_audit.py`) is a portable (Postgres +
  SQLite) "latest row per `target_key`" query — a `GROUP BY target_key,
  MAX(created_at)` subquery joined back to `wikibase_cloud_writes`, no
  Postgres-only `DISTINCT ON` (the test suite shares a SQLite connection).
- **`fetch_merged_hmo_items()`** (`hmo_item_views.py`) joins the latest
  `item_upload` channel / `item` target_kind write per `source_uri` and adds
  three fields to every item dict: `upload_outcome` (`create`/`update`/
  `adopt`/`skip`/`unchanged`/`failed`/`None` if never attempted),
  `upload_message` (failure/adopt reason, `""` otherwise), `upload_at` (ISO
  timestamp). The existing `status` (`would_create`/`created`) field is
  untouched for backward compatibility.
- **Frontend**: `HmoStudioItem` (`frontend/src/api/hmoStudioItems.ts`) gained
  the three fields; `HmoItemUploadOutcomeBadge.tsx` renders a color-coded
  pill (create/adopt/update = success tone, skip/unchanged = muted, failed =
  danger tone with the message as a tooltip, `—` when never attempted) used
  by both `HmoItemTable.tsx`'s "Last upload" column (filterable via the
  existing right-click column-filter popup, `ColKey` extended with
  `"upload_outcome"`) and `HmoItemDetailDrawer.tsx`.

**Single-item live push** closes the "apply AI fix" loop without a full
corpus re-upload: `push_single_item(db, run_id, entity, *, writer, audit_ctx,
update_existing, reconcile_pid, existing_qid)` in `hmo_item_upload.py` is the
per-entity create-or-update body extracted out of `_pass_one_create`'s live
path (bulk upload behaviour is unchanged — it just calls this helper now).
`POST /runs/{run_id}/hmo-studio/items/{local_id}/push` builds a
`ResolvedWikibaseEntity` from the item's **override-merged** state (via
`fetch_merged_hmo_items`, so a curator-applied AI fix is included — the bulk
upload path does NOT apply overrides, only this single-item path does) and
calls `push_single_item` with `update_existing=True` always (create if new,
update if already mapped). Per Rule W-40, the endpoint commits its read
transaction (run lookup, item fetch, pid resolution) before the live
Wikibase Cloud / SPARQL call. Frontend: `HmoStudioItems.pushItem(runId,
localId)`; `HmoItemDetailDrawer.tsx` gained "Push to Wikibase now" and a
combined "Apply fix & push" button next to the existing "Apply AI fix".

**Upload-lifecycle AI verification** reuses the existing `hmo_wikibase_item`
(pre-upload audit) and `hmo_wikibase_item_autofix` (post-upload live-QID
compare) evaluators — no new evaluator was added. `JOB_KIND_HMO_ITEM_VERIFY
= "hmo_item_verify"` is a new background job kind
(`backend/app/models/run_job.py`); `verify_job.py::_open_verify_stream` gained
a branch mirroring `JOB_KIND_WIKIDATA_VERIFY` exactly but calling
`hmo_item_actions.get_action(...)` and `hmo_item_verify_event_stream(...)`,
reusing the already-exported `_fetch_verify_items` / `_prepare_verify_scope`
/ `_load_marc_records` helpers from `hmo_studio_items.py`.
`VERIFY_JOB_CHANNELS` (`verify_session_store.py`) and the verify-params
validation branch in `run_job_params.py` were extended the same way as the
other three verify kinds. `useVerifyJob`'s `kind` union and `RunJobKind`
gained `"hmo_item_verify"`.

In `ItemUploadPanel.tsx`, two curator-controlled, **default-off** checkboxes
wire this into the upload flow — per the "Auto-approve AI verdicts" hard
invariant (this app does NOT auto-approve or auto-fix; a curator always
confirms), verification runs automatically when opted in but a failed
pre-upload audit never silently blocks or silently proceeds:

- **"Verify with AI before upload"**: on click, fetches the current item
  list, scopes to `status === "would_create"` local_ids, starts a
  `hmo_item_verify` job (`action_id: "audit_hmo_wikibase_item"`), and shows
  an inline compact `AgentFlowDiagram` + `VerdictsTable` while it runs (via
  `useVerifyJob` → `fetchVerifySessionWithJobFallback` hydrating from
  ``run_jobs.progress.session_snapshot`` while the job is active, then
  ``result.session_snapshot`` at finish — the same Heroku-multi-dyno-safe
  path as the other three verify modals). If any verdict is `fail`, a confirm
  items" (opens the existing `HmoItemVerificationModal`, scoped to the
  failed ids — it re-verifies through the shared Postgres/Redis cache, so
  this is near-instant) or "Upload anyway" — never a silent block or a
  silent proceed. No fails ⇒ the actual upload starts automatically.
- **"Verify with AI after upload"**: when the live `hmo_item_upload` job
  succeeds, filters `result.outcomes` to status in `{created, updated,
  adopted}`, collects their `local_id`s, and auto-starts a
  `hmo_item_verify` job with `action_id: "autofix_hmo_wikibase_item"`
  scoped to just those ids. Verdicts + `suggested_fixes` persist
  automatically into `HmoStudioItemOverride.ai_verdict` via the existing
  `_persist_hmo_item_verdicts`, so the table's AI-verdict column and the
  drawer's "Apply AI fix" / "Push to Wikibase now" buttons light up with no
  further backend work.

Both checkboxes' helper tooltips note the per-item Gemini call is cached
(Rule W-25) and rate-limited by the action's existing `rate_limit_rpm` (60
for audit, 30 for autofix) — no new cost controls were needed.

Tests: `backend/tests/test_hmo_item_upload.py` (adopt records
`OPERATION_ADOPT`, `push_single_item` unit coverage),
`backend/tests/test_hmo_item_views.py` (`upload_outcome`/`upload_message`/
`upload_at` populated from `wikibase_cloud_writes`), a new
`test_verify_job_hmo.py` and `test_run_job_params_hmo_verify.py` covering
the `JOB_KIND_HMO_ITEM_VERIFY` branch, and `frontend/e2e/
hmo-wikibase-items.spec.ts` extended to cover the "Last upload" column and
the pre/post-upload verify checkboxes end-to-end with mocked routes (Rule
W-19).

---

### Rule W-42 — HMO item upload is SHACL-gated by default (added 2026-07-07)

Audit of run `48ba6c13`: 773/2131 built items had SHACL Violation/Error rows
but all 773 were already on the wiki because the upload path ignored
`HmoStudioItemCache.shacl_report` (block rule R13 read as "never gate").

Invariants now enforced:

- **`blocking_shacl_issues`** (`hmo_item_shacl_gate.py`) treats
  `Violation` and `Error` severities as hard blocks; warnings stay advisory.
- **`upload_items_for_run` / `push_single_item`** consult the cached report
  before any Wikibase call unless `allow_shacl_errors=true` (UI checkbox
  "Upload items with validation errors"). Blocked items emit `blocked` /
  `would_block` outcomes and audit `OPERATION_FAILED` with joined messages.
- **Never emit `und` labels** — `hmo_exporter._labels_for_node`,
  `label_sanitize.py`, and `cloud_client._apply_labels_descriptions_aliases`
  map unsupported language codes to `en` (fixes 50× `not-recognized-language`).
- **Exporter skips `ParadigmBridge` Wikibase items** — bridges stay in RDF +
  pass-2 deferred links only. RDF build adds `view_type` on cataloging views,
  dedupes duplicate `external_identifier_nli`, and strengthens paradigm-bridge
  endpoint typing.
- **Cleanup surface:** `GET …/hmo-studio/items/validation-errors?on_wiki_only=`
  lists items still on the wiki with blocking SHACL issues.

Tests: `test_hmo_item_upload.py` (SHACL block/allow/und),
`test_hmo_item_shacl_gate.py`, `test_hmo_item_views.py`,
`test_hmo_exporter_resolution.py` (no `und`, no ParadigmBridge export).

---

### Rule W-43 — RDF SHACL validation MUST NOT use RDFS inference; type nodes explicitly instead (added 2026-07-07)

Root cause of the 773/2131 SHACL Violation/Error rows from Rule W-42's audit
(run `48ba6c13`): `rdf_build.py::_run_shacl_sync` called
`ShaclValidator.validate(..., inference="rdfs")`. The ontology deliberately
reuses several properties (`forms_part_of`, `has_expression`, `has_work`,
`has_script_type`, `mentions_scribe`, `paradigm_bridge`, …) across multiple
levels of the Manuscript/CodicologicalUnit/PaleographicalUnit hierarchy and
across the Colophon/Production/ParadigmBridge classes — by design, this is
the v1.4 nested-CU model. Each property's `rdfs:domain`/`rdfs:range` axiom
only names the *primary* class it was first declared for, never an
exhaustive union of every class that legitimately uses it. `pyshacl`'s RDFS
inference synthesizes new `rdf:type` triples from those axioms, so a
`CodicologicalUnit` or `Production` node that merely *uses* a property whose
domain says `Manifestation`/`Colophon`/`ParadigmBridge` gets silently
cross-typed as that class too — and is then validated against a shape that
was never meant to apply to it. On the real `cbf6a5a6` local run TTL this
produced 1319 violations (`Paradigm bridge must link to a TextTradition`
×282, `NLI identifier must be a single string value` ×280, `Paradigm bridge
must link to a Work` ×271, `Colophon must have text content` ×37, plus
`Expression`/`Manuscript` completeness noise) — none of which reflected a
real data problem; the same TTL is **0 violations** at `inference="none"`.

**Fix, two parts (defense in depth — either alone would have sufficed for
this corpus, both together close the gap for good):**

1. **`_run_shacl_sync` now calls `inference="none"`.** Every ontology class
   the graph builder emits already gets its real `rdf:type` asserted
   explicitly at construction time, so shapes can target it precisely
   without needing RDFS subclass/domain inference at all. This matches every
   other `ShaclValidator.validate()` call site in the desktop pipeline
   (`converter/main.py`, `converter_api.py`, `gui/main_window.py`, …), which
   already default to `inference="none"` — the web app's build-validation
   path was the only outlier.
2. **`graph_builder.py` (`GraphBuilder`) cleanup**, so future inference-mode
   experiments (or a curator manually running `pyshacl` with `rdfs` for
   deeper reasoning) don't reintroduce the same leak:
   - Removed `hm:mentions_scribe` triples on the Production/Manuscript
     nodes — the property is `rdfs:domain hm:Colophon`; only the Colophon
     node should ever carry it. Scribe linkage from Production/Manuscript
     already exists via `hm:has_scribe` + CIDOC `P14_carried_out_by`.
   - Removed the reversed `hm:paradigm_bridge` triples on the Work/
     TextTradition nodes (`rdfs:domain hm:ParadigmBridge` — the bridge
     points *at* them via `has_linked_work`/`has_linked_tradition`, they
     never point at the bridge).
   - `_add_cataloging_view`/`add_philological_view` now co-type their view
     node as `hm:ManuscriptView` explicitly (previously relied on
     `CatalogingView`/`PhilologicalView rdfs:subClassOf ManuscriptView`
     inference to make `ManuscriptViewShape` apply) and co-type
     `hm:BibliographicParadigm`/`hm:PhilologicalParadigm` as `hm:ViewType`
     directly in the data graph (previously that typing lived only in the
     ontology file's `owl:NamedIndividual` declaration, invisible to any
     SHACL run that doesn't merge the full ontology in as `ont_graph`).
   - Separately, the **desktop** pipeline's `_add_cataloging_view` was
     missing the `hm:view_type hm:BibliographicParadigm` triple entirely
     (a real, inference-independent bug — `PhilologicalView` got its
     `PhilologicalParadigm` typing, `CatalogingView` never got the
     bibliographic counterpart). Fixed there; the web copy already had this
     line, so this specific line does not appear in the web diff.

**Verification:** `tests/unit/test_ontology_golden_shacl.py` (desktop) was
itself part of the problem — its hand-rolled `pyshacl.validate()` call used
`inference="rdfs"` with the ontology only merged into `shacl_graph` (never
passed as `ont_graph`), so subclass/individual-type inference never actually
ran against the golden fixture in the first place; the shapes it should have
been failing were silently never triggered. It now calls the real
`ShaclValidator.validate(..., inference="none", ontology_path=...)` so the
test exercises the same code path production uses. Confirmed:
`inference="none"` → golden corpus conforms; `inference="rdfs"` on the same
corpus still surfaces the domain-leak noise (`canonical_hierarchy`,
`tradition_name`, and dozens of "Manuscript should embody at least one
Expression" warnings on non-Manuscript nodes) — proof the inference mode,
not the corpus, was always the bug.

**Residual gap — desktop↔web mirror drift (Rule R5 exception, temporary):**
the desktop pipeline repo's `converter/rdf/graph_builder.py`,
`converter/transformer/{field_handlers,uri_generator,gematria,
hebrew_date_parse,hebrew_gregorian_calendar}.py`, and
`ontology/hebrew-manuscripts.ttl` currently carry **other, unrelated,
uncommitted WIP changes** (genre-node refactor, Hebrew/Gregorian date
parsing consolidation, subject-record helpers) that are not yet tested/
finished — `sync_converter_to_web.sh` mirrors whole directory trees, so
running it right now would have deleted three transformer modules the web
backend still imports (`marc_ingest.py`, `date_entity_normalize.py`,
`converter/transformer/date_resolver.py`) and dragged in changes that break
`tests/unit/test_safety_guards.py` in the desktop repo. This fix was ported
by hand-applying only the four `graph_builder.py` edits above to the web
copy — **run `/sync-from-desktop` (or `scripts/sync_converter_to_web.sh`)
once the desktop WIP work lands and passes its own test suite**, not before.

Tests: `pipeline/tests/unit/test_ontology_golden_shacl.py`,
`pipeline/tests/unit/test_people_network.py` (confirms removing
`mentions_scribe` from Manuscript/Production doesn't break the people-network
query — it already reaches scribes via `has_scribe`/`P14_carried_out_by`),
`backend/tests/` (34 rdf/graph_builder/people_network cases, web repo).

---

### Rule W-44 — Large-scope AI verify MUST stream verdicts live via job + TRACE (added 2026-07-08)

Incident: run `48ba6c13` — **Autofix with AI (1967 items)** showed an active
`AgentFlowDiagram` (`[STATS]` lines updating judged count) but
`VerdictsTable` stayed at **VERDICTS (0)** / "Waiting for verdicts…" for the
entire multi-hour run.

Root cause, two parts:

1. **Subprocess verdict timing.** `eval-agent` wrote `results.jsonl` only in
   `checkpoint()` at session end. `spawn_eval_agent_run` forwarded
   `[STEP]`/`[STATS]` during the loop but not `agent.verdict`; verify streams
   re-read `results.jsonl` in a `finally` block and only then emitted verdicts.
   For 1967 uncached items at 30 rpm autofix that meant ~65 minutes with a live
   UI count of zero even while Gemini was judging.
2. **HMO item modal used direct SSE.** `HmoItemVerificationModal` called
   `POST …/ai-verify/start-stream` via `useHmoItemVerifySession` instead of the
   job-backed path (`useVerifyJob` + `run_jobs.progress.session_snapshot`) that
   NER verify and `ItemUploadPanel` pre/post-upload already use. Long-lived SSE
   on Heroku is fragile; without incremental `agent.verdict` events the job
   snapshot also had nothing to hydrate mid-run.

Invariants now enforced:

- **`eval-agent` emits `[TRACE] {"type":"agent.verdict",…}` after each judged
  candidate** (`eval_agent/orchestration/session.py::_emit_verdict_trace`).
  `agent_runner._read_subprocess_stream` maps these to `AgentEvent` type
  `agent.verdict` during the subprocess loop.
- **Verify stream `finally` blocks MUST NOT re-yield verdicts already streamed**
  during the subprocess (track streamed `_local_id` / `_match_id` keys; still
  persist from `results.jsonl` for durability).
- **Large-scope curator verify modals MUST use `useVerifyJob`**, not a direct
  `start-stream` hook. `HmoItemVerificationModal` now mirrors
  `NerVerificationModal`: `RunJobs.start(…, kind="hmo_item_verify")`, poll every
  2 s, hydrate from `fetchVerifySessionWithJobFallback` +
  `progress.session_snapshot`. The `start-stream` route remains for replay/tests;
  it is not the primary UI path for 1000+ item scopes.

Tests: `backend/tests/unit/test_agent_runner_subprocess_timeout.py`
(`test_read_subprocess_stream_parses_trace_agent_verdict`),
`backend/tests/test_verify_job_hmo.py`.

---

### Rule W-45 — HMO item build hygiene + verify MARC correlation (added 2026-07-08)

Audit of run `48ba6c13` AI autofix export (966 `fail` / 1967 items):

1. **672 fails were “no MARC context”** — not bad RDF, but a broken verify
   join. `hmo_wikibase_items.control_number()` only matched `/MS_` in
   `source_uri`; HMO instance URIs embed the parent id as
   `…#Acquisition_<cn>_01` / `Expression_…_in_<cn>`. **Every** item got an
   empty `marc_rec` → Gemini conservatively failed. Fix:
   `eval-agent/eval_agent/ingest/hmo_wikibase_items.py` regex-extracts the
   8+ digit control number from `source_uri` / `local_id`.
2. **28 bogus `Q30 Acquisition` items** — `graph_builder` minted
   `CIDOC.E8_Acquisition` from generic MARC **561$a** `data.provenance`
   (ownership/censorship notes). Real acquisitions stay on
   `provenance_events` type `acquisition` (541) + `hm:ownership_history` on
   the manuscript. The standalone Acquisition node + Wikibase item is gone.
3. **~165 label-quote fails** — 505/500 titles kept ISBD ``"title :" "subtitle"``
   nesting and `""` wrappers. `clean_marc_label()` now normalizes adjacent
   quoted ISBD pairs (preserving Hebrew abbreviation gershayim like `ה"ה`);
   `label_sanitize.sanitize_monolingual_map()` applies it at Wikibase export.
4. **Descriptive 505 fragments as Works** — notes like `גם תרגום לטיני…` /
   `כולל גם נוסח ביוונית` were promoted to `contents` / `work_mentions`.
   `is_descriptive_content_title()` filters them at ingest and in
   `_add_content_work`.

Tests: `backend/tests/unit/test_rdf_helpers.py`,
`test_graph_builder_provenance.py`, `eval-agent/tests/test_hmo_wikibase_items.py`.

---

### Rule W-46 — Tier-1 judge model registry + Qubrid Kimi path (added 2026-07-08)

Curators can pick the tier-1 judge per verify run from a server registry
instead of always defaulting to `gemini-3.5-flash`:

- **Registry:** `eval-agent/config/tier1_models.yaml` — today
  `gemini-3.5-flash` (Gemini, agentic-capable) and
  `moonshotai/Kimi-K2.5` (Qubrid OpenAI-compat, linear-only).
  Backend mirrors via `app/pipeline/judge_models.py` (no Python import
  across the subprocess boundary).
- **Eval-agent:** `OpenAICompatJudge` in
  `eval_agent/client/openai_compat_client.py`; `session.py::_build_judge`
  routes by provider. Non-agentic models force `mode=linear` in
  `SessionConfig.from_args`.
- **Credentials:** Gemini — user Settings key or `GEMINI_API_KEY`;
  Qubrid — server `QUBRID_API_KEY` only (injected into subprocess env,
  never argv). `prepare_job_params` fails fast with a clear 400 when the
  chosen model's key is missing.
- **API/UI:** `GET /api/judge-models`; `Tier1ModelSelect` on every verify
  modal + upload pre/post-verify checkboxes. Job params carry `tier_model`.
- **Cache:** unchanged — `judge_model` already part of inference-cache
  query summaries (Rule W-25 / eval-agent R7).

Tests: `eval-agent/tests/test_judge_models.py`,
`test_openai_compat_judge.py`, `backend/tests/test_judge_models_router.py`,
`test_run_job_params_tier_model.py`.

---

### Rule W-47 — HMO schema AI verify must show ontology context to the judge (added 2026-07-08)

Audit of the 2026-07-08 schema verdict export (`387` entries: `232`
partial / `36` fail): **~105 partials** falsely claimed “missing
description” even though every row had a non-empty `description` in the
bootstrap JSON. Root cause: `eval-agent/eval_agent/evaluators/
hmo_wikibase_schema.py` never passed `description` (or OWL metadata)
into `build_prompt()` — the rubric told the judge to score `name_ok`
from label **and** description, but only the label was visible.

Invariants now enforced:

- **Evaluator prompt** includes `description`, `aliases`, and — for
  properties — `OWL kind` + `rdfs:range` + `parent_uri` when present.
- **Fixture enrichment** — `hmo_schema_verify.filter_schema_entries`
  merges `schema_entry_metadata_by_uri()` from
  `ontology_schema_reader.py` so skipped/cached bootstrap rows still carry
  OWL context without re-running bootstrap.
- **Cache key** — `schema_verdict_query_summary` includes `description`,
  `property_kind`, and `range_uri` so pre-fix verdicts do not warm-hit
  after deploy.
- **Rubric** (`hmo_wikibase_schema.md`) documents CIDOC object-property
  `wikibase-item` typing, folio **designation** strings vs folio
  **counts**, `holding_institution` vs `has_holding_institution`, `url`
  for `owl:sameAs`, and `quantity` (not `globe-coordinate`) for
  `geo:lat`/`geo:long` — matching the item exporter.
- **Datatype inference** — `hmo_source_uri` → `url` (`xsd:anyURI` in
  TTL + local-name override); `book_name` → `monolingualtext`; thirty
  CIDOC/LRMoo/HMO classes gained `rdfs:comment` (zero fallback-class
  descriptions remain).

Tests: `eval-agent/tests/test_hmo_wikibase_schema.py`,
`backend/tests/unit/test_ontology_schema_reader.py`,
`backend/tests/unit/test_hmo_schema_verify.py`.

**Curator ops:** re-run HMO schema AI verify with a fresh session (or
`skip_cache`) after deploy — old `ai_verdict` cache rows keyed without
`description` will miss and re-judge correctly.

---

### Rule W-48 — HMO item AI verify needs manuscript scope + substantive descriptions (added 2026-07-09)

Second audit of run `48ba6c13` export (4) after W-45/W-47 fixes — **690
fail / 857 partial** on 1911 items (down from 966 fails, still not
curator-ready):

1. **792 items still had no MARC join** — mostly `QDraft_Person_*` rows
   whose `source_uri` carries no 8+ digit control number. URI-regex alone
   (W-45) cannot reach derived persons/works linked only through the RDF
   graph. **Fix (build):** `hmo_exporter._control_numbers_for_node` BFS-walks
   **incoming** RDF edges to every manuscript URI, stamps `control_numbers`
   on `WikibaseEntityDraft` / `ResolvedWikibaseEntity`, and persists them in
   `HmoStudioItemCache`. **Fix (verify):**
   `hmo_wikibase_items.enrich_control_numbers()` propagates across
   `deferred_links`; `session.py` tries every CN in the list when loading
   MARC.
2. **1283 generic Wikibase descriptions** — `… in the Hebrew Manuscripts
   Ontology (HMO)` fallback when a node lacks `rdfs:comment`. Codicological
   units were fixed in W-45; Work/Expression/Person/Manuscript/Production/
   epistemology nodes were not. **Fix:** `graph_builder._stamp_wikibase_comment`
   (and the existing CU helper) attach English `rdfs:comment` at RDF build;
   `hmo_exporter._descriptions_for_node` prefers those over the fallback.
3. **Wrong judge framing** — the item rubric treated HMO items like raw NER
   spans (`grounded=None`, Wikidata-centric `class_qid` confusion). **Fix:**
   rewritten `hmo_wikibase_item.md` + evaluator passes `entity_type`,
   `control_numbers`, structural vs manuscript-scoped grounding, and
   `full`/`partial`/`fail` overalls.

**Curator ops after deploy:** RDF rebuild → HMO **Rebuild (skip cache)** →
re-run item AI verify with **override cache** (rubric + build fields changed).
Reupload only when live wiki labels/descriptions should change.

Tests: `backend/tests/unit/test_hmo_exporter_control_numbers.py`,
`test_graph_builder_codicological_labels.py`,
`eval-agent/tests/test_hmo_wikibase_items.py`.

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
| `frontend/src/components/` | Shared widgets (AiVerificationModal, AgentFlowDiagram, SelectAllVisible, …) |
| `frontend/src/components/authority/` | Authority review components: `AuthorityTable`, `AuthorityDetailDrawer`, `AuthorityAutoApproveRuleBuilder` |
| `frontend/src/components/wikidata/` | Studio-specific components: `ItemValidatorBadge`, `ItemApprovalBadge` |
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

# Switch authority enrichment to Postgres (production default — Rule W-28)
heroku config:set AUTHORITY_MODE=postgres

# Import Mazal + KIMA from local SQLite into Heroku Postgres (one-time, idempotent)
# Run locally pointing at Heroku DATABASE_URL — 15 s for KIMA, ~10 min for Mazal
cd backend && DATABASE_URL=... KIMA_DB_PATH=backend/data/kima/kima_index.db \
  .venv/bin/python -m scripts.import_kima_to_postgres
cd backend && DATABASE_URL=... MAZAL_DB_PATH=.../mazal_index.db \
  .venv/bin/python -m scripts.import_mazal_to_postgres
```

---

### Rule W-34 — Full HMO RDF projection (added 2026-06-17)

The web RDF build path must wire approved authority + NER enrichment into
``GraphBuilder`` before ``ExtractedData`` is materialised:

- ``backend/app/pipeline/rdf_enrichment.py`` merges approved Stage-2/3 rows.
- ``backend/app/pipeline/rdf_build.py`` passes ``RdfBuildOptions`` toggles
  (epistemology, cataloging view, philological overlay — all default on).
- Each build writes ``rdf_projection_coverage.json`` beside the TTL; the
  router exposes ``GET /runs/{id}/rdf/coverage``.
- Vendored converter code lives under ``backend/converter/``; sync from the
  desktop pipeline with ``pipeline/scripts/sync_converter_to_web.sh``.

Canonical ontology: ``pipeline/ontology/hebrew-manuscripts.ttl`` (copied to
``backend/ontology/`` at sync time).

### Rule W-35 — Reusable glass components, never raw `.glass` classes (added 2026-06-17)

Every frosted / liquid-glass surface in the React UI must use the shared
components under ``frontend/src/components/glass/``:

| Need | Component | Variants |
|---|---|---|
| Panels, cards, modals, drawers | ``<Glass>`` | ``panel`` (default), ``drawer``, ``modal``, ``compact`` |
| Chips, badges, stat pills | ``<GlassPill>`` | (always ``variant="pill"``) |
| Full-page ambient background | ``<LiquidGlassCanvas>`` | mounted once in ``main.tsx`` |

**Do not**:

- Add ``className="glass"`` or ``className="glass-pill"`` on new UI — those
  CSS classes are deprecated legacy aliases.
- Create per-feature glass wrappers or DOM enhancers that upgrade elements at
  runtime.
- Duplicate ``LiquidGlassSurface`` / displacement-map logic outside the glass
  module.

**Do**:

```tsx
import {Glass, GlassPill} from "@/components/glass";

<Glass as="section" className="p-6 space-y-4">…</Glass>
<Glass variant="drawer" className="fixed right-0 …">…</Glass>
<GlassPill className="px-3 py-1 text-xs">…</GlassPill>
<GlassPill as="label" className="…">…</GlassPill>
```

Form fields keep ``input-glass`` (plain CSS, not liquid refraction).
Layout/tint tokens live in ``.glass-shell*``; refraction is applied inside
``LiquidGlassSurface``.

---

### Rule W-36 — Zustand selectors and parent callback effects (added 2026-06-20)

Three production blank-UI incidents (React error #185) traced to the same
failure mode. These invariants prevent recurrence:

**Zustand selectors**
- Select **primitives** from the store: ``useRunJobs((s) => s.jobs)``,
  ``useAuth((s) => s.user?.id)``. Never ``useRunJobs((s) => s.jobForRun(...))``,
  ``useRunJobs((s) => s.activeJobs())``, or ``useRunJobs((s) => Object.values(s.jobs))``
  — each call allocates a new reference and can spin an infinite re-render loop.
- Derive active jobs with ``selectActiveJob(jobsRecord, runId, kind)`` inside
  ``useMemo``, keyed by a ``jobFingerprint`` when syncing progress.

**Table → parent reporting**
- Never ``useEffect`` + ``onFixableChange(newArray)`` without a content
  fingerprint guard. Use ``useReportDerivedIds`` /
  ``useReportFilteredEntities`` from ``frontend/src/hooks/useReportDerivedIds.ts``.
- Parent callbacks passed into tables must be ``useCallback``-stable.

**Long-running job UI**
- Use ``useRunJobAttachment`` or ``useVerifyJob`` — never hand-rolled
  ``jobForRun`` selectors plus overlapping poll effects.
- Hook option callbacks (``onFailed``, ``onComplete``, ``sync``) must be refs
  or ``useCallback``; never inline arrows in objects consumed by effect deps.

Shared helpers: ``frontend/src/utils/renderStable.ts``.

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

Also bump the `W-1…W-N` pointer in [AGENTS.md](AGENTS.md) and
[README.md](README.md) when you add a new `Rule W-N`.

A code change that alters the architecture is not complete until the
plan doc, block docs (see [AGENTS.md](AGENTS.md)), and this file are updated.

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

### Rule W-50 — HMO verify label hygiene + multi-CN MARC merge (added 2026-07-09)

Export (4) on run `48ba6c13` showed ~1111 `name_ok=partial` rows driven by
label design and judge calibration, not bad RDF:

1. **505 / Expression labels** — `parse_contents_entry()` in
   `converter/rdf/rdf_helpers.py` splits `N) folio : title` 505 rows at
   ingest; `graph_builder._add_content_work` / `_add_expression` emit short
   Hebrew titles only (never `(in MS {cn})` in labels; scope stays in
   `rdfs:comment`). `clean_marc_label` strips `(in MS …)` suffixes and
   optional enumeration prefixes; `hmo_exporter._truncate` clips at word
   boundaries.
2. **Multi-CN MARC for verify** — shared persons across manuscripts MUST
   merge MARC from every `control_numbers` entry:
   `marc_extract.merge_records()` / `project_many()`; `session.py` passes
   the union to `hmo_wikibase_item`; `primary_control_number()` picks the
   CN matching `source_uri` / `local_id`.
3. **Vocabulary-node descriptions** — genre/subject/material/script nodes get
   `rdfs:comment` at RDF build (not generic exporter fallback).
4. **SHACL in verify** — fixtures from `fetch_merged_hmo_items` carry real
   `shacl_issues`; evaluator `blocking_shacl` short-circuits; rubric forbids
   `role_ok=no` when `shacl_issues` is empty.

Upload SHACL gate (`hmo_item_shacl_gate.py`) unchanged — fail-closed.

Tests: `test_rdf_helpers.py`, `test_graph_builder_codicological_labels.py`,
`eval-agent/tests/test_marc_extract_merge.py`, `test_hmo_wikibase_items.py`.

---

### Rule W-51 — AI verdict caches are content-addressed everywhere (added 2026-07-09)

Every ``kind=ai_verdict`` surface (NER extraction, authority matches,
Wikidata Studio items, HMO Wikibase items, HMO schema bootstrap) MUST
use the same two-tier contract:

1. **Inference cache lookup** — ``canonical_hash(query_summary)`` where
   ``query_summary`` includes every curator-visible input (entity text,
   match payload, item labels/claims/SHACL, merged MARC slice, judge
   model, evaluator id, and a schema salt like ``w50_v1``).
2. **Stored row ``cache_key``** — the same content fingerprint, never
   the eval-agent's prompt-hash. ``sanitise_stale_*`` on read paths
   drops verdicts whose ``cache_key`` no longer matches the current
   input so a rebuild or MARC edit invalidates pills without
   ``override_cache``.

``override_cache`` / ``--no-cache`` is only for forcing a re-judge when
the input is unchanged (rubric tweak, model swap). Changing the data is
always sufficient invalidation — mirrors Rule W-26 fingerprint-keyed
build caches (Rule R6: never delete manually).

Modules: ``ner_verdict_cache``, ``authority_verdict_cache``,
``wikidata_verdict_cache``, ``hmo_item_verdict_cache``,
``hmo_schema_verdict_cache``, shared ``ai_verdict_cache_common``.

Tests: ``test_*_verdict_cache.py`` per channel.

---

### Rule W-52 — HMO item build must emit substantive metadata for every exportable entity (added 2026-07-10)

Fixup-loop audit of run ``48ba6c13`` (Qubrid/Kimi judge on the
partial/fail scope) traced most residual ``name_ok=partial`` verdicts to
missing or system-only metadata on non-Work/Person entities — not bad
data. Every entity the HMO exporter ships as a Wikibase item MUST carry a
substantive label + description at RDF build time:

- **``graph_builder`` labels/descriptions.**
  ``_add_production_event`` stamps an ``rdfs:label``
  ``Production of MS {cn} ({place}, {date}, scribe {names})`` and a matching
  ``rdfs:comment`` (never the bare ``Production {cn}`` URI local name).
  Time-Span labels are ``Production period {span}`` (``en``), never a bare
  year. ``add_text_tradition`` labels in the title's own script
  (``label_language_for_text`` — a Latin tradition title is never tagged
  ``he``) and its comment names the attesting manuscript; the philological
  loop skips traditions/witnesses whose title fails ``is_usable_work_title``.
  Manuscript labels sanitize the 245 and fall back to ``MS {shelfmark}``
  when the title is <4 chars, plus an ``en`` ``Jerusalem, NLI, {shelfmark}``
  label. Genre/subject/place labels route through ``label_language_for_text``
  so a Latin term is never tagged ``he``. ``_add_content_work`` defensively
  re-runs ``parse_contents_entry`` so a raw ``N) folio : title`` 505 row
  never becomes a label.

- **Person/org hygiene.** ``clean_person_display_name`` strips a dangling
  ``בן``/``אבן`` particle. ``sanitize_work_title`` is gershayim-aware
  (``שד"ל``/``ה"ה`` survive; unbalanced ISBD quotes and dangling ``)`` are
  stripped). ``E74_Group`` org labels are English-only. The exporter dedupes
  repeated sentences when merging one ``rdfs:comment`` per linked manuscript.

- **Export quality gate is a hard block** (``hmo_export_quality`` +
  ``assert_export_quality``). New codes: ``production_missing_label``,
  ``timespan_bare_label``, ``latin_label_in_he``, ``unbalanced_label_quotes``,
  ``witness_unusable_title`` — added to the existing blank-node /
  Hebrew-in-en / generic-description / missing-scope checks. A build with any
  issue raises before caching, so a regression can never reach the wiki.

- **Rubric alignment (eval-agent).** ``hmo_wikibase_item.md`` rules 3d/3e:
  ``E12_Production`` / ``E52_Time-Span`` / ``F27_Work_Creation`` /
  ``TransmissionWitness`` / ``TextTradition`` carry intentional English system
  labels — ``name_ok=yes`` when the label carries the MS/period AND the
  description is substantive; ``E74_Group`` orgs are judged as organizations,
  not Hebrew persons. The evaluator's ``format_grounding`` emits a
  ``SYSTEM-LABELED EVENT`` state for those types.

- **Cache salt (Rule W-51).** ``HMO_ITEM_VERDICT_SCHEMA`` bumped to
  ``w52_v1`` so old verdicts miss automatically after the rubric/RDF change —
  no ``override_cache`` needed; a rebuild + re-verify is sufficient.

**Curator ops after deploy:** RDF rebuild → HMO **Rebuild (skip cache)** →
re-verify (no override cache). Reupload only when live wiki labels/
descriptions should change.

Tests: ``test_graph_builder_codicological_labels.py``,
``test_graph_builder_philological_labels.py``, ``test_rdf_helpers.py``,
``test_hmo_export_quality.py``, ``test_hmo_exporter_resolution.py``,
``eval-agent/tests/test_hmo_wikibase_items.py``.

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
