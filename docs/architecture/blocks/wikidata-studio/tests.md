# Wikidata Studio — Tests pinning this block

> Up: [Wikidata Studio](README.md)

- `backend/tests/unit/test_wikidata_upload_guards.py` (~17) — reconcile-before-create per type, fail-closed outage, validator hard gate, blocked-never-written, dry-run truthfulness, cross-identifier conflict rejection.
- `backend/tests/unit/test_item_validator.py` — every check incl. `TestP50OnManuscript`, `TestP7416AsQuantity`, `TestP31WrongQid`, `TestBuilderNeverViolatesNewChecks`.
- `backend/tests/unit/test_wikidata_studio_slicing.py`, `test_wikidata_studio_works.py`, `test_wikidata_manuscript_labels.py`, `test_wikidata_matcher_backfill.py` — build/serialisation behaviour.
- `backend/tests/unit/test_wikidata_autofix_apply.py`, `test_wikidata_entity_compare.py` — AI-fix merge + live compare.
- `backend/tests/test_hmo_instance_qids_for_run.py` — HMO QID injection into the fingerprint/build.
- `frontend/e2e/wikidata-studio.spec.ts` (~15) — page, AI verification, `approved_item_count`, entity_type filter, sort controls.

Any new external-write path or reconcile change MUST extend
`test_wikidata_upload_guards.py` (Rule W-30).
