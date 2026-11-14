# Gila implementation files

> Up: [Wikidata Studio](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/marc_occurrences.py` | Original fields, source keys, compatibility columns, and derived-value provenance |
| `backend/app/pipeline/marc_ingest.py` | MARCXML and occurrence-aware 505/581/942 and person data |
| `backend/converter/parser/marc_reader.py` | Original field and subfield order |
| `backend/scripts/compare_marc_associations.py` | Local comparison and independent original 942 tuples |
| `backend/app/pipeline/wikidata_source_choices.py` | Candidate proof, record/page guards, current identities, imports, and projection |
| `backend/converter/wikidata/item_builder.py` | Public attribution helper without a candidate-read import cycle |
| `backend/app/pipeline/wikidata_source_coverage.py` | Passage inventory, source gaps, and nine requirement states |
| `backend/app/routers/wikidata_source_review.py` | Authenticated source review and versioned choice API |
| `backend/app/main.py` | Registration of the source-review router |
| `backend/app/models/item_override.py` | Durable `source_review_choices` |
| `backend/app/migrations/versions/0055_source_review_choices.py` | Nullable JSONB column |
| `backend/app/pipeline/hmo_canonical_wikidata.py` | Source choices after the canonical merge; version v27 |
| `backend/app/pipeline/wikidata_studio.py` | Override fingerprint and legacy source choices; version v23 |
| `backend/app/pipeline/wikidata_studio_batches.py` | Source choices in streamed builds |
| `backend/app/pipeline/wikidata_item_views.py` | Current proof metadata and rebuild state |
| `backend/app/pipeline/wikidata_item_merge.py` | Override serialization |
| `backend/app/schemas/export.py` | Typed project export retains person and source choices |
| `backend/app/routers/history.py` | Explicit proof restoration or clear |
| `backend/app/routers/section_import.py` | Current proof checks before an import |
| `backend/app/routers/section_export.py`, `export.py` | Complete proof and citation evidence in exports |
| `backend/converter/wikidata/catalog_references.py` | Original citation, candidate year/pages, and identity review state |
| `backend/converter/wikidata/property_mapping.py` | Reviewed P1343/P304 metadata and qualifier classification |
| `backend/converter/wikidata/property_labels.py` | P304 display metadata for source pages |
| `backend/app/pipeline/marc_verify_context.py` | 505$g and 505$r in the contents evidence for verification |
| `backend/app/pipeline/wikidata_verify_evidence.py` | Bibliography channels for P1343 |
| `backend/converter/wikidata/work_author_claims.py` | Compatible peer author claims before an optional credited name |
| `backend/app/pipeline/wikidata_canonical_enrichment.py` | Source-preserving author normalization around the merge |
| `backend/app/pipeline/wikidata_gila_audit.py`, `wikidata_gila_completion.py` | Passage inventory and honest requirement counts |
| `backend/scripts/export_gila_completion.py` | Source passage CSV and completion manifest |
| `backend/converter/wikidata/retry_policy.py` | Bulk maxlag and Retry-After parser |
| `backend/converter/wikidata/uploader.py` | Fresh creator user-ID proof and public identity checks |
| `backend/app/publication/gateway.py`, `wikidata_gateway.py` | Structured retry delay and target checks |
| `backend/app/publication/core.py`, `repository.py`, `sql_repository.py` | Durable delayed eligibility and atomic result state |
| `backend/app/publication/historical_repairs.py` | Exact repair projection and gateway through Publication |
| `backend/app/pipeline/wikidata_gila_execution.py` | Exact one-send transport and fixed creator ID for this repair scope |
| `backend/scripts/execute_gila_repairs.py` | SQL-backed Publication preparation and resume |
| `frontend/src/api/wikidataSourceReview.ts` | Typed source-review client |
| `frontend/src/components/wikidata/WikidataSourceReview.tsx` | Requirement table and source choice forms |
| `frontend/src/components/wikidata/WikidataItemDetailDrawer.tsx` | Source-review panel within a manuscript drawer |
| `frontend/src/routes/ProjectDetail.tsx` | MARCXML source option |
