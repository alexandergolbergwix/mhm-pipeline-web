# Wikidata Studio — Tests pinning this block

> Up: [Wikidata Studio](README.md)

- `backend/tests/unit/test_item_validator.py` — every check incl. label-hygiene warnings + `TestP50OnManuscript`, `TestP7416AsQuantity`, `TestP31WrongQid`.
- `backend/tests/test_wikidata_items_export_import.py` — override export/import round-trip.
- `backend/tests/test_wikidata_item_views.py`, `test_wikidata_verdict_persistence.py`, `test_wikidata_qid_ledger.py`, `test_wikidata_single_push.py`, `test_wikidata_export_quality.py` — parity plan Phases 1–4.
- `backend/tests/unit/test_wikidata_upload_guards.py` (~17+) — reconcile-before-create per type, fail-closed outage, validator hard gate, blocked-never-written, audit trail, ledger/adopt, dry-run truthfulness.
- `backend/tests/unit/test_wikidata_studio_slicing.py`, `test_wikidata_studio_works.py`, `test_wikidata_manuscript_labels.py`, `test_wikidata_matcher_backfill.py` — build/serialisation behaviour.
- `backend/tests/unit/test_wikidata_autofix_apply.py`, `test_wikidata_entity_compare.py` — AI-fix merge + live compare.
- `backend/tests/test_hmo_instance_qids_for_run.py` — HMO QID injection into the fingerprint/build.
- `frontend/e2e/wikidata-studio.spec.ts` — page (modern + legacy), AI verification, filters, sort, approval, force-rebuild.
- `frontend/e2e/wikidata-item-table.spec.ts` — review table columns, data status, search, approval PATCH, upload-outcome filter.
- `frontend/e2e/wikidata-item-drawer.spec.ts` — drawer apply-fix, push, reconcile API shapes.
- `frontend/e2e/wikidata-upload-panel.spec.ts` — upload controls, moratorium pill, pre-verify fail confirm gate.
- `frontend/tests/unit/useVerifyJob.spec.ts` — verify jobs upsert into the global job tray on start.

Any new external-write path or reconcile change MUST extend
`test_wikidata_upload_guards.py` (Rule W-30).
