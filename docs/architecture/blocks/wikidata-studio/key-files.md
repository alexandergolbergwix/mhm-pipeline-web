# Wikidata Studio — Key files

> Up: [Wikidata Studio](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/wikidata_studio.py` | Glue: DB rows → desktop builder input; fingerprint; override application; item serialisation + label enrichment; `local_id_for_item` |
| `backend/app/pipeline/wikidata_studio_build_job.py` | Background build job (`wikidata_studio_build` kind) — calls `execute_studio_build` off the request path |
| `backend/app/pipeline/wikidata_upload.py` | `_prepare_for_upload` (reconcile + validator gate), `upload_items` / dry-run, moratorium check, `UploadOutcome`/`ReconcileOutcome` |
| `backend/app/pipeline/wikidata_upload_job.py` | Background upload/dry-run job (`wikidata_upload` kind), item-by-item progress + cancel |
| `backend/app/pipeline/wikidata_actions.py` | Prefab AI-agent actions (`audit_wikidata_item`, `autofix_from_wikidata`) for the eval-agent verify modal |
| `backend/app/pipeline/wikidata_autofix_apply.py` | Merge high-confidence AI fixes into override PATCH fragments |
| `backend/app/pipeline/wikidata_entity_compare.py` | Fetch live Wikidata entity + build Studio-vs-live diff rows |
| `backend/app/pipeline/wikidata_live_enrich.py` | Attach `wikidata_live` compare snapshots to items that carry a QID (autofix input) |
| `backend/app/pipeline/work_title_match.py` | Work-title normalization / variants for Mazal work matching |
| `backend/converter/wikidata/property_mapping.py` | All P/Q constants (byte-parity with desktop; live-verified — Rule W-26) |
| `backend/converter/wikidata/item_validator.py` | The moat layer: ~20 checks, ERROR-severity issues block approval and writes |
| `backend/converter/wikidata/reconciler.py` | SPARQL dedup lookups; `ReconciliationUnavailableError` fail-closed contract |
| `backend/converter/wikidata/uploader.py` | Real `WikidataUploader` (wikibaseintegrator) — 4 Rule-38 guards + moratorium enforcement |
| `backend/converter/wikidata/quickstatements.py` | QuickStatements v2 exporter (no reconcile/validate gate — see R10) |
| `backend/app/routers/wikidata_studio.py` | All endpoints: build, QS download, reconcile preview, upload, item override PATCH/DELETE, compare, AI-fix apply, AI-verify SSE |
| `backend/app/routers/wikidata_labels.py` | Batch `GET /wikidata/labels` — lazy live label lookup, 3-tier cached (process dict → `inference_cache` kind `wikidata.label`) |
| `backend/app/models/wikidata_studio_cache.py` | `WikidataStudioCache` — one row per `(run_id, approved_only)`, fingerprint-keyed build result |
| `backend/app/models/item_override.py` | `WikidataItemOverride` — curator diff per `(run_id, local_id)` + item-level `approved` |
| `frontend/src/routes/WikidataStudio.tsx` | Studio page: item list, rebuild/force-rebuild, reconcile, dry-run/live upload via run-job |
| `frontend/src/components/wikidata/` | `ItemValidatorBadge`, `ItemApprovalBadge`, `WikidataComparePanel`, `WikidataVerificationModal` |
| `frontend/src/api/wikidataStudio.ts` | Typed API client (`StudioItem`, `ValidationIssue`, reconcile/upload DTOs) |
