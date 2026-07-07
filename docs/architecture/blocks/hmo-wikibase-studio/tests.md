# HMO Wikibase Studio — Tests pinning this block

> Up: [HMO Wikibase Studio](README.md)

- `backend/tests/test_hmo_schema_bootstrap.py`, `test_hmo_schema_bootstrap_job.py`,
  `test_hmo_wikibase_schema_router.py` — bootstrap idempotency, label de-dup, job wrapper.
- `backend/tests/test_hmo_item_build.py`, `test_hmo_studio_build_items_router.py`,
  `backend/tests/unit/test_hmo_exporter_resolution.py` (incl. truncation cases) — build + cache fingerprint.
- `backend/tests/test_hmo_item_upload.py` (incl.
  `test_live_upload_resolves_reconcile_pid_once_not_per_entity`,
  `test_dry_run_never_resolves_reconcile_pid`,
  `test_live_upload_records_operation_adopt_in_audit_log`, and the
  `push_single_item` cases: creates-when-no-qid, updates-when-qid+flag,
  skips-when-qid+no-flag, records-audit-row), `test_hmo_item_upload_job.py`,
  `test_hmo_studio_upload_items_router.py` — two-pass upload, job, R2, R16, R17.
- `backend/tests/test_hmo_item_views.py` — `fetch_merged_hmo_items` surfaces
  `upload_outcome`/`upload_message`/`upload_at` from `wikibase_cloud_writes`,
  scoped per run, `None` when never attempted.
- `backend/tests/test_hmo_studio_items_push_router.py` — single-item
  `POST .../items/{local_id}/push`: create, update-existing-qid, 404 unknown
  id, 409 no build, 503 Wikibase not configured.
- `backend/tests/test_wikibase_audit.py`
  (`test_fetch_latest_wikibase_writes_returns_only_the_newest_row_per_target`,
  `test_fetch_latest_wikibase_writes_scopes_by_channel_and_target_kind`).
- `backend/tests/test_verify_job_hmo.py`,
  `backend/tests/test_run_job_params_hmo_verify.py` — `JOB_KIND_HMO_ITEM_VERIFY`
  dispatch + param validation (R18; see also job-service and eval-agent blocks).
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
  incl. the "Last upload" column (badge content + column-filter popup) and
  upload-lifecycle AI verification (checkboxes, successful/failed pre-verify,
  the fail-confirm banner's "review" vs "upload anyway" paths).
