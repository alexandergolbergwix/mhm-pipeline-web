# AI Extraction — Extraction stream, persistence, and approvals

> Up: [AI Extraction](README.md)

## How it works — 4. Extraction stream and persistence

`extract_entities_stream` (`extraction.py:83`) emits SSE-friendly events:
`extraction.start` → `extraction.step` (warming/warmed/processing) →
`extraction.model.ready|unavailable` (×4) → per-record
`extraction.record.start/done` → `extraction.end` (or
`extraction.cancelled` / `extraction.error`). Per record
(`_process_one_record`):

1. Person NER over `notes` + `colophon_text` segments.
2. Provenance NER over 561 segments — or, when 561 is absent,
   ownership-keyword windows mined from notes (`_provenance_segments_for_record`).
3. Contents NER over each flattened 505 `contents` entry.
4. Offsets rebased into a concatenated `full_text`
   (verify-then-substring-repair, `_rebase_offsets`).
5. Desktop post-filters in order: `filter_work_author_folio`,
   `filter_collection_citations` (→ `catalog_references`),
   `filter_owner_length` (→ `provenance_inscriptions`),
   `filter_person_hallucinations`, `filter_date_shape`,
   `normalize_provenance_date_entities`, `filter_person_role_dedup`,
   `filter_with_marc_grounding` (Rule W-33 grounding).
6. Genre classifier on title + notes; `"other"` dropped.

Output shape matches desktop `ner_results.json` exactly. Two entry points
drive the stream: the SSE route `POST /runs/{id}/extraction/start-stream`
(`routers/extraction.py:93`) and the background job `run_extraction_job`
(kind `"extraction"`, claimed/heartbeated per Rule W-38). Both re-run
`_collapse_marc_subfields` defensively on DB rows before extraction, and both
end by calling `_bulk_persist_entities` (`routers/extraction.py:355`): a
Postgres upsert into `extraction_approvals` that on conflict updates **only**
the prediction-snapshot columns — never `approved`, `override_*`, or
`ai_verdict`. The Exists-in classification is computed once here and
snapshotted into the `exists_in` JSONB column. The DB is the durable store;
`ner_results.json` is a fast-path cache (Heroku's filesystem is ephemeral —
`get_extraction_status` falls back to counting approval rows).

## How it works — 5. Approval, override, and Exists-in model

`ExtractionApproval` is content-addressed by
`(run_id, control_number, source, text, start, end)`, so re-running
extraction re-attaches existing curator decisions. Curator fields:
`approved/approved_by/approved_at`, `override_text/override_type/override_role`
(effective values = override ?? prediction; downstream stages read the
effective values), `ai_verdict` + `ai_verdict_at` (written by the NER verify
flow, Rule W-17), `exists_in`. `PATCH /entities/{entity_id}` upserts one row;
any input-changing edit clears `ai_verdict` and recomputes `exists_in`
against the record's `MarcStructuredIndex`. Every mutation emits an
`extraction_entity` event via `_emit_extraction_event` (Rule W-21) and
invalidates the run-scoped entities cache.

`MarcStructuredIndex.classify(cn, text, candidate_type)` returns
`{status, fields, note}` with status one of `grounded` (matched in a field
expected for the candidate's type per `_TYPE_TO_FIELDS`), `wrong_field`
(matched elsewhere), `novel` (in no MARC field — genuinely new info), or
`unknown` (no record indexed). Matching is bidirectional-substring over
casefolded, punctuation-collapsed strings; note-sourced keys (`notes`,
`colophon_*`, `work_mentions`, `editorial_metadata`, `dates`,
`provenance_events`) are indexed too.

**Auto-approve** (`_auto_approve_eligible`, `routers/extraction.py:1286`):
predicate over unapproved entities — `sources`, `types`, `not_roles`,
`min_confidence` (against `model_confidence`), `require_ai_pass`,
`respect_ai_fail`. Preview and apply share the same function so counts never
diverge.
