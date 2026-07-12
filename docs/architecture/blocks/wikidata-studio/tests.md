# Wikidata Studio — Tests pinning this block

> Up: [Wikidata Studio](README.md)

- `backend/tests/unit/test_item_validator.py` — every check incl. label-hygiene warnings + `TestP50OnManuscript`, `TestP7416AsQuantity`, `TestP31WrongQid`.
- `backend/tests/test_wikidata_items_export_import.py` — override export/import round-trip and diagnostic CSV coverage (item fields, MARC context, and full AI verdict).
- `backend/tests/test_wikidata_export_quality_checker.py` — compact JSON/CSV audit reports only actionable failures and distinguishes authority-approved-only from diagnostic exports.
- `backend/tests/test_section_export_router.py` — section CSV retains item approval, verdict JSON, source records, validation, authority, and work-candidate evidence.
- `backend/tests/test_wikidata_item_views.py`, `test_wikidata_verdict_persistence.py` (including build `records` ↔ verify `record_ids`, MARC-context fingerprint parity, and safe legacy-key display), `test_wikidata_qid_ledger.py`, `test_wikidata_single_push.py`, `test_wikidata_export_quality.py` — parity plan Phases 1–4.
- `backend/tests/unit/test_wikidata_upload_guards.py` (~17+) — reconcile-before-create per type, fail-closed outage, validator hard gate, blocked-never-written, audit trail, ledger/adopt, dry-run truthfulness.
- `backend/tests/unit/test_rdf_build.py` — clean raw 505 and flat contents create evidence-backed works.
- `backend/tests/unit/test_wikidata_studio_slicing.py`, `test_wikidata_studio_works.py`, `test_wikidata_manuscript_labels.py`, `test_wikidata_matcher_backfill.py` — build/serialisation behaviour. `test_wikidata_studio_works.py` also pins source evidence, folio qualifiers, embedded-author cleanup, Latin-heading rejection, non-inherited work P407, and exact per-item `records`.
- `backend/tests/unit/test_wikidata_studio_control_number_join.py` — quoted/whitespace control numbers join records to approved authority and NER evidence before item projection.
- `backend/tests/unit/test_wikidata_autofix_apply.py`, `test_wikidata_entity_compare.py` — AI-fix merge + live compare.
- `backend/tests/test_hmo_instance_qids_for_run.py` — HMO QID injection into the fingerprint/build.
- `frontend/e2e/wikidata-studio.spec.ts` — page (modern + legacy), AI verification, filters, sort, approval, force-rebuild.
- `frontend/e2e/wikidata-item-table.spec.ts` — review table columns, data status, search, approval PATCH, upload-outcome filter.
- `frontend/e2e/wikidata-item-drawer.spec.ts` — drawer apply-fix, push, reconcile API shapes.
- `frontend/e2e/wikidata-upload-panel.spec.ts` — upload controls, moratorium pill, pre-verify fail confirm gate.
- `frontend/tests/unit/useVerifyJob.spec.ts` — verify jobs upsert into the global job tray on start and reset the modal state when enqueue rejects.

Any new external-write path or reconcile change MUST extend
`test_wikidata_upload_guards.py` (Rule W-30).


- `backend/tests/test_wikidata_items_export_import.py` — diagnostic CSV columns include authority evidence and local-reference target JSON.
- `eval-agent/tests/test_wikidata_item.py` — evaluator payload/prompt carries authority, internal-reference, and contents/catalog context.


- `backend/tests/unit/test_wikidata_builder_modules.py` — public builder API retains the shared models and all extracted projection methods.

- `backend/tests/unit/test_wikidata_work_candidates.py` — source-aware 500/505 decisions, catalogue-prose rejection, authority overrides, and sanitation.
- `backend/tests/unit/test_wikidata_studio_fingerprint.py` — MARC JSON changes invalidate the durable Studio build cache.
