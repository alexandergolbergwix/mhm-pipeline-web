# HMO Wikibase Studio — Key files

> Up: [HMO Wikibase Studio](README.md)

| File | Purpose |
|---|---|
| `backend/converter/wikibase/ontology_schema_reader.py` | TTL → class/property entries, Wikibase datatype inference, `schema_entry_metadata_by_uri()` for schema AI verify |
| `backend/app/pipeline/hmo_schema_bootstrap.py` | Ontology-driven schema bootstrap; label de-dup; per-create commit; report cache for eval-agent |
| `backend/app/pipeline/hmo_schema_bootstrap_job.py` | Background job wrapper for the live bootstrap (~380 writes) |
| `backend/app/pipeline/hmo_item_build.py` | RDF → resolved items, cached in `HmoStudioItemCache` keyed by TTL hash + schema-mapping version |
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
| `backend/app/routers/hmo_studio.py` | `/runs/{id}/hmo-studio/*`: build/upload manifests, coverage, build-items, upload-items, status |
| `backend/app/routers/hmo_studio_items.py` | Per-item review API: list, override PATCH, reconcile, export/import, AI verify, AI-fix apply, single-item live `POST .../{local_id}/push` |
| `backend/app/routers/hmo_wikibase_schema.py` | Global `/hmo-wikibase-schema/*`: status, verify, bootstrap, last-report, schema AI verify |
| `backend/app/routers/wikibase_writes.py` | Read APIs for the audit log (project editor + admin views) |
| `backend/converter/wikibase/cloud_client.py` | `WikibaseCloudClient` (read-only) + `WikibaseCloudWriter` (OAuth2/bot; wikibaseintegrator entity writes; retry/backoff) |
| `backend/converter/wikibase/hmo_exporter.py` | TTL → entity drafts; RDF incoming-edge BFS stamps `control_numbers` per item; comment→`en` normalization + sentence dedup; label/description truncation (250) and string claim truncation (400) |
| `backend/converter/wikibase/hmo_export_quality.py` | `audit_entity_drafts` — label/description hygiene checks (Rule W-52 codes: production/timespan/latin-in-he/unbalanced-quotes/witness) |
| `backend/app/pipeline/hmo_export_quality_gate.py` | `assert_export_quality` — hard block: raises before caching on any export quality issue |
| `backend/scripts/hmo_item_verify_fixup_loop.py` | Qubrid/Kimi fixup loop: eval → diagnose → (rebuild) → re-eval per entity; `--persist-verdicts` |
| `backend/converter/wikibase/resolved_models.py` | `ResolvedWikibaseEntity` incl. `entity_type` + `control_numbers` persisted in `HmoStudioItemCache` |
| `backend/app/services/wikibase_credentials.py` | Server-held OAuth config → verified `WikibaseCloudWriter` (checks the session is the expected write user) |
| `backend/app/services/wikibase_audit.py` | `record_wikibase_write` — one `wikibase_cloud_writes` row per outcome, never raises; `fetch_latest_wikibase_writes` — portable "latest row per target" query powering the review table's upload-outcome fields |
| `backend/app/services/wikibase_user_access.py` | Auto-provisions per-curator wiki accounts on login/invite |
| `backend/app/models/hmo_studio_item_cache.py` | Per-run build cache (unique on `run_id`, fingerprinted) |
| `backend/app/models/hmo_coverage_cache.py` | Durable Postgres coverage cache (Rule W-39) |
| `backend/app/models/hmo_studio_item_override.py` | Curator override rows (labels/descriptions/aliases/statement edits/approved/ai_verdict) |
| `frontend/src/routes/HmoStudio.tsx` | Studio page: schema panel, manifest build/upload; `HmoItemsPanel` merges build/upload lifecycle bar + review table |
| `frontend/src/components/hmo/HmoItemDataStatusBadge.tsx` | Data status pill: new / will update existing / updated |
| `frontend/src/utils/hmoItemDataStatus.ts` | `resolveHmoItemDataStatus` — derives data status from mapping + last push |
| `frontend/src/components/hmo/HmoItemsPanel.tsx` | Review panel: lifecycle bar (`ItemBuildPanel`/`ItemUploadPanel` compact) + `HmoItemTable` |
| `frontend/src/components/hmo/*` | Review UI: `HmoItemTable`, `HmoItemDetailDrawer`, `HmoAuthorityEvidence` (persisted Mazal/KIMA/VIAF/Wikidata claims), `HmoItemAiVerdictBadge` (click pill → full reasoning popover), `HmoItemVerificationModal` (`useVerifyJob`), `ItemUploadPanel` (pre/post-upload AI verify), schema panels |
| `dev-docs/hmo-wikibase-studio-plan.md` | The 8-phase buildout plan + status |
