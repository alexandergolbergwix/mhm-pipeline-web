# Wikidata Studio — Key files

> Up: [Wikidata Studio](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/wikidata_studio.py` | Glue: DB rows → desktop builder input; fingerprint; override application; item serialisation + label enrichment; `local_id_for_item` |
| `backend/app/pipeline/hmo_canonical_wikidata.py` | HMO Wikibase read-back → native Wikidata item adapter; CN-indexed summarized-node rollup onto manuscript/person/work (Rule W-117); `PUBLIC_WIKIDATA_ENTITY_TYPES` gate; `filter_public_wikidata_items` / stale-cache detection (W-118); label hygiene + work evidence (W-120) |
| `backend/converter/wikidata/hmo_wikidata_pq_mapper.py` | Project Wikibase / ontology → public Wikidata P/Q allowlist (Rule W-100); never ID-identity |
| `backend/app/pipeline/hmo_wikidata_projection.py` | Fail-closed adapter accepting only exact HMO manuscript URIs and unambiguous local QIDs for Wikidata projection |
| `backend/app/pipeline/wikidata_item_views.py` | Merged read model: cache + overrides + ledger QID + upload audit + stale-sanitized AI verdict |
| `backend/app/pipeline/wikidata_item_merge.py` | Apply curator overrides onto serialised item dicts |
| `backend/app/pipeline/wikidata_item_verify.py` | Persist AI verdicts to `WikidataItemOverride` after verify streams |
| `backend/app/pipeline/wikidata_verdict_cache.py` | Canonical record/MARC/local-target evidence, durable-target preservation, prompt-complete cache keys, and stale-verdict sanitisation |
| `backend/app/pipeline/wikidata_qid_ledger.py` | Global `wikibase_entity_mappings` ledger (`wikidata:` keys) — adopt + idempotent upload |
| `backend/app/pipeline/wikidata_export_quality_gate.py` | Build-time ERROR-only export quality gate before cache upsert |
| `backend/scripts/check_wikidata_export_quality.py` | Read-only compact audit of work identity, author, language, quote, validation, and export-field failures |
| `backend/app/pipeline/wikidata_studio_build_job.py` | Background build job (`wikidata_studio_build` kind) — `reconcile=False`, threadpool canonical build (W-119) |
| `backend/app/pipeline/wikidata_upload.py` | `resolve_upload_mode` / `upload_target`, `_prepare_for_upload`, foreign-accept map, `UploadOutcome` |
| `backend/converter/wikidata/uploader.py` | Real `WikidataUploader` — Rule-38 guards + `allow_live` / moratorium |
| `backend/app/pipeline/wikidata_existence.py` | Action API `wbgetentities` alive check + ownership classify + QID-bound foreign accept |
| `docs/wikidata-data-access.md` | Wikidata:Data_access API map + MHM write-policy matrix (Rule W-99) |
| `backend/app/pipeline/wikidata_upload_job.py` | Background upload/dry-run job (`wikidata_upload` kind), item-by-item progress + cancel |
| `backend/app/pipeline/wikidata_actions.py` | Prefab AI-agent actions (`audit_wikidata_item`, `autofix_from_wikidata`) for the eval-agent verify modal |
| `backend/app/pipeline/wikidata_autofix_apply.py` | Merge high-confidence AI fixes into override PATCH fragments |
| `backend/app/pipeline/wikidata_entity_compare.py` | Fetch live Wikidata entity + build Studio-vs-live diff rows |
| `backend/app/pipeline/wikidata_live_enrich.py` | Attach `wikidata_live` compare snapshots to items that carry a QID (autofix input) |
| `backend/app/pipeline/work_title_match.py` | Work-title normalization / variants for Mazal work matching |
| `backend/converter/wikidata/item_builder.py` | Public compatibility facade: orchestration, reconciliation, and legacy exports only |
| `backend/converter/wikidata/item_models.py` | Shared `WikidataItem` / `WikidataStatement` data structures |
| `backend/converter/wikidata/manuscript_projection.py` | Manuscript identity, catalog, digital-access, note, and evidence-gated related_works → P1574 (Rule W-114) |
| `backend/converter/wikidata/manuscript_metadata.py` | Labels, instance types, language, physical description, and provenance projection |
| `backend/converter/wikidata/content_projection.py` | Contents, genre, and canonical-subject projection |
| `backend/converter/wikidata/person_linking.py`, `person_projection.py` | Role-safe manuscript links and authority-backed person construction |
| `backend/converter/wikidata/work_projection.py` | Work creation, labels, deduplication, and author links |
| `backend/converter/wikidata/work_candidates.py` | Source-aware MARC 500/505/NER eligibility decisions and compact evidence |
| `backend/converter/wikidata/property_mapping.py` | P/Q constants, WPM role map, discouraged P31 set, condition/fragment vocabulary, verified KNOWN_WORK_QIDS (Rules W-26/W-71/W-98/W-114) |
| `docs/wikidata-manuscripts-data-model.md` | Code contract from WikiProject Manuscripts + DS paper |
| `backend/converter/wikidata/property_labels.py` | Verified human labels for static QIDs rendered into review/evaluator statements |
| `backend/converter/wikidata/item_validator.py` | The moat layer: ~20 checks, ERROR-severity issues block approval and writes |
| `backend/converter/wikidata/reconciler.py` | SPARQL dedup lookups; `ReconciliationUnavailableError` fail-closed contract |
| `backend/converter/wikidata/quickstatements.py` | QuickStatements v2 exporter (gated download runs `_prepare_for_upload` first — R10) |
| `backend/app/routers/wikidata_studio.py` | All endpoints: build, gated QS, reconcile, upload, push, export/import, diagnostic CSV, overrides, compare, AI-verify |
| `backend/app/routers/wikidata_labels.py` | Batch `GET /wikidata/labels` — lazy live label lookup, 3-tier cached (process dict → `inference_cache` kind `wikidata.label`) |
| `backend/app/models/wikidata_studio_cache.py` | `WikidataStudioCache` — one row per `(run_id, approved_only)`, fingerprint-keyed build result |
| `backend/app/models/item_override.py` | `WikidataItemOverride` — curator diff + `approved` + `ai_verdict`/`ai_verdict_at` |
| `frontend/src/routes/WikidataStudio.tsx` | Studio page: modern review table (default) + legacy sidebar/table toggle; `useProjectEvents` → job-tray live progress |
| `frontend/src/components/wikidata/WikidataItemsPanel.tsx` | Orchestrator: lifecycle bar, upload hub, review table, drawer, verify modal; **Approve all visible** → `wikidata_item_bulk_approve` job |
| `backend/app/pipeline/studio_item_bulk_approve.py` | Shared HMO/Wikidata bulk-approve core (versioned overrides) |
| `backend/app/pipeline/studio_item_bulk_approve_job.py` | Background worker for Studio bulk approve |
| `frontend/src/components/wikidata/WikidataItemTable.tsx` | HMO-parity review table (filters, badges, pagination) |
| `frontend/src/components/wikidata/WikidataItemDetailDrawer.tsx` | Per-item drawer: overrides, compare, reconcile, verify/autofix/push |
| `frontend/src/components/wikidata/WikidataUploadPanel.tsx` | Upload hub: dry_run/test/live radios (default dry-run), pre/post AI verify |
| `frontend/src/components/shared/UploadOutcomeBadge.tsx` | Shared upload-outcome pill (HMO + Wikidata) |
| `frontend/src/components/wikidata/` | Also: `ItemValidatorBadge`, `ItemApprovalBadge`, `WikidataComparePanel`, `WikidataVerificationModal`, data-status + AI verdict badges |
| `frontend/src/api/wikidataStudio.ts` | Typed API client; `STUDIO_MAX_PAGE_SIZE` (500); `fetchAllStudioItems` paginates bulk loads |
| `frontend/src/utils/wikidataItemDataStatus.ts` | `new` / `will_update` / `updated` posture helper |
