# HMO Wikibase Studio — How it works

> Up: [HMO Wikibase Studio](README.md)

**Schema bootstrap (global).** `converter/wikibase/ontology_schema_reader.read_hmo_schema`
parses `hebrew-manuscripts.ttl` (~103 classes / ~277 properties) and maps each
`owl:ObjectProperty` / `owl:DatatypeProperty` to a Wikibase datatype via
`rdfs:range` (`_infer_datatype`: URI props like `hmo_source_uri` → `url`, folio
labels → `string`, CIDOC class ranges → `wikibase-item`). `schema_entry_metadata_by_uri`
supplies verify-enrichment fields (`property_kind`, `range_uri`, aliases) for AI
schema verify without re-bootstrapping.
`bootstrap_schema` (`hmo_schema_bootstrap.py:94`) creates every URI without a
schema-level mapping row (`wikibase_entity_mappings` with `run_id IS NULL`),
properties before classes (item claims reference PIDs). Duplicate `en` labels
(CIDOC/LRM reuse) are disambiguated as `{label} ({local_name})`
(`wikibase_label_for_entry`, :250). Each successful create commits its mapping
row immediately (`_record_mapping`, :335) so a crash mid-batch resumes without
re-creating live entities. Dry-run is synchronous in the router; live runs as a
`JOB_KIND_HMO_SCHEMA_BOOTSTRAP` background job (a `run_id` is required only to
anchor the job row). The last report is persisted both as a job result and on
disk under `backend/state/hmo_wikibase_schema/` where the eval-agent's
`hmo_wikibase_schema` evaluator reads it (subprocess boundary — no imports).
**Schema AI verify** (`hmo_schema_verify.py`) filters bootstrap entries,
enriches each row with OWL metadata, writes only uncached rows to the fixture,
and streams verdicts to `inference_cache` (no per-row DB column). The judge prompt
must include `description` + OWL context (Rule W-47 / eval-agent R17).

**Item build (per run).** `POST /runs/{id}/hmo-studio/build-items` (and
`POST /jobs` with `kind=hmo_item_build`) always enqueues a background job and
returns **201** (Rule W-106). The worker
(`hmo_item_build_job.py` → `execute_hmo_item_build`) runs authority refresh →
RDF rebuild → `build_items_for_run` (`hmo_item_build.py:88`) with **1-based
step progress** (`1/3`…`3/3`, `unit=steps`, Rule W-112) plus **nested
sub-progress** on long steps (`sub_unit=entities` during authority,
`sub_unit=records` during RDF — Rule W-113); the UI attaches via
`JobProgressInline` / job tray. By default
`refresh_authority=true` re-runs the matcher, then **forces an RDF rebuild**
from approved matches (and upserts `RdfArtifact`) so Mazal/KIMA/VIAF/Wikidata
payloads reach the TTL before export (Rule W-101). Fingerprint = SHA-256 over the
TTL bytes **and** the schema-mapping version (count + max `created_at` of
schema rows), so a bootstrap invalidates every run's cached build. On a miss it
runs `HmoWikibaseExporter().from_ttl` + `resolve_against_mappings`, computes the
SHACL report, and upserts `HmoStudioItemCache`. Each resolved entity carries
`entity_type` and `control_numbers` (RDF incoming-edge BFS — Rule W-48 / R23)
so the eval-agent can join parent MARC even when `source_uri` has no embedded
8+ digit id (e.g. shared `Person_*` nodes). An `UnmappedOntologyUriError`
fails the job (and the dedicated POST returns 409 only for “no RDF yet” when
`refresh_authority=false`). The exporter
truncates labels/descriptions to 250 chars and string/monolingualtext claim
values to 400 chars (`hmo_exporter.py:155,197-198`) so long free-text titles no
longer trigger `ModificationFailed`.
The class URI, every RDF predicate, and the `hmo_source_uri` reconciliation
predicate are mandatory schema mappings; any missing URI raises
`UnmappedOntologyUriError` before the cache is written.

Live canonical snapshots are also persisted one-per-entity in `hmo_canonical_entities` (migration `0035_hmo_canonical_entities`); the cache JSON remains a compatibility/read-model copy. The source-URI mapping is the only QID lookup key. Every successful write must be read back; a missing/timeout read-back or any partial write blocks canonical persistence rather than replacing durable rows with a partial set. Downstream readiness and backfill tooling can therefore query durable canonical rows without depending on cache retention.

Readiness is evaluated by `hmo_canonical_readiness.evaluate`, not by row count
alone. The shared result records expected/durable coverage, missing or malformed
snapshots, duplicate local IDs/source URIs/QIDs, authority conflicts, stale or
missing fingerprints, and live read-back failures. The canonical database gate,
projection shadow gate, and production E2E audit all consume this result.

The canonical-state boundary is implemented by the hmo_canonical module. Live Wikibase read-backs are normalized into a revision-independent fingerprinted entity shape; RDF and Wikidata projections consume durable `hmo_canonical_entities` rows rather than raw authority matches, transient item-cache snapshots, or pre-Wikibase RDF. Missing or incomplete durable rows fail closed. Wikidata claim projection uses `hmo_wikidata_pq_mapper` so project Cloud P/Q IDs never become public Wikidata identities by numeric coincidence (Rule W-100; see Wikidata Studio R38).

The item-build endpoint runs the matcher/re-enrichment service inside the HMO creation workflow by default (`refresh_authority=true`). This makes accepted authority evidence part of the canonical HMO build rather than a separate Authority UI action; callers may explicitly disable it for offline/cache-only diagnostics.

The HMO Studio lifecycle bar displays the same readiness counters, so curators can distinguish a dry-run/build preview from a canonical live-backed state before opening downstream projections. The route presents a four-step **Prepare → Review → Preview → Publish** workflow in plain language on the default **Items** sub-tab; schema maintenance and server connection details are behind advanced disclosures. Additional sub-tabs host **Wikidata coverage** (projection table), **RDF graph** (embedded `RdfGraphExplorer` with filters/canvas), and **Manifests** (build/upload + JSON preview via `GET …/manifests`). The canonical badge says `not yet confirmed`, `N entries confirmed`, or `all entries confirmed` rather than fabricating a denominator when no expected total is available.

The status endpoint also reports `canonical_live_count` and `canonical_ready`; readiness is true only when every built entity has a persisted live Wikibase read-back, preventing downstream projections from treating a dry-run preview as canonical.

**Review.** `fetch_merged_hmo_items` (`hmo_item_views.py:22`) merges the cached
build with `HmoStudioItemOverride` rows (label/description/alias edits,
`statement_edits`, `remove_statements`, `add_statements`, `approved`), joins
`wikibase_entity_mappings` for live QIDs (`status = created | would_create`),
and attaches per-item SHACL issues and AI verdicts. On the frontend,
`HmoStudio.tsx` places `ItemBuildPanel` + `ItemUploadPanel` in a compact
lifecycle bar directly above `HmoItemsPanel` (always visible). The review
table's **Data status** column shows `new (not uploaded)`, `will update
existing`, or `updated` per row. Override PATCHes route
through `apply_event` (entity type `hmo_item_override` — Rule W-21). AI verify
streams over SSE via the eval-agent subprocess; verdicts land in the
`inference_cache` (kind `ai_verdict`) and on the override row. The autofix
action first enriches QID-bearing items with a live-Wikibase compare snapshot
(`hmo_wikibase_live_enrich.py`), and accepted fixes are merged into the
override via `POST .../ai-fixes/apply`. It also joins the latest
`wikibase_cloud_writes` row per item (`upload_outcome`/`upload_message`/
`upload_at`) — see [upload outcomes + verify](upload-outcomes-and-verify.md).

**Upload (two-pass).** `upload_items_for_run` (`hmo_item_upload.py`).
Optional `local_ids` scopes pass 1 to those drafts (UI **Retry N failed**);
pass 2 still writes deferred links that touch the scope. Full-corpus uploads
still persist live canonical state; scoped retries do not.
While a live `hmo_item_upload` job runs, pass-1 progress carries
`item_outcome` / `recent_item_outcomes`; `ItemUploadPanel` patches only those
rows in the review table so Publication status / Last publication move during
the run without unmounting the table. Terminal succeed still silent-reloads.
`upload_outcome === failed` surfaces as Publication status **failed**.
Scalar snak mismatches that Wikibase reports as ``expected string`` (including
quantity→string drift on `max_nesting_depth` / P224) retry per-claim via the
string-compatible fallback (Rule W-91 class).
Pass 1: for each entity — already mapped → skip (or `update_item` merge when
`update_existing=True`); unmapped → SPARQL reconcile by `hmo_source_uri`; a hit
is **adopted** (mapping row recorded, no create); a miss → `create_item`. Every
successful create/adopt commits its `wikibase_entity_mappings` row immediately.
Pass 2 resolves `deferred_links` (item→item claims needing both live QIDs) via
`add_claim`; targets that never got created are reported `unresolved`, never
dropped. Dry-run is synchronous and network-free; live spawns a
`JOB_KIND_HMO_ITEM_UPLOAD` job with progress callbacks and cooperative
cancellation (partial result with `cancelled=True`, never an exception). The
reconcile PID is resolved **once** before the loop
(`resolve_source_uri_pid`, `hmo_item_upload.py:139`) — Rule W-40. The
per-entity create-or-update body is the shared `push_single_item` helper —
see [upload outcomes + verify](upload-outcomes-and-verify.md) for the
single-item push endpoint and the opt-in pre/post-upload AI verification
that also build on this pass.
Canonical read-back runs only after both passes complete with no failed or
unresolved links, so durable HMO state never represents a pass-1-only upload.

**Writer.** `WikibaseCloudWriter` (`cloud_client.py:296`) wraps
`wikibaseintegrator` for entity writes (`create_item`/`create_property`/
`update_item`/`add_claim`/`get_entity`) and raw MediaWiki API for page edits
(IIIF manifests, idempotent via content hash). `_post_with_retry` (:871) does up
to 6 attempts with exponential backoff 1s→30s cap, retrying 429/5xx/network
errors; WBI writes get the same budget via `BACKOFF_MAX_TRIES`/`MAXLAG=10`.
Entity-write failures return `EntityEditOutcome(status="failed")` — they never
raise into the batch loop.

**Credentials + audit.** `build_server_wikibase_writer`
(`wikibase_credentials.py:48`) builds the writer from server-held OAuth2 Heroku
config vars (`WIKIBASE_CLOUD_OAUTH_*`), authenticates, and 503s unless
`current_api_user()` equals `WIKIBASE_CLOUD_WRITE_USER` (default
`mhm-pipeline-web`). Every live write outcome (create/update/skip/failed, for
schema entries, items, claims, and manifest pages) appends a
`wikibase_cloud_writes` row via `record_wikibase_write` with actor, channel,
job, and target; the audit log is readable per project (`require_editor`) and
globally (`require_admin`) via `routers/wikibase_writes.py`.
`wikibase_user_access.py` additionally auto-provisions a wiki account per
curator on login (best-effort, never blocks login).

**Coverage + manifests.** `GET /coverage` returns the on-disk JSON if present,
else falls back to the durable `HmoCoverageCache` row (fingerprint = SHA-256 of
TTL bytes) and re-seeds the disk file; only a genuine miss enqueues a
`JOB_KIND_HMO_COVERAGE` job and returns 409 `hmo_coverage_in_progress` with the
job id. The job write-throughs both caches on success (`hmo_coverage_job.py:64-73`).
Each coverage class row already carries `wikidata_representation` + `notes` from
`projection_coverage.STRATEGY_BY_LOCAL_NAME`; the Studio table exposes those on
**hover** (native tooltip) and **click** (`CoverageClassRow` +
`ClickDetailPopover`, with Wikidata property links).
IIIF manifests are built deterministically from the TTL (`MS_<shelfmark>.json`)
and listed/previewed via `GET …/hmo-studio/manifests` (+ `…/manifests/{shelfmark}`).
They are uploaded under the `IIIF:` namespace, with per-manifest *intent* audited as
versioning events before the network call (`_audit_manifest_upload_intent`,
`routers/hmo_studio.py:649`). The upload path reads each successful live item back and stores the normalized canonical snapshot in both the durable HMO table and the compatibility cache. Wikibase calls are bounded; known legacy string-property datatype errors retry with serialized scalar values, while timeouts and unknown errors remain failed. Because wikibase.cloud does not expose a boolean snak type, boolean claims are converted to explicit `"true"`/`"false"` string claims before the first live call; time and monolingual-text claims retain their declared datatypes and only fall back when the server explicitly reports a scalar mismatch.


The HMO review table's External authority column now shows accepted persisted enrichment as source/count badges (Wikidata, VIAF, Mazal/NLI), while the Wikibase QID column remains explicitly local.

The default review table is aimed at library editors: title/shelfmark, type, review status, data quality, publication status, and AI review are visible first. Approval is tri-state (`Pending review`, `Approved`, `Rejected`) and saves with a status message; **Approve all visible** enqueues `hmo_item_bulk_approve` with the pending filtered `local_ids` (background job + progress bar / job tray — Rule W-105), never a synchronous browser PATCH loop. Column filter popups pass real per-value row counts (not distinct-only fake `(1)`s). Search and filter chips are visible without opening a technical menu. Record IDs, source URIs, local QIDs, authority evidence, and upload audit details are available through `Show technical columns`. Single-entry publish/update actions in `HmoItemDetailDrawer` always pass through `HmoPublishConfirmationDialog`; bulk publication remains preview-first and places the validation-error override under `Advanced: publication checks`.

**Phase 9 hardening.** `schema_mirror_report` exposes label/datatype drift. The
server writer reads `WIKIBASE_CLOUD_MIN_WRITE_INTERVAL_SECONDS` and throttles
entity writes only. Typed blank nodes receive deterministic graph-derived IDs.
Deferred object-property links retain an external source URI and pass two
resolves it against mappings from all completed runs, while unresolved targets
remain visible.
The existing Wikidata P2888/P973 manuscript links remain the only emitted HMO
cross-links until an approved per-statement Wikidata property exists.


Phase 1 canonical contract: `hmo_canonical_entities` stores explicit identity (`source_uri`, local QID, entity type), labels/descriptions/aliases, claims, accepted/rejected authority evidence, provenance, lifecycle status, and a revision-independent fingerprint. The full JSON snapshot remains for forward-compatible fields.


Phase 3 HMO **item upload** runs authority enrichment internally and applies
`hmo_authority_gate.validate_authority_rows` before any Wikibase write.
Approved external identifiers reused by different headings, or NLI/Mazal
identifiers misclassified as VIAF, block creation; `format_authority_gate_error`
lists the colliding ids/owners in the job error so Publish can be unblocked
by unapproving or correcting those AuthorityMatch rows. Local IIIF
**manifest build** (`POST …/build-manifests`) is RDF-only
and is not blocked by that gate. Every live read-back is checked for the mapped
QID and duplicate local, source, or QID identities before durable canonical
rows are replaced.


Phase 3 HMO-first entry: when no RDF artifact exists, `POST .../hmo-studio/build-items` internally builds the source graph from MARC, approved authority matches, and approved extraction entities, then immediately resolves HMO items. Curators no longer need to visit the RDF screen first.
