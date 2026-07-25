# HMO Wikibase Studio — Tests pinning this block

> Up: [HMO Wikibase Studio](README.md)

- `backend/tests/unit/test_hmo_ontology_graphbuilder_parity.py` — GraphBuilder
  `hm:` terms ⊆ ontology; mapper ↔ `owl:equivalentProperty` sync (W-102).
- `backend/tests/unit/test_hmo_export_quality.py` — export quality and
  fail-closed authority gates (per-entity conflicts plus global duplicate
  Wikidata/VIAF/KIMA/Mazal identifiers), incl. Rule W-52 codes (`production_missing_label`, `timespan_bare_label`,
  `latin_label_in_he`, `unbalanced_label_quotes`, `witness_unusable_title`)
- `backend/tests/unit/test_hmo_exporter_resolution.py` /
  `test_hmo_exporter_descriptions.py` — no Hebrew-in-`en`, comment→`en`
  normalization, sentence dedup
- `backend/tests/unit/test_ontology_schema_reader.py` — full-ontology walk,
  external-vocab properties, datatype inference (`hmo_source_uri` → `url`,
  `book_name` → `monolingualtext`, OWL kind/range metadata).
- `backend/tests/unit/test_hmo_schema_verify.py` — fixture enrichment +
  cache-key fields (`description`, `property_kind`, `range_uri`).
- `backend/tests/unit/test_hmo_exporter_resolution.py` (incl. truncation cases),
  `backend/tests/unit/test_hmo_exporter_control_numbers.py` — build + CN graph walk.
- `backend/tests/test_hmo_item_build.py`, `test_hmo_studio_build_items_router.py`,
  `test_hmo_studio_ttl_restore.py` — build + cache fingerprint; job enqueue /
  worker (W-106); authority-refresh forces RDF rebuild (W-101).
- `backend/tests/test_hmo_studio_upload_manifests_router.py`,
  `test_hmo_studio_upload_items_router.py`, `test_hmo_item_upload_job.py` —
  publish jobs (W-107); per-item progress outcomes for mid-run table patches
  (W-110).
- `backend/tests/unit/test_hmo_authority_gate.py`,
  `test_hmo_authority_conflicts_router.py` — conflict report + keep-one
  resolve (W-109).
- `backend/tests/unit/test_kima_disambiguate.py`, `test_place_coords_in_rdf.py` —
  multi-source place/person enrichment claims.
- `backend/tests/test_hmo_item_upload.py` (incl.
  `test_unsupported_boolean_claims_are_serialized_before_live_write`,
  `test_url_claims_strip_marc_quote_wrappers_before_wbi` (W-111),
  `test_live_upload_resolves_reconcile_pid_once_not_per_entity`,
  `test_dry_run_never_resolves_reconcile_pid`,
  `test_live_upload_records_operation_adopt_in_audit_log`,
  `test_shacl_violation_blocks_live_create`,
  `test_allow_shacl_errors_bypasses_upload_gate`, and the
  `push_single_item` cases), `test_hmo_item_upload_job.py`,
  `test_hmo_studio_upload_items_router.py` — two-pass upload, job, R2, R16, R17, R19.
- `backend/tests/test_hmo_item_views.py` — `fetch_validation_error_items`,
  `has_blocking_shacl` on merged items.
- `backend/tests/unit/test_hmo_item_shacl_gate.py` — blocking severity filter,
  `und` label sanitization.
- `backend/tests/test_hmo_studio_items_push_router.py` — single-item
  `POST .../items/{local_id}/push`: create, update-existing-qid, 404 unknown
  id, 409 no build, 503 Wikibase not configured.
- `backend/tests/test_wikibase_audit.py`
  (`test_fetch_latest_wikibase_writes_returns_only_the_newest_row_per_target`,
  `test_fetch_latest_wikibase_writes_scopes_by_channel_and_target_kind`).
- `backend/tests/test_verify_job_hmo.py`,
  `backend/tests/test_run_job_params_hmo_verify.py`,
  `backend/tests/unit/test_verify_job_progress.py` — `JOB_KIND_HMO_ITEM_VERIFY`
  dispatch + param validation + live `progress.session_snapshot` (R18; see
  also job-service and eval-agent blocks).
- `backend/tests/unit/test_hmo_item_reconcile.py`
  (`test_pid_lookup_transaction_is_closed_before_the_sparql_call`) and
  `test_hmo_studio_items_reconcile_router.py` — fail-closed reconcile.
- `backend/tests/unit/test_hmo_item_shacl.py`, `test_hmo_item_merge.py`,
  `test_hmo_studio_item_override.py` — SHACL bucketing + override merge.
- `backend/tests/test_hmo_studio_coverage_router.py`
  (`test_coverage_restores_from_durable_db_cache_on_disk_miss`) and
  `test_hmo_studio_ttl_restore.py` — Rule W-39.
- `backend/tests/unit/test_wikibase_cloud_client_entities.py`,
  `test_wikibase_cloud_oauth.py`, `test_wikibase_create_account.py`,
  `backend/tests/test_wikibase_credentials.py`, `test_wikibase_audit.py`,
  `test_wikibase_user_access.py` — writer, OAuth gate, audit, provisioning.
- `backend/tests/unit/test_hmo_schema_verify.py` — cached-verdict filtering for
  the eval-agent fixture.
- `frontend/e2e/hmo-wikibase-items.spec.ts` — items review UI click paths,
  incl. the merged single-page layout (lifecycle bar with rebuild/reupload
  controls always above the table, data status column),
  the "Last upload" column (badge content + column-filter popup) and
  upload-lifecycle AI verification (checkboxes, successful/failed pre-verify,
  the fail-confirm banner's "review" vs "upload anyway" paths).
  Retry-failed button posts `local_ids` for last-push failures.
  Live upload progress reloads the items table mid-job; Publication status
  shows `failed` when `upload_outcome` is failed.
- `frontend/e2e/hmo-wikibase-studio.spec.ts` — Phase 7 acceptance coverage:
  schema class/property counts, dry-run bootstrap defaults, item-build gating,
  item-upload request payload/result summary, and the advanced schema disclosure.
- `frontend/e2e/hmo-wikibase-items.spec.ts` — review-table filters (real value
  counts), Approve all visible (`hmo_item_bulk_approve` job), tri-state editorial decisions, publish
  confirmation, technical-column disclosure, and upload-lifecycle verification
  paths.
- `backend/tests/test_studio_item_bulk_approve.py` — bulk-approve params + worker.

- `backend/tests/test_hmo_studio_ttl_restore.py` — TTL restore for status/build-manifests/build-items; IIIF build ignores authority conflicts
- `backend/tests/unit/test_hmo_authority_gate.py` — approved identifier conflicts and NLI/VIAF misclassification fail closed before HMO **item** creation.

- `backend/tests/unit/test_hmo_canonical_wikidata.py::test_full_canonical_chain_has_no_legacy_authority_dependency` — one enriched HMO entity projects consistently to RDF and Wikidata/QuickStatements.

- `backend/tests/unit/test_canonical_rollout.py` — explicit run, deterministic
  percentage, and global canonical-first cohorts.

- `backend/tests/unit/test_hmo_canonical_readback.py` — nested MediaWiki JSON normalization and source-URI/QID read-back contract (Rule W-90)
- `backend/tests/unit/test_projection_shadow_compare.py` — RDF and structured
  item shadow comparisons, difference classifications, and fail-closed
  promotion readiness.
- `backend/tests/unit/test_hmo_canonical_readiness_contract.py` — shared
  readiness integrity fields and fail-closed promotion behavior.
- `backend/tests/test_hmo_item_upload.py` — authority-gated creation,
  all-or-nothing read-back, reconciliation, duplicate live-identity guards,
  and canonical persistence only after successful deferred-link writes.
- `backend/tests/unit/test_run_hmo_phase8.py` — Phase 8 coordinator safety
  gates and fail-fast test-sweep behavior.
- `backend/tests/unit/test_hmo_ontology_mirror.py` — schema label/datatype
  drift reporting (R40).
- `backend/tests/unit/test_hmo_exporter_resolution.py` — deterministic typed
  blank-node IDs and cross-run-capable deferred target serialization (R42/R43).
