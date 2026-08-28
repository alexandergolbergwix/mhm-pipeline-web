# AI Extraction — Tests pinning this block

> Up: [AI Extraction](README.md)

- `backend/tests/unit/test_marc_ingest_ner_inputs.py` — subfield collapse → NER inputs
- `backend/tests/unit/test_colophon_structured.py`, `test_notes_work_extraction.py`, `test_editorial_extraction.py` — colophon year/scribe, anchored/quoted work mentions, stale-derived cleanup, editor notes
- `backend/tests/test_provenance_events_ingest.py` — 541/583 provenance events (Rule W-32)
- `backend/tests/unit/test_marc_date_sources.py`, `test_marc_dates_no_008.py`, `test_hebrew_date_parse.py` — the 260/264-not-008 date contract, Hebrew century parsing, and BCE boundaries
- `backend/tests/unit/test_marc_710_pipe_split.py`, `test_marc_650_655_lod.py`, `test_marc_corporate_topic.py`, `test_marc_coverage_fixes.py`, `test_marc_subject_resolve.py` — field-level ingest edge cases
- `backend/tests/test_marc_structured_index.py` — Exists-in classify contract
- `backend/tests/unit/test_extraction_postprocessing.py`, `test_check_extraction_dates.py` — post-filter chain
- `backend/tests/unit/test_extraction_auto_approve.py` — auto-approve predicate
- `backend/tests/test_extraction_entities_cache.py`, `test_extraction_entity_cache_invalidation.py` — entities ETag + invalidation
- `backend/tests/test_extraction_verify_router.py`, `test_extraction_verify_suggested_fix.py` — NER verify flow (Rules W-17/W-18)
- `frontend/e2e/extraction-review.spec.ts`, `extraction-cache.spec.ts`, `extraction-autofix.spec.ts` — click-path coverage of the review surface (Rule W-19)

- `scripts.audit_mapping_coverage` is the scale check for the full MARC corpus:
  it asserts zero normalization errors and zero unmapped non-empty tags.
