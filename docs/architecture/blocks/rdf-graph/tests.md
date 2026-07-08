# RDF / HMO-Ontology Graph Build — Tests pinning this block

> Up: [RDF / HMO-Ontology Graph Build](README.md)

- `backend/tests/unit/test_rdf_build.py` — build core
- `backend/tests/unit/test_rdf_enrichment.py`, `test_rdf_enrichment_topics.py`,
  `test_rdf_enrichment_work_corporate.py` — approved-row merge semantics
- `backend/tests/unit/test_graph_index.py` — catalog/viewport index
- `backend/tests/unit/test_ontology_coverage.py` — coverage report
- `backend/tests/unit/test_rdf_shacl_conformance.py` — SHACL shapes
- `backend/tests/unit/test_rdf_ontology_usage.py` — ontology-usage endpoint core
- `backend/tests/unit/test_graph_builder_philological_labels.py` — overlay labels
- `backend/tests/unit/test_rdf_helpers.py` — MARC ISBD label hygiene (Rule W-45)
- `backend/tests/unit/test_graph_builder_provenance.py` — 561 provenance must not
  mint Acquisition (Rule W-45)
- `backend/tests/test_rdf_artifact.py` — durable TTL persistence/restore +
  stale on-disk refresh from Postgres
- `backend/tests/test_provenance_events_rdf.py` (5) +
  `test_provenance_events_ingest.py` (10) — Rule W-32 event nodes
- `backend/tests/test_place_coords_in_rdf.py` — coord write-back into TTL
