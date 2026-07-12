# Wikidata Studio — Key files

> Up: [Wikidata Studio](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/wikidata_studio.py` | Glue: DB rows → desktop builder input; fingerprint; override application; item serialisation + label enrichment; `local_id_for_item` |
| `backend/app/pipeline/wikidata_item_views.py` | Merged read model: cache + overrides + ledger QID + upload audit + stale-sanitized AI verdict |
| `backend/app/pipeline/wikidata_item_merge.py` | Apply curator overrides onto serialised item dicts |
| `backend/app/pipeline/wikidata_item_verify.py` | Persist AI verdicts to `WikidataItemOverride` after verify streams |
| `backend/app/pipeline/wikidata_verdict_cache.py` | Canonical Wikidata record-ID/MARC-context cache keys and stale-verdict sanitisation |
| `backend/app/pipeline/wikidata_qid_ledger.py` | Global `wikibase_entity_mappings` ledger (`wikidata:` keys) — adopt + idempotent upload |
| `backend/app/pipeline/wikidata_export_quality_gate.py` | Build-time ERROR-only export quality gate before cache upsert |
| `backend/app/pipeline/wikidata_studio_build_job.py` | Background build job (`wikidata_studio_build` kind) — calls `execute_studio_build` off the request path |
| `backend/app/pipeline/wikidata_upload.py` | `_prepare_for_upload` (reconcile + validator gate), `upload_items` / dry-run, moratorium check, `UploadOutcome`/`ReconcileOutcome` |
| `backend/app/pipeline/wikidata_upload_job.py` | Background upload/dry-run job (`wikidata_upload` kind), item-by-item progress + cancel |
| `backend/app/pipeline/wikidata_actions.py` | Prefab AI-agent actions (`audit_wikidata_item`, `autofix_from_wikidata`) for the eval-agent verify modal |
| `backend/app/pipeline/wikidata_autofix_apply.py` | Merge high-confidence AI fixes into override PATCH fragments |
| `backend/app/pipeline/wikidata_entity_compare.py` | Fetch live Wikidata entity + build Studio-vs-live diff rows |
| `backend/app/pipeline/wikidata_live_enrich.py` | Attach `wikidata_live` compare snapshots to items that carry a QID (autofix input) |
| `backend/app/pipeline/work_title_match.py` | Work-title normalization / variants for Mazal work matching |
| `backend/converter/wikidata/item_builder.py` | Public compatibility facade: orchestration, reconciliation, and legacy exports only |
| `backend/converter/wikidata/item_models.py` | Shared `WikidataItem` / `WikidataStatement` data structures |
| `backend/converter/wikidata/manuscript_projection.py` | Manuscript identity, catalog, digital-access, note, and relationship projection |
| `backend/converter/wikidata/manuscript_metadata.py` | Labels, instance types, language, physical description, and provenance projection |
| `backend/converter/wikidata/content_projection.py` | Contents, genre, and canonical-subject projection |
| `backend/converter/wikidata/person_linking.py`, `person_projection.py` | Role-safe manuscript links and authority-backed person construction |
| `backend/converter/wikidata/work_projection.py` | Work creation, labels, deduplication, and author links |
| `backend/converter/wikidata/work_candidates.py` | Source-aware MARC 500/505/NER eligibility decisions and compact evidence |
| `backend/converter/wikidata/property_mapping.py` | All P/Q constants (byte-parity with desktop; live-verified — Rule W-26) |
| `backend/converter/wikidata/item_validator.py` | The moat layer: ~20 checks, ERROR-severity issues block approval and writes |
| `backend/converter/wikidata/reconciler.py` | SPARQL dedup lookups; `ReconciliationUnavailableError` fail-closed contract |
| `backend/converter/wikidata/uploader.py` | Real `WikidataUploader` (wikibaseintegrator) — 4 Rule-38 guards + moratorium enforcement |
| `backend/converter/wikidata/quickstatements.py` | QuickStatements v2 exporter (gated download runs `_prepare_for_upload` first — R10) |
| `backend/app/routers/wikidata_studio.py` | All endpoints: build, gated QS, reconcile, upload, push, export/import, diagnostic CSV, overrides, compare, AI-verify |
| `backend/app/routers/wikidata_labels.py` | Batch `GET /wikidata/labels` — lazy live label lookup, 3-tier cached (process dict → `inference_cache` kind `wikidata.label`) |
| `backend/app/models/wikidata_studio_cache.py` | `WikidataStudioCache` — one row per `(run_id, approved_only)`, fingerprint-keyed build result |
| `backend/app/models/item_override.py` | `WikidataItemOverride` — curator diff + `approved` + `ai_verdict`/`ai_verdict_at` |
| `frontend/src/routes/WikidataStudio.tsx` | Studio page: modern review table (default) + legacy sidebar/table toggle; `useProjectEvents` → job-tray live progress |
| `frontend/src/components/wikidata/WikidataItemsPanel.tsx` | Orchestrator: lifecycle bar, upload hub, review table, drawer, verify modal |
| `frontend/src/components/wikidata/WikidataItemTable.tsx` | HMO-parity review table (filters, badges, pagination) |
| `frontend/src/components/wikidata/WikidataItemDetailDrawer.tsx` | Per-item drawer: overrides, compare, reconcile, verify/autofix/push |
| `frontend/src/components/wikidata/WikidataUploadPanel.tsx` | Upload hub: dry-run/live job, pre/post AI verify, moratorium pill |
| `frontend/src/components/shared/UploadOutcomeBadge.tsx` | Shared upload-outcome pill (HMO + Wikidata) |
| `frontend/src/components/wikidata/` | Also: `ItemValidatorBadge`, `ItemApprovalBadge`, `WikidataComparePanel`, `WikidataVerificationModal`, data-status + AI verdict badges |
| `frontend/src/api/wikidataStudio.ts` | Typed API client; `STUDIO_MAX_PAGE_SIZE` (500); `fetchAllStudioItems` paginates bulk loads |
| `frontend/src/utils/wikidataItemDataStatus.ts` | `new` / `will_update` / `updated` posture helper |
