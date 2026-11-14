# Versioning, history & export — Tests pinning this block

> Up: [Versioning, history & export](README.md)

- `backend/tests/test_versioning_core.py` — apply_event ops, replay, auto-snapshot, diff, revert
- `backend/tests/test_history_router.py` — timeline / diff / at / revert / snapshots + cross-project guards
- `backend/tests/test_versioning_integration.py` — every mutation surface emits its event
- `backend/tests/test_snapshot_prune_jobs.py` — slot snapshotting, anchor preservation, 1000-cap
- `backend/tests/test_export_router.py` — bundle / snapshots / history exports + entity_types filter
- `backend/tests/test_section_export_router.py`, `test_section_import_router.py` — per-section round-trips
- `backend/tests/test_extraction_rejection.py` — rejected import/export and legacy import compatibility
- `frontend/e2e/history-timeline.spec.ts` — timeline UI click paths

Source choices: `test_wikidata_source_review.py` checks the full proof event before the row state. `test_publication_migrations.py` checks migration through the current head. Import/export and history suites retain the existing reference-block boundaries.
