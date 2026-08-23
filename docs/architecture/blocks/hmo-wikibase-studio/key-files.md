# HMO Wikibase Studio — Key files

> Up: [HMO Wikibase Studio](README.md)

| File | Purpose |
|---|---|
| `backend/converter/wikibase/ontology_schema_reader.py` | TTL → class/property entries, Wikibase datatype inference, `schema_entry_metadata_by_uri()` for schema AI verify |
| `backend/app/pipeline/hmo_schema_bootstrap.py` | Ontology-driven schema bootstrap; label de-dup; per-create commit; report cache for eval-agent |
| `backend/app/pipeline/hmo_schema_bootstrap_job.py` | Background job wrapper for the live bootstrap (~380 writes) |
| `backend/app/pipeline/authority_re_enrich.py` | Shared re-enrich; optional throttled `on_progress` for build sub-bar (Rule W-113) |
| `backend/app/pipeline/hmo_item_build.py` | RDF → resolved items, cached in `HmoStudioItemCache` keyed by TTL hash + schema-mapping version |
| `backend/app/pipeline/hmo_item_build_exec.py` | Orchestrates authority → RDF → export for `hmo_item_build` jobs; nests `sub_*` progress (Rules W-106 / W-113) |
| `backend/app/pipeline/hmo_item_build_job.py` | Worker for `hmo_item_build`; forwards outer + nested progress |
| `backend/app/pipeline/hmo_manifest_build_job.py` | Worker for `hmo_manifest_build` (IIIF from TTL) |
| `backend/app/pipeline/hmo_manifest_upload_job.py` | Worker for `hmo_manifest_upload` (IIIF publish; Rule W-107) |
| `backend/app/pipeline/hmo_item_upload_job.py` | Worker for `hmo_item_upload` (dry-run + live; Rule W-107) |
| `backend/app/pipeline/hmo_authority_gate.py` | Fail-closed approved-ID uniqueness + rich conflict report |
| `backend/app/pipeline/hmo_authority_conflict_resolve.py` | Keep-one / unapprove-rest planner for HMO Studio (Rule W-109) |
| `backend/app/pipeline/hmo_item_shacl.py` | Runs SHACL once, buckets violations by item `local_id` (signal, not gate) |
| `backend/app/pipeline/hmo_item_merge.py` | Applies curator override rows onto built entities |
| `backend/app/pipeline/hmo_item_views.py` | `fetch_merged_hmo_items` — build cache + overrides + QID mappings + SHACL + AI verdicts, merged for the UI |
| `backend/app/pipeline/hmo_item_reconcile.py` | SPARQL exists-check by `hmo_source_uri` claim; fail-closed; `resolve_source_uri_pid` fast path |
| `backend/app/pipeline/hmo_item_upload.py` | Two-pass upload: pass 1 create/adopt/update items (via shared `push_single_item`), pass 2 deferred item→item links; SHACL gate (R19) |
| `backend/app/pipeline/hmo_item_shacl_gate.py` | `blocking_shacl_issues`, label/description sanitization for Wikibase writes |
| `backend/app/pipeline/hmo_item_upload_job.py` | Background job wrapper for the live item upload |
| `backend/converter/wikibase/label_sanitize.py` | Shared `und`→`en` language-code hygiene for exporter + cloud writer |
| `backend/app/models/wikibase_cloud_write.py` | Audit-log model; `OPERATION_ADOPT` distinguishes reconcile-match links from `OPERATION_CREATE` |
| `backend/app/pipeline/hmo_item_verify.py` / `hmo_item_actions.py` | AI-verify SSE stream + prefab actions (`audit_hmo_wikibase_item`, `autofix_hmo_wikibase_item`) |
| `backend/app/pipeline/hmo_schema_verify.py` / `hmo_schema_actions.py` | Same, for schema bootstrap entries (`hmo_wikibase_schema` evaluator) |
| `backend/app/pipeline/hmo_wikibase_live_enrich.py` | Fetches live entities (max 5 concurrent) and attaches compare snapshots for autofix |
| `backend/app/pipeline/hmo_studio.py` | IIIF manifest build/upload, coverage report, on-disk + durable Postgres coverage cache |
| `backend/app/pipeline/hmo_coverage_job.py` | Background coverage build (9–14 min: parses TTL twice via rdflib) |
| `backend/app/routers/hmo_studio.py` | `/runs/{id}/hmo-studio/*`: build/upload manifests, coverage, build-items (authority refresh → RDF rebuild → item export), upload-items, status |
| `backend/app/routers/hmo_studio_items.py` | Per-item review API: list, override PATCH, reconcile, export/import, AI verify, AI-fix apply, single-item live `POST .../{local_id}/push` |
| `backend/app/routers/hmo_wikibase_schema.py` | Global `/hmo-wikibase-schema/*`: status, mirror-report, verify, bootstrap, last-report, schema AI verify |
| `backend/app/routers/wikibase_writes.py` | Read APIs for the audit log (project editor + admin views) |
| `backend/converter/wikibase/cloud_client.py` | `WikibaseCloudClient` (read-only) + `WikibaseCloudWriter` (OAuth2/bot; wikibaseintegrator entity writes; retry/backoff) |
| `backend/converter/wikibase/hmo_exporter.py` | TTL → entity drafts; RDF incoming-edge BFS stamps `control_numbers` per item; comment→`en` normalization + sentence dedup; label/description truncation (250) and string claim truncation (400); full-date times use ±YYYY padding |
| `backend/converter/wikibase/hmo_export_quality.py` | `audit_entity_drafts` — label/description hygiene checks (Rule W-52 codes: production/timespan/latin-in-he/unbalanced-quotes/witness) |
| `backend/tests/unit/test_hmo_export_quality.py` | Authority evidence and global duplicate-QID withholding regressions |
| `backend/app/pipeline/hmo_export_quality_gate.py` | `assert_export_quality` — hard block: raises before caching on any export quality issue |
| `backend/scripts/hmo_item_verify_fixup_loop.py` | Qubrid/Kimi fixup loop: eval → diagnose → (rebuild) → re-eval per entity; `--persist-verdicts` |
| `backend/scripts/rebuild_run_rdf_and_items.py` | Controlled production rebuild with explicit `--upload` update/read-back and canonical persistence |
| `backend/scripts/backfill_hmo_canonical_entities.py` | Snapshot audit/backfill for persisted live read-backs |
| `backend/scripts/run_hmo_production_e2e.py` | Read-only production E2E audit for live read-back, exact Studio/QuickStatements diffs, canonical RDF, and false-positive rejection |
| `backend/scripts/run_hmo_phase8.py` | Guarded Phase 8 backend/frontend sweep plus ordered live bootstrap/upload idempotency report |
| `backend/converter/wikibase/resolved_models.py` | `ResolvedWikibaseEntity` incl. `entity_type` + `control_numbers` persisted in `HmoStudioItemCache` |
| `backend/scripts/audit_hmo_authority_consistency.py` | Deterministic export audit for authority coverage, malformed links, and duplicate local-QID mappings |
| `backend/app/services/wikibase_credentials.py` | Server-held OAuth config → verified `WikibaseCloudWriter` (checks the session is the expected write user) |
| `backend/app/services/wikibase_audit.py` | `record_wikibase_write` — one `wikibase_cloud_writes` row per outcome, never raises; `fetch_latest_wikibase_writes` — portable "latest row per target" query powering the review table's upload-outcome fields |
| `backend/app/services/wikibase_user_access.py` | Best-effort per-curator wiki provision on login/invite only (5 s cap; failed is no-retry — Rule W-123) |
| `backend/app/models/hmo_studio_item_cache.py` | Per-run build cache (unique on `run_id`, fingerprinted) |
| `backend/app/models/hmo_coverage_cache.py` | Durable Postgres coverage cache (Rule W-39) |
| `backend/app/models/hmo_studio_item_override.py` | Curator override rows (labels/descriptions/aliases/statement edits/approved/ai_verdict) |
| `frontend/src/routes/HmoStudio.tsx` | HMO Studio page: Items-default sub-tabs (coverage / RDF / Manifests), workflow + item review, advanced schema/config |
| `frontend/src/components/hmo/HmoItemDataStatusBadge.tsx` | Data status pill: new / will update existing / updated |
| `frontend/src/utils/hmoItemDataStatus.ts` | `resolveHmoItemDataStatus` — derives data status from mapping + last push |
| `frontend/src/components/hmo/HmoItemsPanel.tsx` | Review panel: authority-conflict resolver + lifecycle bar + `HmoItemTable`; **Approve all visible** → `hmo_item_bulk_approve` job |
| `frontend/src/components/hmo/HmoAuthorityConflictPanel.tsx` | Keep-one / unapprove-rest UI for shared Mazal/Wikidata/VIAF IDs (Rule W-109) |
| `frontend/src/components/hmo/SchemaBootstrapPanel.tsx` | Schema class/property mapping status, dry-run/live bootstrap controls, report results, and schema AI verification |
| `frontend/src/components/rdf/RdfGraphExplorer.tsx` | Embeddable RDF viewport + GraphFilters + canvas/list (no build chrome) |
| `frontend/src/components/hmo/CoverageClassRow.tsx` | Coverage table row: hover summary + click detail popover for projection strategy |
| `frontend/src/utils/hmoCoverageExplain.ts` | Hover/click explanation text and Wikidata property labels for coverage rows |
| `frontend/src/components/hmo/ItemBuildPanel.tsx` | RDF-backed item build/rebuild controls and build-status summary |
| `frontend/src/components/hmo/ItemUploadPanel.tsx` | Dry-run-first item upload controls, SHACL override, background-job progress, upload outcomes, **Retry N failed** (`local_ids` scope) |
| `frontend/src/components/hmo/HmoPublishConfirmationDialog.tsx` | Accessible confirmation gate for single-entry publication and updates |
| `frontend/src/api/hmoWikibaseSchema.ts` | Typed schema status/bootstrap/report client, including live bootstrap job detection |
| `frontend/src/api/hmoStudio.ts` | Typed item build, upload, status, and upload-job result helpers |
| `frontend/src/components/hmo/*` | Review UI: `HmoItemTable`, `HmoItemDetailDrawer`, `HmoAuthorityEvidence` (persisted Mazal/KIMA/VIAF/Wikidata claims), `HmoItemAiVerdictBadge` (click pill → full reasoning popover), `HmoItemVerificationModal` (`useVerifyJob`), `ItemUploadPanel` (pre/post-upload AI verify), schema panels |
| `dev-docs/hmo-wikibase-studio-plan.md` | The 8-phase buildout plan + status |

| `backend/app/models/hmo_canonical_entity.py` | Durable canonical HMO entity contract and projection source |
| `backend/app/migrations/versions/0036_hmo_canonical_contract.py` | Adds explicit canonical identity, claims, evidence, provenance, and status fields |
| `backend/app/migrations/versions/0037_wikidata_accept_foreign.py` | Wikidata Studio override: `accept_foreign_modify` + `accepted_foreign_qid` (Rule W-99) |

| `backend/app/pipeline/hmo_canonical.py` | Canonical entity contract plus raw Wikibase read-back normalization (labels, aliases, claims, ontology property URIs) |
| `backend/app/pipeline/hmo_canonical_wikidata.py` | Canonical HMO → Wikidata Studio native items (uses PQ mapper) |
| `backend/converter/wikidata/hmo_wikidata_pq_mapper.py` | Project Wikibase / ontology → public Wikidata P/Q (Rule W-100); shared with Wikidata Studio |
