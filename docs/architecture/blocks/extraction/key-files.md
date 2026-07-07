# AI Extraction — Key files

> Up: [AI Extraction](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/marc_ingest.py` | Upload parsing (all formats), `_collapse_marc_subfields` normalisation, colophon/work-mention/editorial extraction, provenance events, `extract_named_entities` + role-priority dedup |
| `backend/app/pipeline/extraction.py` | `extract_entities_stream` orchestrator: warm-up, per-record model loop, desktop post-filters, offset rebasing, `ner_results.json` writer |
| `backend/app/pipeline/extraction_backend.py` | `InferenceBackend` Protocol, `resolve_mode()` (EXTRACTION_MODE), `build_backend()` factory |
| `backend/app/pipeline/extraction_backend_modal.py` | `ModalInferenceBackend` — one HTTPS POST per record to the Modal `/extract` endpoint; wraps every call in the inference cache |
| `backend/app/pipeline/extraction_backend_local.py` | In-process torch backend (dev default) |
| `backend/app/pipeline/extraction_backend_hf.py` | HuggingFace Inference Providers backend |
| `backend/app/pipeline/extraction_job.py` | Background job wrapper (`run_extraction_job`) with per-record progress + cancel, dispatched by `run_job_service.py` (kind `"extraction"`) |
| `backend/app/pipeline/extraction_entities_cache.py` | Fingerprint/ETag for `GET /entities`; only the unfiltered list is cached |
| `backend/app/pipeline/extraction_actions.py` | Prefab AI-verify actions (`audit_ner_extraction`, `check_ner_genre`) — no free-text goals |
| `backend/app/pipeline/marc_structured_index.py` | `MarcStructuredIndex.classify()` — grounded / wrong_field / novel / unknown Exists-in classification |
| `backend/app/routers/extraction.py` | SSE start-stream, results/status, entities list, MARC source, PATCH entity, bulk-approve, auto-approve preview/apply, `_bulk_persist_entities` |
| `backend/app/models/extraction_approval.py` | `ExtractionApproval` row: content-addressed key, prediction snapshot, `override_*`, `approved*`, `ai_verdict`, `exists_in` JSONB |
| `modal/modal_app.py` | Modal app `mhm-ner`: image with pre-baked weights, `MhmNer` class, `/extract` + `/health` + `/transliterate` ASGI endpoints |
| `modal/README.md` | Deploy steps, pay-per-call economics, cold-start numbers |
| `frontend/src/components/extraction/` | `EntityTable`, `EntityFilterChips`, `ColumnFilterPopup`, `EntityActionsBar`, `EntityEditModal`, `MarcSourceDrawer`, `NerVerificationModal`, `AiVerdictPill`, `EntityDetailDrawer` |
| `frontend/src/hooks/useApprovalStore.ts` | Poll store: 2s while a verify modal is open, 30s idle; emits `mhm.entities.refreshed` DOM event |
