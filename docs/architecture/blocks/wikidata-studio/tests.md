# Wikidata Studio — Tests pinning this block

> Up: [Wikidata Studio](README.md)

- `backend/tests/unit/test_item_validator.py` — every check incl. label-hygiene warnings + `TestP50OnManuscript`, `TestP7416AsQuantity`, `TestP31WrongQid`.
- `backend/tests/test_wikidata_items_export_import.py` — override export/import round-trip and diagnostic CSV coverage (item fields, MARC context, and full AI verdict).
- `backend/tests/test_wikidata_export_quality_checker.py` — compact JSON/CSV audit reports only actionable failures and distinguishes authority-approved-only from diagnostic exports.
- `backend/tests/test_section_export_router.py` — section CSV retains item approval, verdict JSON, source records, validation, authority, and work-candidate evidence.
- `backend/tests/test_wikidata_item_views.py`, `test_wikidata_verdict_persistence.py`, and `unit/test_wikidata_verdict_cache.py` pin build `records` ↔ verify `record_ids`, MARC/local-target fingerprint parity, prompt-visible statement/work evidence, and safe stale-key display; `unit/test_marc_subject_resolve.py` and `test_marc_650_655_lod.py` pin verified static QIDs/labels and broad-P921 omission.
- `backend/tests/unit/test_wikidata_upload_guards.py` (~17+) — reconcile-before-create per type, fail-closed outage, validator hard gate, blocked-never-written, audit trail, ledger/adopt, dry-run truthfulness.
- `backend/tests/unit/test_rdf_build.py` — clean raw 505 and flat contents create evidence-backed works.
- `backend/tests/unit/test_wikidata_studio_slicing.py`, `test_wikidata_studio_works.py`, `test_wikidata_manuscript_labels.py`, `test_wikidata_matcher_backfill.py` — build/serialisation behaviour. `test_wikidata_studio_works.py` also pins source evidence, folio qualifiers, embedded-author cleanup, Latin-heading rejection, non-inherited work P407, and exact per-item `records`; it also pins contents-level author fields and
approved work-QID reuse so enrichment metadata cannot be dropped.
- `backend/tests/unit/test_wikidata_studio_control_number_join.py` — quoted/whitespace control numbers join records to approved authority and NER evidence before item projection.
- `backend/tests/unit/test_wikidata_autofix_apply.py`, `test_wikidata_entity_compare.py` — AI-fix merge + live compare.
- `backend/tests/test_hmo_instance_qids_for_run.py` — HMO QID injection into the fingerprint/build, including quoted control-number normalisation.
- `backend/tests/unit/test_hmo_wikidata_projection.py`, `unit/test_property_mapping_hmo_links.py`, and `unit/test_item_builder_hmo_links.py` — exact-URI, valid-QID, conflicting-mapping, real-item URL, malformed-QID, and slug-fallback behavior at the HMO→Wikidata boundary.
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
- `backend/tests/unit/test_wikidata_phase1_projection.py` — Phase 1 evidence gates for P136/P31, canonical-holder P195, verified Masorah P921, MARC-100 work chains, facsimile typing, P1684, P127, and descriptions.
- `backend/tests/unit/test_hebrew_date_parse.py` — Hebrew geresh/gershayim century parsing and mixed century/year fallback; malformed date tokens must not abort a record.
- `backend/tests/unit/test_item_validator.py` — legitimate internal Hebrew gershayim are not reported as LABEL_QUOTE_NOISE.
- `backend/scripts/audit_marc_tsv_scale.py` — streaming full-corpus normalization plus bounded deterministic builder smoke test.

- `backend/tests/unit/test_wikidata_studio_source_cache.py` — legacy/canonical cache lookup isolation for shadow builds.
