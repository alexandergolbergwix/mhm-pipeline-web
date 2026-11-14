# AI Extraction — Tests pinning this block

> Up: [AI Extraction](README.md)

- `backend/tests/test_extraction_rejection.py` verifies rejection, import/export, cache changes, and exclusion from auto-approve.
- `frontend/tests/unit/extractionRejection.spec.ts` verifies request payloads and a rejected-only change on the next poll.

- `backend/tests/unit/test_marc_ingest_ner_inputs.py` — subfield collapse → NER inputs
- `backend/tests/unit/test_marc_person_date_alignment.py` checks empty slots, sparse dates, source repair, and retained review evidence (W-322).
- `backend/tests/unit/test_colophon_structured.py`, `test_notes_work_extraction.py`, `test_editorial_extraction.py` — colophon year/scribe, anchored/quoted work mentions, stale-derived cleanup, editor notes
- `backend/tests/test_provenance_events_ingest.py` — 541/583 provenance events (Rule W-32)
- `backend/tests/unit/test_marc_date_sources.py`, `test_marc_dates_no_008.py`, `test_hebrew_date_parse.py` — the 260/264-not-008 date contract, Hebrew century parsing, and BCE boundaries
- `backend/tests/unit/test_marc_710_pipe_split.py`, `test_marc_650_655_lod.py`, `test_marc_corporate_topic.py`, `test_marc_coverage_fixes.py`, `test_marc_subject_resolve.py` — field-level ingest edge cases
- `backend/tests/test_marc_structured_index.py` — Exists-in classify contract
- `backend/tests/unit/test_extraction_postprocessing.py`, `test_check_extraction_dates.py` — post-filter chain
- `backend/tests/unit/test_extraction_auto_approve.py` — auto-approve predicate
- `backend/tests/test_extraction_entities_cache.py`, `test_extraction_entity_cache_invalidation.py` — entities ETag + invalidation
- `backend/tests/test_extraction_verify_router.py`, `test_extraction_verify_suggested_fix.py` — NER verify flow (Rules W-17/W-18)
- `backend/tests/unit/test_judge_failure_is_not_a_verdict.py` — shared
  provider-error normalization for persisted NER verdicts (Rule W-211)
- `frontend/e2e/extraction-review.spec.ts`, `extraction-cache.spec.ts`, `extraction-autofix.spec.ts` — click-path coverage of the review surface (Rule W-19)

- `scripts.audit_mapping_coverage` is the scale check for the full MARC corpus:
  it asserts zero normalization errors and zero unmapped non-empty tags.
- `backend/tests/unit/test_gila_full_path_regressions.py` verifies raw script
  wording, source folios, catalogue semantics, and content scope through the builder (W-305).

W-326: `test_marc_occurrences.py` covers MARCXML, binary MARC, field order, sparse roles, removed source fields, and repeated preparation. The migration test also supplies the older extraction table. Revision 0053 uses batch alteration so SQLite can check the same constraint.

`test_notes_work_extraction.py` retains the exact source key with the 505 title, source text, and folio.
