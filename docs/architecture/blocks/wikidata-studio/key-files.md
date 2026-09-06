# Wikidata Studio — Key files

> Up: [Wikidata Studio](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/wikidata_studio.py` | Glue: DB rows → desktop builder input; fingerprint; override application; item serialisation + label enrichment; `local_id_for_item` |
| `backend/app/pipeline/hmo_canonical_wikidata.py` | HMO Wikibase read-back → native Wikidata item adapter; CN-indexed summarized-node rollup onto manuscript/person/work (Rule W-117); `PUBLIC_WIKIDATA_ENTITY_TYPES` gate; `filter_public_wikidata_items` / stale-cache detection (W-118); label hygiene + work evidence (W-120); MARC 245 / known-QID work recovery (W-121); approved author recovery for canonical works (W-201); safe single-representation work author normalization (W-204); role-specific work-author sanitation with approved NER context (W-206 / W-209); browseable P2888/P973 Item:Q bridge (W-122); recover trusted person IDs before W-154 omit (W-188); coalesce same-identity persons before export (W-213) |
| `backend/converter/wikidata/property_mapping.py` | `resolve_hmo_bridge_url` / `is_browseable_hmo_wikibase_url` — fail-closed HMO Wikibase link helpers (W-122); known-work / Esther aliases (W-171); `format_wikidata_time` ±YYYY padding (W-193) |
| `backend/converter/wikidata/catalog_notes.py` | Shared filter for NLI workflow + scholarly catalog notes that must never become P1684 / P1922 (W-72 / W-122 / W-171 / W-172) |
| `backend/converter/wikidata/isbd_title.py` | ISBD `245` title/subtitle split (Rule W-171) |
| `backend/converter/wikidata/work_link_specificity.py` | P1574 specificity ladder — megillah vs Bible/Tanakh; RELATED_WORKS alias must not defeat piyyut block (W-171 / W-173) |
| `backend/converter/wikidata/hmo_wikidata_pq_mapper.py` | Project Wikibase / ontology → public Wikidata P/Q allowlist (Rule W-100); P1476/P1680/P1684 as monolingualtext (W-196) |
| `backend/app/pipeline/hmo_wikidata_projection.py` | Fail-closed adapter accepting only exact HMO manuscript URIs and unambiguous local QIDs for Wikidata projection |
| `backend/app/pipeline/wikidata_item_views.py` | Merged read model: cache + overrides + ledger QID + upload audit + cached QID adoption + live value-label gloss + stale-sanitized AI verdict; only probe-adopted QIDs enter the stable verdict projection (W-131 / W-169 / W-205) |
| `backend/app/pipeline/marc_verify_context.py` | `load_run_control_numbers`, `load_run_marc_records_scoped` (W-132) |
| `backend/app/pipeline/wikidata_verify_fixture.py` | Lean eval-agent fixture + `release_wikidata_verify_heap` (W-131/W-132) |
| `backend/app/pipeline/wikidata_item_merge.py` | Apply curator overrides onto serialised item dicts (carries `ai_verdict` for sticky-full, W-171) |
| `backend/app/pipeline/wikidata_item_verify.py` | Persist AI verdicts to `WikidataItemOverride` after verify streams |
| `backend/app/pipeline/wikidata_duplicate_probe.py` | Batched Action-API duplicate check; linked-QID classification; work `inlabel`+P31 allowlist probe (W-139 / W-190 / W-196 / W-199) |
| `backend/app/pipeline/wikidata_duplicate_confirm.py` | Fail-closed DeepSeek confirm (W-195); work allowlisted P31 may keep `same_item` for skip+`link_qid` (W-196) |
| `backend/app/pipeline/wikidata_local_refs.py` | Resolves `__LOCAL:` targets; drops orphan bare-QID P3342; drops redundant Q234460 (Rules W-138 / W-170 / W-171) |
| `backend/app/pipeline/wikidata_live_native_hygiene.py` | W-194 / W-196: clear false live QIDs; coerce monolingualtext; omit implausible P2048/P2049; rewrite `__LOCAL:` |
| `backend/app/pipeline/wikidata_verify_evidence.py` | Multi-source evidence packs: MARC slice, per-claim `claim_sources` provenance, offline `value_labels`, VIAF/Mazal/HMO channels |
| `backend/app/pipeline/wikidata_verdict_cache.py` | Verdict fingerprints, claims-fingerprint sticky-full helpers, volatile-metadata stripping, legacy judge-failure normalization, schema `w175_v1` (W-171…W-175 / W-207 / W-208) |
| `backend/app/migrations/versions/0038_unknown_judge_failures.py` | Historical migration that converted provider-failure verdicts to `unknown` rows (superseded by W-211) |
| `backend/app/migrations/versions/0039_public_abstain_provider_errors.py` | Convert legacy `unknown` / `verification_failed` rows to `abstain` with provider-error status and a marked, reversible migration (W-211) |
| `backend/app/pipeline/wikidata_verify_scope.py` | Shared verify cache partitioner + sticky-full (W-167 / W-171) |
| `backend/app/pipeline/wikidata_canonical_enrichment.py` | Merge legacy MARC/authority claims onto canonical Studio items (Rule W-125); recover trusted person IDs before the W-154 omit gate (W-188); coalesce connected same-identity person drafts and rewrite local references (W-213); normalize duplicate work author claims (W-204) |
| `backend/app/pipeline/wikidata_qid_ledger.py` | Global `wikibase_entity_mappings` ledger (`wikidata:` keys) — adopt + idempotent upload |
| `backend/app/pipeline/wikidata_export_quality_gate.py` | Build-time ERROR-only export quality gate before cache upsert; source-backed works must retain one safe P50 or P2093 representation |
| `backend/app/publication/core.py` | Deep module with `prepare`, `advance`, and `read`; seals Release, Approval Set, Plan, Receipt, Execution, and write journal without a transaction across remote I/O |
| `backend/app/publication/sql_repository.py` | Normalized persistence, keyset reads, collision Findings, and leased Execution Actions for Publication state |
| `backend/app/publication/runtime.py` | Studio source adapter; resolves known local targets, retains explicit deferred claims, and reports `source_current` |
| `backend/app/publication/gateway.py` | The only Publication seam for reconciliation, mutation compilation, write, and ambiguous-result recovery |
| `backend/app/publication/wikidata_gateway.py`, `credentials.py` | Production Action API gateway and the target-bound server credential factory. No HTTP route or job payload receives a plaintext token. |
| `backend/app/publication/repository.py`, `types.py`, `digests.py`, `testing.py` | Publication repository protocol, immutable domain types, canonical digest helpers, and deterministic test seams |
| `backend/app/models/publication.py` | ORM rows for immutable Releases, approvals, Plans, Execution Actions, Write Intents, Receipts, and audit journal |
| `backend/app/migrations/versions/0040_publication_core.py` | Creates normalized durable Publication state; identity assertions remain non-unique so collisions become Findings |
| `backend/app/schemas/publication.py`, `backend/app/routers/publication.py` | Typed run-scoped Publication HTTP commands and cursor reads; editor writes and viewer reads use run access checks |
| `backend/converter/transformer/extent.py` | Single MARC 300$a extent parser — sums leaf sequences, reads page/gematria units, fails closed (Rule W-140) |
| `backend/app/pipeline/marc_llm_extract.py` | Span-grounded tier-1 extraction of owner/place/material from MARC provenance prose; proposals only (Rule W-140) |
| `backend/scripts/check_wikidata_export_quality.py` | Read-only compact audit of work identity, author, language, quote, validation, linked-QID handling, and export-field failures (W-199) |
| `backend/scripts/audit_test_wikidata_upload.py` | Per-entity test.wikidata.org write vs Studio native live-readiness (validator ERROR, claim count, live URI leak, W-190 identity clash); identifierless persons `skip_for_live` (W-195) |
| `backend/scripts/judge_test_wikidata_live_ready.py` | LLM live-readiness judge: deterministic audit + eval-agent `wikidata_test_live_ready` on natives (never copy test Q/P); `skip_for_live` excluded from written denominator (W-195) |
| `backend/app/pipeline/wikidata_studio_build_job.py` | Background build job (`wikidata_studio_build` kind) — `reconcile=False`, threadpool canonical build (W-119) |
| `backend/app/pipeline/wikidata_upload.py` | `resolve_upload_mode` / `upload_target`, `_prepare_for_upload`, person identity gate (W-190), work skip+`link_qid` (W-196), clash-cleared identifierless skip + uncertain-duplicate confirm + own-only pass 2 (W-195), `UploadOutcome` |
| `backend/converter/wikidata/uploader.py` | Real `WikidataUploader` — Rule-38 guards + `allow_live` / moratorium; `is_bot` via `mark_as_bot` (default false, W-181); test-wiki remap; leftover snaks refuse the write; `partition_unresolved_local` / session `created_qids` (W-191 / W-192); quantity/time exists-match (W-193); adopt-on-conflict + datatype-keyed maps + MHM stub reuse + holder/live gloss stubs + item-write adopt (W-182 / W-183 / W-186 / W-187 / W-189) |
| `backend/converter/wikidata/test_wiki_compat.py` | Remap live P/Q to test entities by label+datatype; `(live_pid, value_type)` map keys; conflict-id parse; holder/live gloss; leftover list for W-186 refuse (W-189) |
| `backend/app/routers/wikidata_studio.py` (`_build_native_items`, `studio_dict_to_native_item`) | Upload natives from Studio cache (W-181); cache-miss rebuild fallback |
| `backend/app/pipeline/wikidata_existence.py` | Action API `wbgetentities` alive check + labels + ownership classify + QID-bound foreign accept |
| `docs/wikidata-data-access.md` | Wikidata:Data_access API map + MHM write-policy matrix (Rule W-99) |
| `backend/app/pipeline/wikidata_upload_job.py` | Two-pass upload job: partition `__LOCAL:`, write items, MERGE connections on created/own QIDs only; leftover P50→P2093 (W-191 / W-192 / W-195) |
| `backend/app/pipeline/wikidata_actions.py` | Prefab AI-agent actions (`audit_wikidata_item`, `autofix_from_wikidata`) for the eval-agent verify modal |
| `backend/app/pipeline/wikidata_autofix_apply.py` | Merge high-confidence AI fixes into override PATCH fragments |
| `backend/app/pipeline/wikidata_entity_compare.py` | Fetch live Wikidata entity + build Studio-vs-live diff rows |
| `backend/app/pipeline/wikidata_live_enrich.py` | Attach `wikidata_live` compare snapshots to items that carry a QID (autofix input) |
| `backend/app/pipeline/work_title_match.py` | Work-title normalization / variants for Mazal work matching |
| `backend/converter/wikidata/item_builder.py` | Public compatibility facade: orchestration, reconciliation, and legacy exports only; shared work-title attribution parsing (W-206) |
| `backend/converter/wikidata/item_models.py` | Shared `WikidataItem` / `WikidataStatement` data structures |
| `backend/converter/wikidata/manuscript_projection.py` | Manuscript identity, catalog, digital-access, note, and evidence-gated related_works → P1574 (Rule W-114) |
| `backend/converter/wikidata/manuscript_metadata.py` | Labels, instance types, language, physical description, and provenance projection |
| `backend/converter/wikidata/content_projection.py` | Contents, genre, canonical-subject projection, exact work-QID relinking, approved author recovery from MARC matches, and primary-author precedence (W-198 / W-200 / W-202 / W-206) |
| `backend/converter/wikidata/semantic_policy.py`, `work_author_claims.py` | Generic source-evidence policy and safe work-author claim selection before the export gate |
| `backend/converter/wikidata/person_linking.py`, `person_projection.py`, `role_normalize.py` | Role-safe manuscript links (paren/quote MARC relators), authority-backed person construction, and explicit person-language claims (W-206) |
| `backend/converter/wikidata/work_projection.py` | Work creation, author-aware deduplication, labels, and source-backed author links (W-198 / W-200) |
| `backend/converter/wikidata/work_candidates.py` | Source-aware MARC 500/505/NER eligibility decisions and compact evidence |
| `backend/converter/wikidata/property_mapping.py` | P/Q constants, WPM role map, discouraged P31 set, condition/fragment vocabulary, verified KNOWN_WORK_QIDS (Rules W-26/W-71/W-98/W-114) |
| `docs/wikidata-manuscripts-data-model.md` | Code contract from WikiProject Manuscripts + DS paper |
| `backend/converter/wikidata/property_labels.py` | Verified human labels for static QIDs and projected PIDs (review/evaluator gloss + test remap search/CREATE, including P1680 subtitle) |
| `backend/converter/wikidata/item_validator.py` | The moat layer: ~20 checks, ERROR-severity issues block approval and writes |
| `backend/converter/wikidata/reconciler.py` | SPARQL dedup lookups; `ReconciliationUnavailableError` fail-closed contract |
| `backend/converter/wikidata/quickstatements.py` | QuickStatements v2 exporter (gated download runs `_prepare_for_upload` first — R10) |
| `backend/app/routers/wikidata_studio.py` | All endpoints: build, gated QS, reconcile, upload, push, export/import, diagnostic CSV, overrides, compare, AI-verify; isolated bulk transliteration cache prewarm (W-197); `_apply_cached_qid_adoption_to_native` (W-177) |
| `backend/app/routers/wikidata_labels.py` | Batch `GET /wikidata/labels` — lazy live label lookup, 3-tier cached (process dict → `inference_cache` kind `wikidata.label`) |
| `backend/app/models/wikidata_studio_cache.py` | `WikidataStudioCache` — one row per `(run_id, approved_only)`, fingerprint-keyed build result |
| `backend/app/models/item_override.py` | `WikidataItemOverride` — curator diff + `approved` + `ai_verdict`/`ai_verdict_at` |
| `frontend/src/routes/WikidataStudio.tsx` | Studio page: modern review table (default) + legacy sidebar/table toggle; `useProjectEvents` → job-tray live progress |
| `frontend/src/components/wikidata/WikidataItemsPanel.tsx` | Orchestrator: lifecycle bar, Publication-first target controls, compatibility upload hub after API unavailability, review table, drawer, verify + upload progress modals; **Approve all visible** → `wikidata_item_bulk_approve` job |
| `backend/app/pipeline/studio_item_bulk_approve.py` | Shared HMO/Wikidata bulk-approve core (versioned overrides) |
| `backend/app/pipeline/studio_item_bulk_approve_job.py` | Background worker for Studio bulk approve |
| `frontend/src/components/wikidata/WikidataItemTable.tsx` | HMO-parity review table (filters, badges, pagination) |
| `frontend/src/components/wikidata/WikidataItemDetailDrawer.tsx` | Per-item drawer: overrides, compare, reconcile, verify/autofix/push |
| `frontend/src/components/wikidata/WikidataUploadPanel.tsx` | Compatibility upload hub: dry-run/test radios only, pre/post AI verify; opens upload progress modal on start; inline two-step bars (same `WikidataUploadSteps` as test/live modal) |
| `frontend/src/components/wikidata/WikidataUploadProgressModal.tsx` | Upload progress modal (tray View / Rule W-141): two-step bars + Now/ETA (W-192), target-aware title + sticky badge, counts strip, per-item table, cancel |
| `frontend/src/utils/wikidataUploadOutcomes.tsx` | Shared upload outcome tally/table helpers (panel summary + progress modal) |
| `frontend/src/components/shared/UploadOutcomeBadge.tsx` | Shared upload-outcome pill (HMO + Wikidata) |
| `frontend/src/components/wikidata/` | Also: `ItemValidatorBadge`, `ItemApprovalBadge`, `WikidataComparePanel`, `WikidataVerificationModal`, data-status + AI verdict badges |
| `frontend/src/api/wikidataStudio.ts` | Typed API client; `STUDIO_MAX_PAGE_SIZE` (500); `fetchAllStudioItems` paginates bulk loads |
| `frontend/src/api/publication.ts` | Typed client for Release prepare, commands, cursor reads, and audit |
| `frontend/src/components/wikidata/WikidataPublicationPanel.tsx` | The only default target selector for test/live Release preparation, plus Release controls and a 50-row Publication entity page; enables the compatibility panel only after 404/405/410 |
| `frontend/src/components/wikidata/WikidataPublicationControls.tsx` | Hard UI gates for Review, Dry-run Receipt, Publish, Resume, and Cancel |
| `frontend/src/routes/WikidataPublicationAudit.tsx` | Cursor audit route for immutable Publication evidence |
| `frontend/src/utils/wikidataItemDataStatus.ts` | `new` / `will_update` / `updated` posture helper |

`WikidataItemsPanel.tsx` loads all compact API pages through `fetchAllStudioItems` (W-215).
`frontend/e2e/wikidata-complete-scope.spec.ts` tests complete filter and action scopes.

`backend/tests/test_publication_postgres_order.py` checks PostgreSQL source order and duplicate rejection (W-216).

[Existing items without updates](../wikidata-studio/reference-only.md) lists the prepare contract, projection, executor, UI, and tests.

[Automatic resolution](../wikidata-studio/automatic-resolution.md) lists the worker, evidence cache, structured verdict, subset, and tests.
