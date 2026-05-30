# Project sub-hierarchy plan

Canonical reference for the 5-stage project hierarchy in the web app.
Authoritative — anything that contradicts this file is the file's
fault.

## Hierarchy

```
Project
  ├─ Stage 2 · AI Extraction         (NER + classifier; HF Hub delivery)
  ├─ Stage 3 · Authority Enrichment  (Mazal/VIAF/Wikidata/KIMA)
  ├─ Stage 4 · RDF Graph             (HMO ontology; Cytoscape UI)
  ├─ HMO Wikibase Studio             (IIIF + crosswalk → wikibase.cloud)
  └─ Wikidata Studio                 (existing — polish only)
       └─ AI verification             (per-run verb, opens from any stage)
```

The eval-agent **ai-verify** modal is reachable from inside Stage 2 /
Stage 3 / Wikidata Studio panels — not as a top-level destination.
The Phase-1 LLM planner (eval-agent `orchestrator/`) is an internal
research tool; not user-facing.

## Reuse map

| Subsystem | Desktop module(s) | Web status | Action |
|---|---|---|---|
| Stage 1 — MARC parse | `marc_ingest.py` | ✓ already in web | none |
| Stage 2 — Person NER (joint) | `ner/inference_pipeline.py` (HF Hub) | absent | **REWRITE** in `app/pipeline/extraction.py` |
| Stage 2 — Provenance + Contents NER | `ner/ner_inference_pipeline.py` | absent | **COPY + threadpool** |
| Stage 2 — Genre classifier | `converter/authority/genre_classifier.py` | already copied, unused | **WIRE** via threadpool |
| Stage 2 — Post-filters | `converter/authority/ner_post_filters.py` | absent | **COPY** |
| Stage 3 — Matchers (Mazal/VIAF/Wikidata/KIMA) | `converter/authority/*.py` | ✓ byte-identical | none |
| Stage 3 — Hardening (7 guards) | `controller/workers.py::AuthorityWorker` | partial (date guard only) | **PORT** to `app/pipeline/authority_hardening.py` |
| Stage 4 — RDF mapper | `converter/transformer/mapper.py` | ✓ already in web | wire endpoint |
| Stage 4 — Ontology | `ontology/hebrew-manuscripts.ttl` | absent | **COPY** to `backend/ontology/` |
| Stage 4 — SHACL | `ontology/shacl-shapes.ttl` + `pyshacl` | absent | **COPY + threadpool** |
| Stage 4 — Graph viewer | `gui/widgets/knowledge_graph_view.py` (Cytoscape in Qt) | absent | **REWRITE** in React + `react-cytoscapejs` |
| HMO — Manifest builder | `converter/wikidata/iiif_manifest_builder.py` | ✓ already in web | wire endpoint |
| HMO — Uploader | `converter/wikidata/iiif_uploader.py` | ✓ already in web | wire endpoint |
| HMO — Crosswalk + coverage | `converter/wikidata/hmo_crosswalk.py`, `projection_coverage.py` | ✓ already in web | wire endpoint |
| HMO — Wikibase Cloud Writer | `converter/wikibase/cloud_client.py` | absent | **COPY + adapt creds** |
| Wikidata Studio backend | `controller/workers.py::WikidataUploadWorker` | ✓ already wrapped | none |
| Safety guards (Rule 38) | `converter/wikidata/uploader.py` | ✓ byte-identical | none |

## URL surface

```
GET  /api/projects/{id}
GET  /api/projects/{id}/runs
POST /api/projects/{id}/runs                      (upload MARC)

GET  /api/runs/{id}
GET  /api/runs/{id}/extraction                    (Stage 2)
POST /api/runs/{id}/extraction/start              (SSE)
GET  /api/runs/{id}/extraction/results

GET  /api/runs/{id}/authority/matches             (Stage 3 — existing)
POST /api/runs/{id}/authority/rebuild             (NEW — apply hardening)
POST /api/runs/{id}/matches/backfill-dates        (existing)

GET  /api/runs/{id}/rdf/graph                     (Stage 4)
POST /api/runs/{id}/rdf/build
POST /api/runs/{id}/rdf/validate                  (SHACL)
GET  /api/runs/{id}/rdf/download.ttl

GET  /api/runs/{id}/hmo-studio/coverage           (Stage 6.5)
POST /api/runs/{id}/hmo-studio/build-manifests
POST /api/runs/{id}/hmo-studio/upload-manifests

GET  /api/runs/{id}/wikidata-studio               (existing)
POST /api/runs/{id}/wikidata-studio/upload        (existing)

POST /api/runs/{id}/ai-verify/start-stream        (existing)
GET  /api/runs/{id}/ai-verify/sessions            (existing)
```

## Frontend routes

```
/projects/:projectId                              ProjectDetail (5 tiles)
/runs/:runId                                      RunOverview   (5-stage status)
/runs/:runId/extraction                           StageExtraction
/runs/:runId/authority                            StageAuthority (former RunDetail)
/runs/:runId/rdf                                  StageRdf
/runs/:runId/hmo-studio                           HmoStudio
/runs/:runId/wikidata-studio                      WikidataStudio (existing)
```

## Build order

1. Plan doc (this file).
2. Stage 2 backend (extraction router + NER runner).
3. Stage 4 backend (RDF + SHACL routers + ontology copy).
4. Test infrastructure (Playwright + Vitest + backend pytest).
5. Project overview page (5 tiles).
6. RunOverview page (5-stage status).
7. Stage 2 UI (extraction results table).
8. Stage 4 UI (Cytoscape graph + TTL + SHACL violations).
9. HMO Studio backend (Wikibase Cloud Writer + endpoints).
10. HMO Studio UI.
11. Authority hardening (7 guards) + Dates column.
12. AI-verify modal (complete the parked work).
13. Wikidata Studio polish.

## Decisions captured

- **HF Hub delivery for Stage 2**: only `alexgoldberg/hebrew-manuscript-joint-ner-v2` is on Hub; provenance + contents + genre `.pt` files (3 × 738 MB) need to be either pushed to a private HF org or container-bundled. Default: bundle in backend Docker (simplest); push to HF as a follow-up.
- **Cytoscape.js (web)** = same library the desktop already uses inside QWebEngineView. The 8-colour palette by ontology class transfers 1:1.
- **Bot creds for Wikibase Cloud**: stored in same encrypted store as Wikidata token (`backend/app/crypto/secrets.py`), new `key_name="wikibase_bot"`.
- **No auto-approve**: AI verdicts surface as a `✨ AI says pass` pill; curator still ticks the checkbox themselves.
- **Test pyramid**: backend pytest + httpx (route tests), Vitest + RTL (frontend units), Playwright (true e2e browser).
