# HMO Wikibase Studio (build, upload, canonical read-back)

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

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

### Rule W-53 — HMO item build: honest-negative grounding, person heading fidelity, second-pass label hygiene (added 2026-07-10)

Export (5) on run ``48ba6c13`` (Kimi K2.5, override cache) scored 1750
pass / 155 partial / 4 fail; the residuals were almost entirely
``name_ok=partial`` (thin/malformed/wrong-language labels + descriptions) plus
4 person-typing fails. This rule closes them:

- **Person heading fidelity (`rdf_helpers.py`).**
  ``clean_person_display_name`` now **uninverts** a trailing ``אבן`` after a
  ``Surname, Given`` heading (``סיד, יצחק אבן`` → ``יצחק אבן סיד``, Ibn Sid)
  instead of deleting the Ibn; a bare trailing ``בן`` is still stripped as a
  truncated patronymic. ``infer_person_type`` no longer lets a 110/610/710/810
  field force ``organization`` when the name is an obvious personal
  ``Surname, Given`` heading with no institutional keyword — NLI catalogs
  former-owner **persons** (Adler/Roth/Sassoon) under 710. ``… Collection`` /
  ``Library of …`` still resolve to ``E74_Group`` on the institutional keyword.

- **Honest-negative event grounding (`graph_builder.py`).** When a manuscript
  records no production place/date/scribe, ``_add_production_event`` grounds the
  description in the title + shelfmark and states the negative
  (``… production place and date are not recorded in the catalog record.``)
  instead of the label-repeating template. Paleographical_Unit comments name
  the script/scribe when known, else say ``… not recorded in the catalog``. The
  synthetic *Unidentified textual content* placeholder work no longer mints a
  circular TextTradition/Witness/Bridge (tagged ``synthetic`` and skipped).

- **Second-pass label hygiene (`rdf_helpers.py` + `graph_builder.py`).**
  ISBD ``X" ו"Y`` quoted-conjunctions collapse to ``X וY`` before the gershayim
  parity check; a ``|`` publication note is truncated off titles; single-token
  vav-prefix construct fragments (``ותשובת``) are rejected as work titles;
  over-long ISBD titles use the pre-colon head as the label with the full title
  in the description (``shorten_isbd_label``). Person labels emit a single
  longest-Hebrew-form ``he`` label; ``E74_Group`` descriptions are org-worded.
  One-word manuscript ``he`` titles are shelfmark-disambiguated; every
  ``E53_Place`` mint site (subject / provenance-event / holding-institution /
  related-place) stamps a manuscript-scoped comment with a script-correct
  language tag.

- **Gate + rubric + salt.** ``hmo_export_quality`` adds
  ``production_description_repeats_label``. The item rubric
  (``hmo_wikibase_item.md``) gains 3d empty-production honest-negative, 3f
  (MARC-heading order + subject-heading persons), 3g (generic-MS-title +
  controlled-vocabulary terms); the evaluator's SYSTEM-LABELED grounding and
  ``Paleographical_Unit`` structural typing mirror it.
  ``HMO_ITEM_VERDICT_SCHEMA`` → ``w53_v1`` (Rule W-51) so old verdicts miss
  automatically — a rebuild + re-verify is sufficient, no ``override_cache``.

**Curator ops after deploy:** RDF rebuild → HMO **Rebuild (skip cache)** →
re-verify (no override cache). Reupload only when live wiki labels/descriptions
should change.

Mirror caveat (Rule W-43 residual): the four ``graph_builder.py`` /
``rdf_helpers.py`` edits still owe a hand-port to the desktop pipeline repo —
do **not** run ``sync_converter_to_web.sh``.

Tests: ``test_rdf_helpers.py`` (Ibn/710-person, quote/pipe/vav/long-title),
``test_graph_builder_codicological_labels.py`` (empty-production, PU comment,
no synthetic tradition, long-title head), ``test_hmo_export_quality.py``
(``production_description_repeats_label``),
``eval-agent/tests/test_hmo_wikibase_items.py`` (rubric grounding).

---

### Rule W-55 — Canonical ontology namespace is `https://w3id.org/mhm/ontology#` (added 2026-07-10)

The HMO ontology + all instance URIs moved off the non-dereferenceable
placeholder ``http://www.ontology.org.il/HebrewManuscripts/2025-12-06#`` (a
domain the project does not serve — it returns 404) to the project's real
permanent identifier namespace ``https://w3id.org/mhm/ontology#``.

`w3id.org/mhm` is the project's registered w3id.org permalink prefix (see
``pipeline/docs/w3id/htaccess``): ``/ontology`` → the ontology TTL on GitHub,
``/ontology#Term`` dereferences to it, ``/manuscript/<cn>`` / ``/person/<id>`` /
``/work/<id>`` → the live ``mhm-hmo.wikibase.cloud`` items, ``/`` → the wiki
landing. The IIIF/P2888 permalinks already used ``w3id.org/mhm`` (this aligns
the ontology + RDF instance namespace with them).

The migration is a single base-string swap applied repo-wide (30 files: the
`HM`/`HMO` namespace constants in ``converter/config/namespaces.py``, the
research SPARQL prefixes, ``property_mapping.HMO_NS_TEMPLATE``, the active
ontology TTLs — ``hebrew-manuscripts.ttl`` / ``shacl-shapes.ttl`` /
``controlled-vocabularies.ttl`` — and every test fixture/assertion). The
archival ``hebrew-manuscripts-original.ttl`` is left on the old namespace as
the pre-migration record (it is not loaded by code).

**Two hard consequences before any deploy/upload:**

1. **Live Wikibase reconciliation (Rule W-30 / W-42).** HMO items already on
   ``mhm-hmo.wikibase.cloud`` carry ``hmo_source_uri`` values in the **old**
   namespace. New builds emit ``w3id`` source URIs, so reconcile-by-source-URI
   will NOT match the existing items → **mass duplicate creation** on the next
   upload. A run's items MUST NOT be re-uploaded after this change until the
   live ``hmo_source_uri`` values are migrated old→new (or the reconciler is
   made domain-agnostic on the URI local-name). This is the April-failure class
   the upload guards exist to prevent.
2. **Stale build artifacts.** Every stored ``RdfArtifact`` TTL + ``HmoStudio
   ItemCache`` and each run's on-disk ``manuscripts.ttl`` still hold old URIs;
   research SPARQL now uses the new prefix, so runs must be **RDF-rebuilt** for
   the Geography/graph tabs and Studio to reflect the change. Build/verdict
   caches are content-addressed and self-invalidate.

**Mirror parity (Rule R5 / W-43 residual):** the desktop pipeline's
``converter/config/namespaces.py`` + ontology TTLs still hold the old
namespace. They MUST get the same swap before any ``/sync-from-desktop`` — a
sync now would revert the web change. Do not run ``sync_converter_to_web.sh``
until desktop is migrated.

Tests: the full suite asserts the new URIs; ``test_rdf_shacl_conformance.py``
(run-48ba6c13 corpus) reconfirms 0 violations under the new namespace.

**Data migration (``scripts/migrate_hmo_source_uri_namespace.py``).** The
namespace swap orphaned two live data surfaces, both fixed by this dry-run-first
script (Rule W-51-style deterministic prefix swap):
1. ``wikibase_entity_mappings.ontology_uri`` — 2580 schema + instance rows on
   the prod DB (``--skip-wiki``). **Required**: the deployed code resolves
   NEW-namespace URIs, so without this every ontology/PID lookup misses and HMO
   Studio build/upload is broken.
2. Live wiki ``hmo_source_uri`` (P293) claims — 1520 items (``--skip-db``).
   Optional once Rule W-56 lands (reconcile matches both namespaces), but worth
   running for a fully-canonical wiki.

Run: ``cd backend && DATABASE_URL=… python -m scripts.migrate_hmo_source_uri_namespace``
(dry-run) then ``--apply``.

---

### Rule W-56 — Wikibase reconcile queries the instance's own direct-property URI, namespace-agnostically (added 2026-07-10)

Two bugs in ``hmo_item_reconcile.py`` (found during the Rule W-55 migration):

1. **``wdt:`` resolves to Wikidata, not the project instance.** The reconcile
   SPARQL used ``?item wdt:PNNN "…"``; on ``mhm-hmo.wikibase.cloud`` the
   ``wdt:`` prefix defaults to ``http://www.wikidata.org/prop/direct/``, so the
   query matched **nothing** — reconcile had silently never found an existing
   item (failing open to "create", the duplicate-creation risk the guards exist
   to prevent). Fix: build the instance's own direct-property URI from
   ``settings.wikibase_cloud_base_url`` (``{base}/prop/direct/PNNN``).
2. **Single-namespace match orphans migrated items.** After Rule W-55, live
   items carry the OLD ``hmo_source_uri`` namespace while new builds emit the
   new one. ``_reconcile_uri_variants`` now matches BOTH the current
   (``https://w3id.org/mhm/ontology#``) and legacy
   (``http://www.ontology.org.il/HebrewManuscripts/2025-12-06#``) forms via a
   SPARQL ``VALUES`` clause, so a namespace migration never causes a re-upload
   to duplicate an existing item — regardless of whether the wiki claims were
   rewritten.

Any future Wikibase-Cloud SPARQL that filters by a project property MUST use
the instance's direct-property URI, never ``wdt:``. Tests:
``test_hmo_item_reconcile.py::test_query_uses_instance_property_uri_and_both_namespaces``.

---

### Rule W-85 — HMO entity links MUST use the live project Wikibase URL (added 2026-07-21)

Ontology source URIs are provenance identifiers, not browsable Wikibase pages.
When an HMO item has a mapped local QID, curator links must target the project
Wikibase `/wiki/Item:Q…` page; source URIs remain metadata only.

### Rule W-86 — Authority identifiers MUST be unique across HMO entities (added 2026-07-21)

A Wikidata QID, VIAF cluster, KIMA ID, or Mazal ID must not be accepted for
multiple distinct HMO source entities. The HMO exporter performs a global
collision gate after per-entity ambiguity checks: every colliding evidence row
is retained as `accepted: false` with a reason, and the corresponding external
claim is removed before caching or upload. Cross-source VIAF disagreement also
withholds the Wikidata candidate. This prevents a plausible label match from
creating a false-positive identity link.


### Rule W-89 — Canonical rollout and Authority retirement MUST be fail-closed (added 2026-07-23)

HMO Wikibase live read-back is the source of truth for both downstream
projections. Canonical-first therefore rolls out by explicit run UUID or a
deterministic percentage only after the durable canonical gate and a real
HMO→RDF/HMO→Wikidata shadow audit pass. The global flag remains disabled when
any read-back, duplicate identifier, false-positive authority conflict, or
projection build is unresolved. The former standalone Authority mutation
surface returns HTTP 410 and logs telemetry by default; its matcher remains
internal to HMO creation and read-only historical AuthorityMatch data remains
for provenance and migration audits. Test: `test_canonical_rollout.py` and
`run_hmo_production_e2e.py`.


### Rule W-90 — Live HMO read-back MUST use source-URI mappings and normalize Wikibase JSON (added 2026-07-23)

The first completed production upload wrote more than 100,000 Wikibase claims
but produced zero canonical rows because upload mappings are keyed by RDF source
URI, not local display ID. Raw Wikibase Integrator JSON also stores language
values and claims in nested MediaWiki shapes. Read-back now resolves QIDs by
`source_uri`, decodes labels/descriptions/aliases/claims, maps schema PIDs back
to ontology property URIs, and persists only the canonical contract. The
canonical gate remains blocked when any live item cannot be read back. Test:
`test_hmo_canonical_readback.py`.


### Rule W-91 — Unsupported Wikibase scalar datatypes MUST be normalized before writes (added 2026-07-23)

The first corrected production migration still sent a boolean snak in the
whole-item update before attempting its per-claim fallback. wikibase.cloud
rejects the boolean datatype, so a large run generated one avoidable failed API
call per item and exceeded the safe migration window. HMO create/update now
serializes boolean claims as explicit strings before the first remote call;
time and monolingual-text values remain native and only use the narrow scalar
fallback when the server explicitly reports a mismatch. Test:
`test_unsupported_boolean_claims_are_serialized_before_live_write`.

### Rule W-92 — Canonical projections MUST fail closed on transient cache fallback (added 2026-07-23)

Canonical RDF and Wikidata/QuickStatements projections read only durable
`hmo_canonical_entities` rows produced by complete live Wikibase read-back.
They MUST reject missing, malformed, incomplete, or duplicate rows rather than
falling back to `HmoStudioItemCache.canonical_live`, MARC, AuthorityMatch, or
NER inputs. The projection shadow report classifies item and RDF differences;
blocking or unclassified differences cannot report promotion readiness. The
`verify_hmo_projection_gate.py --allow-difference` option is diagnostic-only.
Tests: `test_projection_shadow_compare.py` and the canonical RDF/Wikidata
projection suites.

### Rule W-93 — Retired Authority mutations MUST remain fail-closed and observable (added 2026-07-23)

Standalone Authority mutation routes return HTTP 410 while
`legacy_authority_mutations_enabled` is false. The retirement guard emits
structured `legacy_authority_mutation_retired` telemetry containing the route
family, run, actor, and status code. The legacy `/runs/:runId` frontend route
immediately replaces with `/runs/:runId/hmo-studio` (no deprecation interstitial);
stale Authority navigation is removed from active run/project surfaces.
Test: `backend/tests/unit/test_authority_retirement.py`.

### Rule W-94 — Canonical readiness MUST be integrity-based, not count-based (added 2026-07-23)

The canonical HMO database gate, projection gate, and production E2E audit
share `app.pipeline.hmo_canonical_readiness.CanonicalReadiness`. Readiness
requires exact expected/durable coverage and zero missing or malformed rows,
duplicate local IDs/source URIs/QIDs, authority conflicts, stale or missing
fingerprints, and live read-back failures. A row-count match alone MUST NOT
promote a canonical cohort. Test: `test_hmo_canonical_readiness_contract.py`.

### Rule W-95 — HMO creation MUST gate authority and live identity before canonical replacement (added 2026-07-23)

`upload_items_for_run` runs `validate_authority_rows` over approved
`AuthorityMatch` rows before any external Wikibase write. Every mapped item is
read back and must retain the mapped QID; duplicate local IDs, source URIs, or
Wikibase QIDs abort before replacing durable `hmo_canonical_entities` rows.
Read-back failure remains all-or-nothing. Local IIIF manifest generation
(`POST …/hmo-studio/build-manifests`) is RDF-only and MUST NOT apply this
authority gate. Tests:
`backend/tests/test_hmo_item_upload.py`,
`backend/tests/test_hmo_studio_ttl_restore.py`
(`test_build_manifests_ignores_authority_conflicts`).

### Rule W-96 — HMO item builds MUST reject unmapped reconciliation predicates (added 2026-07-23)

`resolve_against_mappings` must require schema mappings for the item class,
every RDF predicate, and `hmo_source_uri` before writing
`HmoStudioItemCache`. Missing mappings raise `UnmappedOntologyUriError` and
the build is not cached. A skipped source-URI claim would create an item that
cannot be safely reconciled on upload and would hide ontology drift.
Test: `backend/tests/unit/test_hmo_exporter_resolution.py`.

### Rule W-97 — HMO canonical read-back MUST follow deferred-link writes (added 2026-07-23)

`upload_items_for_run` must complete pass 2 and reject unresolved or failed
links before reading back live entities into `hmo_canonical_entities`. A
pass-1-only snapshot is not canonical because it omits item-to-item claims
that the upload may add in pass 2. Test: `backend/tests/test_hmo_item_upload.py`.

### Rule W-101 — HMO entities MUST be multi-source enriched through fail-closed matching (added 2026-07-24)

HMO Wikibase items should carry the richest *accepted* evidence from
Mazal, KIMA, VIAF, and Wikidata — but enrichment richness never
overrides matcher fail-closed policy. Postgres production
`match_place` uses the same multi-row KIMA disambiguation as SQLite
(`converter/authority/kima_disambiguate.pick_kima_place_row`): conflicting
Wikidata QIDs abstain unless one exact primary name uniquely wins
(Rule W-84 parity). Fuzzy KIMA candidates are likewise multi-row +
QID-abstain, never `LIMIT 1`.

When HMO `build-items` runs with `refresh_authority=true` (the default),
the router MUST rebuild RDF from the refreshed approved
`AuthorityMatch` rows, upsert `RdfArtifact`, and force the item-cache
rebuild — a stale TTL must not hide new Mazal/KIMA/VIAF/Wikidata
payloads. GraphBuilder mints person `hm:mazal_id` + VIAF-cluster
`owl:sameAs` (GND/LC/ISNI/BnF/J9U) and place `hm:geonames_id` from
accepted evidence only; bare cluster digits are never mis-parsed as
VIAF. Collision gates (Rule W-86) and upload authority validation
(Rule W-95) remain hard blocks.

Tests: `test_kima_disambiguate.py`, `test_place_coords_in_rdf.py`.

### Rule W-102 — Four HMO pillars: Wikibase root, Wikidata map, ontology mirror, multi-source richness (added 2026-07-24)

The scholarly spine of the browser deployment is the project HMO Wikibase
(`mhm-hmo.wikibase.cloud`). Four invariants:

1. **Wikibase is the root.** After a successful live upload + read-back,
   durable `hmo_canonical_entities` are the source of truth for RDF rebuilds
   and Wikidata Studio (`source=canonical`). First publish may still mint
   items from MARC→RDF→export; once canonical rows exist and readiness
   passes, downstream projections MUST NOT silently fall back to MARC /
   AuthorityMatch / item-cache drafts (Rule W-92).
2. **Perfect Wikidata mapper.** Public Wikidata P/Q come only from
   `hmo_wikidata_pq_mapper` + ontology `owl:equivalentProperty` /
   `owl:equivalentClass`. Never treat a project `P`/`Q` as a Wikidata ID
   (Rule W-100). Mapper local-names that become Wikibase claims MUST be
   declared in `hebrew-manuscripts.ttl`.
3. **1:1 ontology mirror.** Schema bootstrap + item resolve cover every
   `owl:Class` / property in `backend/ontology/hebrew-manuscripts.ttl`.
   GraphBuilder MUST NOT emit undeclared `hm:` predicates or individuals
   (`mazal_id`, `kima_id`, `authority_id` declared; condition uses
   `hm:Good`, not a synthetic `Good_condition`). Unmapped URIs fail the
   build (Rule W-96 / W-88).
4. **Richest fail-closed enrichment.** HMO entities are enriched from
   Mazal, KIMA, VIAF, and Wikidata at build time (`refresh_authority` →
   RDF rebuild — Rule W-101). Ambiguous matches abstain; accepted IDs
   mint the full safe claim surface.

Tests: `test_hmo_ontology_graphbuilder_parity.py`,
`test_hmo_wikidata_pq_mapper.py` (TTL equivalent sync).

### Rule W-109 — HMO Studio MUST resolve AuthorityMatch ID collisions in-place (added 2026-07-25)

Upload fail-closed on shared Mazal/Wikidata/VIAF IDs among approved matches
(Rules W-86 / W-95), but the standalone Authority mutation UI is retired
(HTTP 410, Rule W-93). Curators had no path to unapprove colliding rows
without flipping `legacy_authority_mutations_enabled`.

Invariant:

1. `GET /runs/{id}/hmo-studio/authority-conflicts` returns conflict groups
   with match ids, roles, CNs, and invalid VIAF rows (rich report from
   `build_authority_conflict_report`).
2. `POST …/authority-conflicts/resolve` accepts `keep_match_ids` /
   `unapprove_match_ids`, unapproves siblings via `_apply_approval` +
   `_record_match_event` (Rule W-21), and does **not** reopen legacy
   Authority routes.
3. HMO Studio shows `HmoAuthorityConflictPanel` above the lifecycle bar:
   pick one keep per shared ID → unapprove the rest → rebuild/retry upload.

Tests: `test_hmo_authority_gate.py`, `test_hmo_authority_conflicts_router.py`,
`frontend/e2e/hmo-wikibase-items.spec.ts` (conflict panel).

### Rule W-111 — All HMO URL claims MUST strip MARC quote wrappers (added 2026-07-25)

Live HMO upload stopped mid-corpus with WBI
`Invalid URL "http://web.nli.org.il/…nli-public-domain.aspx"` — the quotes are
part of the rejected value. Rule W-87 cleaned `digital_access_url` /
`iiif_manifest_url`, but `hm:restriction_url` from MARC 540/939 `$u` was still
emitted raw into the item cache.

Invariant:

1. `clean_url_value` in `converter/rdf/rdf_helpers.py` is the shared stripper
   for every `xsd:anyURI` / Wikibase `url` path.
2. GraphBuilder cleans `restriction_url` before RDF emit.
3. `hmo_exporter` cleans `url` datatype claims at resolve time.
4. `_sanitize_url_claim` / `_build_live_wbi_claims` clean again on write so a
   stale cached build can **Retry N failed** without an RDF rebuild.

Tests: `test_graph_builder_codicological_labels.py`
(`test_restriction_url_strips_marc_quote_wrappers`),
`test_hmo_item_upload.py` (`test_url_claims_strip_marc_quote_wrappers_before_wbi`).
