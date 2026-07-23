# HMO Wikibase Studio — Full Implementation Plan

## Status (2026-07-03, end of session)

- **Phases 1-6: done and unit/integration-tested** (SQLite-backed test
  suite; no live-Wikibase-cloud call has been made yet — see below).
  `WikibaseCloudWriter` gained `create_item`/`create_property`/
  `add_claim`/`get_entity`, built on `wikibaseintegrator` (the same
  library `converter/wikidata/uploader.py` already uses) rather than
  hand-rolled `wbeditentity` calls — a deviation from the plan text
  below, made after confirming the dependency was already in
  `pyproject.toml` and in active use. The ontology parser
  (`converter/wikibase/ontology_schema_reader.py`) reads the real
  `hebrew-manuscripts.ttl` (103 classes / 277 properties, tolerant test
  thresholds). Phases 4-5 (item export + create-only two-pass upload)
  and Phase 6 (Wikidata P2888/P973 cross-linking to real HMO items) are
  fully implemented per the plan below, with one correctness fix beyond
  the original design: `compute_build_fingerprint` now also hashes
  `hmo_instance_qids` so a manuscript getting uploaded to HMO Wikibase
  invalidates the Wikidata Studio build cache (otherwise P2888/P973
  would keep pointing at the stale slug URL).
- **Phase 7: implemented and browser-tested.** The three panels
  (`frontend/src/components/hmo/{SchemaBootstrapPanel,
  ItemBuildPanel,ItemUploadPanel}.tsx`) pass `tsc --noEmit`, and
  `frontend/e2e/hmo-wikibase-studio.spec.ts` covers schema counts, dry-run
  defaults, and upload gating. The focused Playwright spec passes. Frontend
  unit test infrastructure (`yarn test:unit`) remains separately broken
  repo-wide because of a missing `@testing-library/dom` peer dependency.

  **Professor-facing review UI: implemented and browser-tested.** `HmoStudio`
  now leads with a four-step Prepare → Review → Preview → Publish workflow,
  uses truthful canonical confirmation wording, and places schema/server
  maintenance behind advanced disclosures. The item table defaults to
  title/type/review status/data quality/publication status/AI review, exposes
  visible search and filter chips, supports Pending/Approved/Rejected decisions
  with save feedback, and keeps technical identifiers in an opt-in disclosure.
  Single-entry publication/update actions require the accessible
  `HmoPublishConfirmationDialog`; bulk publication remains preview-first.
  `yarn tsc --noEmit`, `git diff --check`, and the focused 3-test HMO Playwright
  suite pass after the change.
**Phase 8: read-only sweep verified; live pass remains explicitly gated.**
  `backend/scripts/run_hmo_phase8.py` runs the focused backend/frontend sweep
  and, with `--live`, enforces the ordered schema → item build → dry-run upload
  → live upload → repeat/idempotency workflow. Live writes require both
  `--confirm-live-writes` and `HMO_PHASE8_LIVE_WRITES=1`; the command records
  mapping counts and representative QIDs/PIDs in its JSON report. The real
  `mhm-hmo.wikibase.cloud` write pass is not run automatically because it
  mutates a shared external system. Credentials remain local and gitignored;
  **never commit them**. The read-only coordinator sweep is green: 208
  backend HMO tests and 3 Phase 7 Playwright tests pass.
- **New, not in the original 8-phase plan:** an `eval-agent` extension —
  a `hmo_wikibase_schema` evaluator (rubric, ingest reader, evaluator
  class) that judges schema-bootstrap output against the HMO ontology and
  Wikidata's `WikiProject_Manuscripts/Data_Model` conventions. Usable
  today via `eval-agent run --pipeline-output backend/state/hmo_wikibase_schema
  --evaluators hmo_wikibase_schema` (the router writes that snapshot on
  every bootstrap call). No SSE endpoint or frontend verification modal
  was built yet — that depends on Phase 4/5 producing real per-manuscript
  items worth judging, not just schema entries.

## Context

HMO Wikibase Studio is meant to be the **Wikidata-Studio equivalent for our
own ontology**: it should write the full Hebrew Manuscripts Ontology (HMO)
graph — every class, property, and instance — as native entities on our
self-hosted `https://mhm-hmo.wikibase.cloud/` instance, and then have
Wikidata items cross-link back to those real HMO Wikibase entities wherever
a concept has no native Wikidata equivalent.

Research established that this does **not exist yet** — it needs to be
built, not just fixed:
- `WikibaseCloudWriter` (`backend/converter/wikibase/cloud_client.py`) can
  only do generic MediaWiki page edits (used today for IIIF manifest JSON
  blobs). It has no `wbeditentity`/`wbcreateclaim` methods, so it cannot
  create a Wikibase Item or Property at all.
- `schema_bootstrap.py` doesn't parse `backend/ontology/hebrew-manuscripts.ttl`
  — `build_default_hmo_schema_bootstrap()` returns a hand-written list of
  only 10 classes / 14 properties, and performs no network calls
  ("offline schema bootstrap only" per its own docstring).
- `hmo_exporter.py` does walk the full RDF graph into item/statement drafts
  (broad, reusable foundation) but never resolves to real Wikibase PIDs/QIDs
  and never writes anything.
- `property_mapping.py` (used by Wikidata Studio) is 100% real Wikidata
  P-IDs — the opposite job. Its only HMO bridge, `hmo_wikibase_page_url()`,
  builds a static `/wiki/MS_<cn>` slug URL, not a link to a real created
  entity.
- Nothing here is wired into `hmo_studio.py`'s router/pipeline, and there
  are zero tests for any of it.

Given the scope, this plan sequences work into 8 independently
mergeable/testable phases, mirroring the mature `wikidata_studio.py` /
`wikidata_upload.py` build→validate→upload shape wherever it fits, but with
its own identity/idempotency model (does this ontology URI already have a
live Wikibase ID?) tracked in a new Postgres table — consistent with Rule
W-33 (Heroku `/tmp` is ephemeral; durable state must live in Postgres).

## Key judgment calls (apply throughout)

- **One mapping table**, not two. `wikibase_entity_mappings` holds schema
  rows (`run_id IS NULL`) and instance rows (`run_id` set): same lookup
  shape (`ontology_uri -> wikibase_id`), and instance resolution needs to
  query schema rows in the same pass — splitting forces a join everywhere
  both are consumed together.
- **Mapping table = idempotency ledger, not the build cache.** A separate
  `hmo_studio_item_cache` table (modeled on `WikidataStudioCache`) holds
  fingerprinted per-run build output, since mapping rows mutate
  independently of any one run (schema created while building run A must be
  visible to run B) — conflating it with the fingerprint cache would merge
  "idempotency" and "cache staleness," which are different concerns.
- **URL shape**: schema is global → new top-level router
  `/api/hmo-wikibase-schema/*`. Per-run item build/upload stay under the
  existing `/api/runs/{run_id}/hmo-studio/*`.
- **v1 = create-only.** Existing mapping row → skip, no diff/edit-in-place
  (see Residual Risks).
- **Rule W-25**: mapping-table reads are cheap Postgres, not external calls
  — exempt from `cache_lookup_or_call`. The mapping table is the write-dedup
  mechanism for `wbeditentity` calls, not that cache layer.

---

## Phase 1 — Wikibase entity-write API

**Modify** `backend/converter/wikibase/cloud_client.py`. Add to
`WikibaseCloudWriter` (reusing its existing CSRF-token + retry logic,
the same pattern `edit_page` already uses):
- `create_item(labels, descriptions, claims=None, aliases=None) -> EntityEditOutcome`
- `create_property(labels, descriptions, datatype, claims=None, aliases=None) -> EntityEditOutcome`
- `add_claim(entity_id, property_id, snak) -> EntityEditOutcome` (`wbcreateclaim`, used in phase 5's pass 2)
- `get_entity(entity_id) -> Mapping | None` (`wbgetentities`, verification only)

All go through `action=wbeditentity` (`new="item"|"property"`,
`data=json.dumps(...)`, `token`, `bot="1"`, `assert="bot"`), with the same
stale-token retry-once pattern `edit_page` uses today. New frozen dataclass
`EntityEditOutcome(entity_id, status, message, page_url)`, sibling to the
existing `EditOutcome`. No idempotency check inside the writer — that's the
mapping table's job (phase 3); keep the transport layer dumb and testable.

**Tests:** `backend/tests/converter/wikibase/test_cloud_client_entities.py`
— mock `requests.Session.post`; assert `wbeditentity` payload shape for
item vs property, CSRF-refresh-on-`badtoken` retry, `assert=bot` present.

**Verification:** unit tests only — no live calls until phase 3.

---

## Phase 2 — Ontology parser (pure, offline)

**New** `backend/converter/wikibase/ontology_schema_reader.py`:
- `read_hmo_schema(ttl_path) -> OntologySchema`: rdflib parse, walk
  `owl:Class`, `owl:ObjectProperty`, `owl:DatatypeProperty` (flag
  `owl:AnnotationProperty` as skip-with-reason, don't silently drop).
- Per subject: label from `rdfs:label` (en, else any, else local-name),
  description from `rdfs:comment`/`skos:definition`, fallback
  `f"HMO {kind}: {local_name}"` (Wikibase rejects blank descriptions).
  `rdfs:subClassOf`/`subPropertyOf` for hierarchy parent. Datatype
  inference for properties: object property w/ class range →
  `wikibase-item`; `xsd:date(Time)` → `time`; `xsd:anyURI` → `url`;
  default `string` (log a warning when unresolved).
- Dataclasses `OntologyClassEntry`, `OntologyPropertyEntry`,
  `OntologySchema`. These feed `schema_bootstrap.py`; `models.py` and
  existing consumers of `WikibaseSchemaClassDraft` need no changes.

**Modify** `backend/converter/wikibase/schema_bootstrap.py`: replace
`build_default_hmo_schema_bootstrap()`'s body to call `read_hmo_schema()`
and map entries into the existing draft dataclasses (same function
signature/return type). Extract the local-ID slug logic already duplicated
in `hmo_exporter.py` into a shared `backend/converter/wikibase/_ids.py`
(`safe_local_id()`), imported by both modules. Add
`default_hmo_ontology_path()` → `backend/ontology/hebrew-manuscripts.ttl`.

**Tests:** `backend/tests/converter/wikibase/test_ontology_schema_reader.py`
— parse the real ontology, assert counts against tolerant thresholds so
ontology growth doesn't rot the test; every entry has non-empty
label+description, no `None` datatypes. `test_schema_bootstrap.py` —
rewritten bootstrap count matches reader minus skipped kinds.

**Verification:** run the bootstrap builder locally, eyeball counts against
the ttl. Still zero network calls.

---

## Phase 3 — Postgres mapping table + live schema bootstrap

**Migration** `backend/app/migrations/versions/0025_wikibase_entity_mappings.py`
(next number after `0024_run_jobs.py`):
```
wikibase_entity_mappings(
  id UUID PK, ontology_uri TEXT NOT NULL, entity_kind TEXT NOT NULL, -- class|property|instance
  wikibase_id TEXT NOT NULL, run_id UUID NULL, local_key TEXT NULL,
  datatype TEXT NULL, label TEXT NOT NULL, created_at TIMESTAMPTZ default now())
UNIQUE(ontology_uri) WHERE run_id IS NULL
UNIQUE(ontology_uri, run_id) WHERE run_id IS NOT NULL
INDEX(entity_kind)
```
Mirrors `0024_run_jobs.py`'s partial-index style.

**New model** `backend/app/models/wikibase_entity_mapping.py` →
`WikibaseEntityMapping(Base)`, structured like `wikidata_studio_cache.py`.

**New pipeline** `backend/app/pipeline/hmo_schema_bootstrap.py`:
- `bootstrap_schema(session, *, writer, dry_run) -> SchemaBootstrapResult`:
  build drafts (phase 2) → load existing `run_id IS NULL` mappings as the
  skip-set → for each unmapped class/property: dry-run collects
  `would_create`; live calls `writer.create_property`/`create_item`
  (`asyncio.to_thread`, matching `hmo_studio.py` convention) and inserts the
  mapping row **immediately** per success (crash-safe resume, not batched).
  Ordering: create ALL properties before ALL classes (properties are
  referenced by later item claims); v1 classes get NO `instance of` claim
  (Wikibase Cloud has no default meta-class item — future task can mint one
  and backfill). Returns created/skipped counts + `would_create` list.
- `schema_status(session) -> SchemaStatusResult`: ontology counts (phase 2)
  vs. mapped rows, with a sample of missing URIs — pure Postgres, safe to poll.

**New router** `backend/app/routers/hmo_wikibase_schema.py`
(`/api/hmo-wikibase-schema`):
- `GET /status` (`require_viewer`)
- `POST /bootstrap?dry_run=` (`require_editor`), credentials via the same
  `ApiKey`/`secrets_mod` unwrap pattern `hmo_studio.py` already uses; one
  `ProjectEvent` per *call* (not per entity), new
  `entity_type="wikibase_schema"` constant in `app/models/event.py`,
  `op=OP_CREATE`, payload = summary counts.
Register in `backend/app/main.py`.

**Tests:** `test_hmo_schema_bootstrap.py` (mock writer; first run creates N
rows, second run creates 0, mid-loop failure leaves prior rows committed).
`test_hmo_wikibase_schema_router.py` (dry-run never invokes the writer mock).

**Manual live verification (sequence, live bot credentials confirmed available):**
1. `GET /status` → `mapped=0`.
2. `POST /bootstrap?dry_run=true` → `would_create` matches phase-2 counts;
   confirm nothing appears live (spot-check via `wbsearchentities`).
3. `POST /bootstrap?dry_run=false` — watch for rate-limit backoff over
   the full sequential write batch.
4. `GET /status` → fully mapped.
5. Re-run live bootstrap → 0 new creates (idempotency proof).
6. Browse a handful of created entities on the live site.

This is the "phases 1+2+3 = live schema bootstrap end-to-end" milestone —
independently mergeable/demoable before any instance work starts.

---

## Phase 4 — Full item export wired to live schema

**Modify** `backend/converter/wikibase/hmo_exporter.py`: add
`resolve_against_mappings(drafts, schema_mappings) -> list[ResolvedWikibaseEntity]`
where `schema_mappings = {ontology_uri: PID_or_QID}` from phase 3
(`entity_kind IN ('class','property')`, `run_id IS NULL`). New
`ResolvedWikibaseEntity` in a new `backend/converter/wikibase/resolved_models.py`
carrying real Wikibase JSON claims. Object statements pointing at another
instance draft *in the same run* resolve to a placeholder token
(`"$local:QDraft_x"`) and are recorded separately as
`deferred_item_links: list[(local_id, property_pid, target_local_id)]`,
resolved post-creation in phase 5's pass 2. Any predicate/class URI with NO
schema mapping is a hard `unmapped_uri` error (guards ontology drift) —
fail closed, never silently drop.

**New pipeline** `backend/app/pipeline/hmo_item_build.py` (mirrors
`wikidata_studio.py`): `compute_hmo_build_fingerprint(ttl_hash,
schema_mapping_version)` — schema version = `COUNT(*)`+`MAX(created_at)`
over schema-mapping rows, so a bootstrap invalidates cached builds.
`build_items_for_run(session, run_id, ttl_path)`: check
`hmo_studio_item_cache` by `(run_id, fingerprint)`; on miss run exporter +
resolve in a threadpool, upsert cache, return items + unresolved summary.

**Migration** `0026_hmo_studio_item_cache.py`: table cloned from
`wikidata_studio_cache.py` minus Wikidata-specific match columns, plus
`deferred_link_count`. **New model**
`backend/app/models/hmo_studio_item_cache.py`.

**Tests:** `test_hmo_exporter_resolution.py` (synthetic TTL fixture, assert
real-PID resolution, deferred self-links captured, `unmapped_uri` raised for
absent predicates). `test_hmo_item_build.py` (fingerprint bumps on mapping
version change even if TTL unchanged).

**Verification:** run against a real run's RDF, diff resolved claim PIDs
against phase-3's live mapping table — no network writes yet.

---

## Phase 5 — Upload path (create-only, two-pass)

No new writer methods beyond phase 1. **New**
`backend/app/pipeline/hmo_item_upload.py`:
`upload_items_for_run(session, run_id, *, writer, dry_run=True)`:
1. Load resolved items from the item cache; error if no build exists yet or
   it's stale.
2. Query `entity_kind='instance' AND run_id=:run_id` mappings for
   already-uploaded instances (create-only idempotency, no diff/update).
3. **Pass 1**: create unmapped items with non-deferred claims only; insert a
   mapping row immediately per success.
4. **Pass 2**: for each `deferred_item_links` entry where both ends are now
   mapped (this run or a prior one), `writer.add_claim(...)`; entries whose
   target never got created are reported as `unresolved_links`, not dropped.
5. `dry_run=True` default, skips all writer calls, returns the same result
   shape via a simulated pass (matches `upload_manifests_for_run`'s convention).

**Modify** `backend/app/routers/hmo_studio.py`, add:
- `POST /runs/{run_id}/hmo-studio/build-items`
- `POST /runs/{run_id}/hmo-studio/upload-items?dry_run=` (`require_editor`,
  one `ProjectEvent` per call reusing the existing audit block,
  `entity_type=ENTITY_TYPE_WIKIBASE_ITEM`, `op=OP_CREATE`)
- `GET /runs/{run_id}/hmo-studio/item-status`

**Tests:** `test_hmo_item_upload.py` (mock writer; pass-1/pass-2 ordering,
idempotent re-run = 0 new items, `unresolved_links` surfaced). Router httpx
tests mirroring `test_hmo_studio_works.py`'s auth/dry-run-default checks.

**Manual live verification:** dry-run against a real built run, inspect
claim JSON for a manuscript and a person item, confirm PIDs match phase-3's
live properties; then live-upload one small run, browse created items on
`mhm-hmo.wikibase.cloud`, confirm a pass-2 inter-item claim resolves to a
real item link, not a dangling placeholder.

---

## Phase 6 — Wikidata cross-linking to real HMO entities

**Modify** `backend/converter/wikidata/property_mapping.py`:
`hmo_wikibase_page_url()` stays as the manuscript-slug fallback. Add
`hmo_wikibase_entity_url(ontology_uri, mapping) -> str | None` returning
`.../wiki/Item:Q<n>` or `.../wiki/Property:P<n>` for a mapped
class/property, `None` if unmapped (caller must not emit a broken link).

`property_mapping.py`/`item_builder.py` stay DB-agnostic:
`wikidata_studio.py` (the one call site with `AsyncSession` access) loads
the schema mapping dict once per build and passes it down as a parameter
defaulting to `{}`.

**v1 scope (explicit):** ship only the manuscript-item-level upgrade —
once phase 5 creates a real per-manuscript Wikibase item, `item_builder.py`
(current `hmo_wikibase_page_url` call site, ~line 1013) emits the real
`/wiki/Item:Q<n>` URL for P2888/P973 when an instance mapping row exists,
falling back to the static slug otherwise (pre-upload runs keep working).
**Deferred:** a per-statement cross-link for individual HMO-only
properties/classes used by a record — needs a product decision on which
Wikidata slot should carry it, not an engineering default, so it's
explicitly out of v1.

**Tests:** `test_property_mapping_hmo_links.py` for
`hmo_wikibase_entity_url`; update `item_builder`'s P2888 tests to cover both
slug-fallback and real-QID branches.

**Verification:** build a Wikidata Studio item for a manuscript already
uploaded in phase 5; confirm P2888 = real `/wiki/Item:Q<n>`, not the slug.

---

## Phase 7 — Frontend

**New** `frontend/src/components/hmo/` (mirror `.../wikidata/`):
`SchemaBootstrapPanel.tsx` (status pill N/M classes+properties, dry-run/live
bootstrap buttons, results table — `<Glass>`/`<GlassPill>` only per Rule
W-35), `ItemBuildPanel.tsx`, `ItemUploadPanel.tsx` (dry-run default true,
outcome table: created/skipped/unresolved-link).

**API client:** new `frontend/src/api/hmoWikibaseSchema.ts` for the
schema-global endpoints (different router prefix); `postBuildItems`,
`postUploadItems`, `getItemStatus` added to the existing `hmoStudio.ts`.

**Modify** `frontend/src/routes/HmoStudio.tsx` — additive, not a rewrite:
mount `SchemaBootstrapPanel` above the coverage table, the two item panels
below the manifest section. Rule W-36: new table/list components select
primitives only, stable `useCallback` handlers, no raw Zustand
selector allocations.

**Tests:** `frontend/e2e/hmo-wikibase-studio.spec.ts` mirroring
`wikidata-studio.spec.ts` — mock endpoints, assert dry-run default,
schema-count rendering, upload disabled until a build exists.

**Verification:** run the app locally against the real backend, exercise
dry-run end-to-end (status → bootstrap dry-run → build → upload dry-run),
check each panel state visually.

---

## Phase 8 — Full test sweep + live-credential integration pass

The repeatable coordinator is:

```bash
cd backend
.venv/bin/python -m scripts.run_hmo_phase8 --report ../state/hmo-phase8.json
```

This runs the HMO backend tests and the Phase 7 browser spec. For an
explicitly authorized small run, add both live-write confirmations:

```bash
HMO_PHASE8_LIVE_WRITES=1 .venv/bin/python -m scripts.run_hmo_phase8 \
  --run-id <run-uuid> --live --confirm-live-writes \
  --report ../state/hmo-phase8-live.json
```

The live report is the record of schema/item mapping counts, sample QIDs/PIDs,
upload outcomes, and the second-pass zero-write idempotency check. It should
be attached to the PR or release record; do not paste credentials into it.

- `pytest backend/tests/converter/wikibase backend/tests/app/pipeline
  backend/tests/app/routers -k hmo` + the new e2e spec.
- One coordinated live run in strict order: schema status → bootstrap
  dry-run → bootstrap live → status (fully mapped) → pick one small run →
  build items → upload dry-run → upload live → item-status → spot-check
  several entities in browser → re-run bootstrap and upload once more each
  to prove idempotency (0 new writes).
- Record live mapping-table counts and sample QIDs/PIDs in the PR description.

---

## Phase 9 — Hardening and cross-run safety

Phase 9 closes the five deferred operational risks from Phase 8:

- schema mirror reports label/datatype drift instead of silently presenting a
  mapping as current; datatype changes remain fail-closed and require a new
  property plus a migration plan because Wikibase cannot mutate a property's
  datatype in place;
- `WikibaseCloudWriter` accepts `WIKIBASE_CLOUD_MIN_WRITE_INTERVAL_SECONDS`
  and throttles entity/property/claim writes without delaying reads;
- typed RDF blank nodes receive deterministic graph-derived draft IDs;
- deferred object-property targets retain their source URI and pass two
  resolves against instance mappings from all completed runs;
- manuscript Wikidata cross-links continue to use the existing P2888/P973
  contract, including real HMO item URLs after upload. Per-statement links for
  HMO-only properties remain intentionally excluded until a Wikidata property
  slot is approved; no speculative claims are emitted.

Focused regression coverage is in the HMO exporter, schema mirror, writer,
and upload suites. The Phase 8 coordinator remains the integration pass.

## Residual Risks / Explicitly Deferred

1. **Schema migrations remain explicit.** A detected datatype drift needs a
  new Wikibase property and a controlled statement migration; Phase 9 does
  not attempt an unsafe in-place datatype mutation.
2. **Per-statement Wikidata cross-links** still require an approved Wikidata
  property slot and editorial semantics; the implementation remains
  fail-closed rather than inventing a claim.

## Critical files

- `backend/converter/wikibase/cloud_client.py`
- `backend/converter/wikibase/schema_bootstrap.py`
- `backend/converter/wikibase/hmo_exporter.py`
- `backend/converter/wikidata/property_mapping.py`
- `backend/app/pipeline/hmo_studio.py`
- `backend/app/routers/hmo_studio.py`
- `frontend/src/routes/HmoStudio.tsx`
