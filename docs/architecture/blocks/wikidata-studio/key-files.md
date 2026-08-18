# Wikidata Studio — Key files

> Up: [Wikidata Studio](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/wikidata_studio.py` | Glue: DB rows → desktop builder input; fingerprint; override application; item serialisation + label enrichment; `local_id_for_item` |
| `backend/app/pipeline/hmo_canonical_wikidata.py` | HMO Wikibase read-back → native Wikidata item adapter; CN-indexed summarized-node rollup onto manuscript/person/work (Rule W-117); `PUBLIC_WIKIDATA_ENTITY_TYPES` gate; `filter_public_wikidata_items` / stale-cache detection (W-118); label hygiene + work evidence (W-120); MARC 245 / known-QID work recovery (W-121); browseable P2888/P973 Item:Q bridge (W-122) |
| `backend/converter/wikidata/property_mapping.py` | `resolve_hmo_bridge_url` / `is_browseable_hmo_wikibase_url` — fail-closed HMO Wikibase link helpers (W-122); known-work / Esther aliases (W-171) |
| `backend/converter/wikidata/catalog_notes.py` | Shared filter for NLI workflow + scholarly catalog notes that must never become P1684 / P1922 (W-72 / W-122 / W-171 / W-172) |
| `backend/converter/wikidata/isbd_title.py` | ISBD `245` title/subtitle split (Rule W-171) |
| `backend/converter/wikidata/work_link_specificity.py` | P1574 specificity ladder — megillah vs Bible/Tanakh; RELATED_WORKS alias must not defeat piyyut block (W-171 / W-173) |
| `backend/converter/wikidata/hmo_wikidata_pq_mapper.py` | Project Wikibase / ontology → public Wikidata P/Q allowlist (Rule W-100); never ID-identity |
| `backend/app/pipeline/hmo_wikidata_projection.py` | Fail-closed adapter accepting only exact HMO manuscript URIs and unambiguous local QIDs for Wikidata projection |
| `backend/app/pipeline/wikidata_item_views.py` | Merged read model: cache + overrides + ledger QID + upload audit + cached QID adoption + live value-label gloss + stale-sanitized AI verdict; `trim_studio_list_item` / `fetch_merged_wikidata_item` (W-131 / W-169) |
| `backend/app/pipeline/marc_verify_context.py` | `load_run_control_numbers`, `load_run_marc_records_scoped` (W-132) |
| `backend/app/pipeline/wikidata_verify_fixture.py` | Lean eval-agent fixture + `release_wikidata_verify_heap` (W-131/W-132) |
| `backend/app/pipeline/wikidata_item_merge.py` | Apply curator overrides onto serialised item dicts (carries `ai_verdict` for sticky-full, W-171) |
| `backend/app/pipeline/wikidata_item_verify.py` | Persist AI verdicts to `WikidataItemOverride` after verify streams |
| `backend/app/pipeline/wikidata_duplicate_probe.py` | Batched Action-API duplicate check for CREATE candidates; fail-closed statuses (Rule W-139) |
| `backend/app/pipeline/wikidata_local_refs.py` | Resolves `__LOCAL:` targets; drops orphan bare-QID P3342; drops redundant Q234460 (Rules W-138 / W-170 / W-171) |
| `backend/app/pipeline/wikidata_verify_evidence.py` | Multi-source evidence packs: MARC slice, per-claim `claim_sources` provenance, offline `value_labels`, VIAF/Mazal/HMO channels |
| `backend/app/pipeline/wikidata_verdict_cache.py` | Verdict fingerprints, claims-fingerprint sticky-full helpers, schema `w175_v1` (W-171…W-175) |
| `backend/app/pipeline/wikidata_verify_scope.py` | Shared verify cache partitioner + sticky-full (W-167 / W-171) |
| `backend/app/pipeline/wikidata_canonical_enrichment.py` | Merge legacy MARC/authority claims onto canonical Studio items (Rule W-125) |
| `backend/app/pipeline/wikidata_qid_ledger.py` | Global `wikibase_entity_mappings` ledger (`wikidata:` keys) — adopt + idempotent upload |
| `backend/app/pipeline/wikidata_export_quality_gate.py` | Build-time ERROR-only export quality gate before cache upsert |
| `backend/converter/transformer/extent.py` | Single MARC 300$a extent parser — sums leaf sequences, reads page/gematria units, fails closed (Rule W-140) |
| `backend/app/pipeline/marc_llm_extract.py` | Span-grounded tier-1 extraction of owner/place/material from MARC provenance prose; proposals only (Rule W-140) |
| `backend/scripts/check_wikidata_export_quality.py` | Read-only compact audit of work identity, author, language, quote, validation, and export-field failures |
| `backend/app/pipeline/wikidata_studio_build_job.py` | Background build job (`wikidata_studio_build` kind) — `reconcile=False`, threadpool canonical build (W-119) |
| `backend/app/pipeline/wikidata_upload.py` | `resolve_upload_mode` / `upload_target`, `_prepare_for_upload`, foreign-accept map, `UploadOutcome` |
| `backend/converter/wikidata/uploader.py` | Real `WikidataUploader` — Rule-38 guards + `allow_live` / moratorium; `is_bot` via `mark_as_bot` (default false, W-181); test-wiki remap + leftover strip (W-182 / W-183) |
| `backend/converter/wikidata/test_wiki_compat.py` | Remap live P/Q to test entities by label+datatype; drop leftover snaks (W-182 / W-183) |
| `backend/app/routers/wikidata_studio.py` (`_build_native_items`, `studio_dict_to_native_item`) | Upload natives from Studio cache (W-181); cache-miss rebuild fallback |
| `backend/app/pipeline/wikidata_existence.py` | Action API `wbgetentities` alive check + ownership classify + QID-bound foreign accept |
| `docs/wikidata-data-access.md` | Wikidata:Data_access API map + MHM write-policy matrix (Rule W-99) |
| `backend/app/pipeline/wikidata_upload_job.py` | Background upload/dry-run job (`wikidata_upload` kind), item-by-item progress (`label`, `entity_type`, `outcome_counts`) + cancel |
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
| `backend/converter/wikidata/person_linking.py`, `person_projection.py`, `role_normalize.py` | Role-safe manuscript links (paren/quote MARC relators), authority-backed person construction |
| `backend/converter/wikidata/work_projection.py` | Work creation, labels, deduplication, and author links |
| `backend/converter/wikidata/work_candidates.py` | Source-aware MARC 500/505/NER eligibility decisions and compact evidence |
| `backend/converter/wikidata/property_mapping.py` | P/Q constants, WPM role map, discouraged P31 set, condition/fragment vocabulary, verified KNOWN_WORK_QIDS (Rules W-26/W-71/W-98/W-114) |
| `docs/wikidata-manuscripts-data-model.md` | Code contract from WikiProject Manuscripts + DS paper |
| `backend/converter/wikidata/property_labels.py` | Verified human labels for static QIDs rendered into review/evaluator statements |
| `backend/converter/wikidata/item_validator.py` | The moat layer: ~20 checks, ERROR-severity issues block approval and writes |
| `backend/converter/wikidata/reconciler.py` | SPARQL dedup lookups; `ReconciliationUnavailableError` fail-closed contract |
| `backend/converter/wikidata/quickstatements.py` | QuickStatements v2 exporter (gated download runs `_prepare_for_upload` first — R10) |
| `backend/app/routers/wikidata_studio.py` | All endpoints: build, gated QS, reconcile, upload, push, export/import, diagnostic CSV, overrides, compare, AI-verify; `_apply_cached_qid_adoption_to_native` (W-177) |
| `backend/app/routers/wikidata_labels.py` | Batch `GET /wikidata/labels` — lazy live label lookup, 3-tier cached (process dict → `inference_cache` kind `wikidata.label`) |
| `backend/app/models/wikidata_studio_cache.py` | `WikidataStudioCache` — one row per `(run_id, approved_only)`, fingerprint-keyed build result |
| `backend/app/models/item_override.py` | `WikidataItemOverride` — curator diff + `approved` + `ai_verdict`/`ai_verdict_at` |
| `frontend/src/routes/WikidataStudio.tsx` | Studio page: modern review table (default) + legacy sidebar/table toggle; `useProjectEvents` → job-tray live progress |
| `frontend/src/components/wikidata/WikidataItemsPanel.tsx` | Orchestrator: lifecycle bar, upload hub, review table, drawer, verify + upload progress modals; **Approve all visible** → `wikidata_item_bulk_approve` job |
| `backend/app/pipeline/studio_item_bulk_approve.py` | Shared HMO/Wikidata bulk-approve core (versioned overrides) |
| `backend/app/pipeline/studio_item_bulk_approve_job.py` | Background worker for Studio bulk approve |
| `frontend/src/components/wikidata/WikidataItemTable.tsx` | HMO-parity review table (filters, badges, pagination) |
| `frontend/src/components/wikidata/WikidataItemDetailDrawer.tsx` | Per-item drawer: overrides, compare, reconcile, verify/autofix/push |
| `frontend/src/components/wikidata/WikidataUploadPanel.tsx` | Upload hub: dry_run/test/live radios (default dry-run), pre/post AI verify; opens upload progress modal on start |
| `frontend/src/components/wikidata/WikidataUploadProgressModal.tsx` | Live upload progress modal (tray View / Rule W-141): counts strip, per-item table, cancel |
| `frontend/src/utils/wikidataUploadOutcomes.tsx` | Shared upload outcome tally/table helpers (panel summary + progress modal) |
| `frontend/src/components/shared/UploadOutcomeBadge.tsx` | Shared upload-outcome pill (HMO + Wikidata) |
| `frontend/src/components/wikidata/` | Also: `ItemValidatorBadge`, `ItemApprovalBadge`, `WikidataComparePanel`, `WikidataVerificationModal`, data-status + AI verdict badges |
| `frontend/src/api/wikidataStudio.ts` | Typed API client; `STUDIO_MAX_PAGE_SIZE` (500); `fetchAllStudioItems` paginates bulk loads |
| `frontend/src/utils/wikidataItemDataStatus.ts` | `new` / `will_update` / `updated` posture helper |
