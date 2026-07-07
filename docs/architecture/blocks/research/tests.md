# Research Surface — Tests pinning this block

> Up: [Research Surface](README.md)

- `backend/tests/test_provenance_map_guards.py` — A1–A7/D2 integrity guards + Rule-60 event-stop cases
- `backend/tests/test_provenance_map_endpoint.py`, `test_provenance_timeline.py` — router behaviour
- `backend/tests/test_research_geo_enrich.py`, `test_institution_place.py` — WDQS parsers, abstain rules
- `backend/tests/test_corpus_movement.py`, `test_corpus_query.py` — corpus map + federation
- `backend/tests/test_research_aggregate.py`, `test_research_summary_cache.py`, `tests/unit/test_research_summary_vocab.py` — dedup, coherence gate, vocab drift
- `backend/tests/test_pathfinding.py`, `test_entity_detail.py`, `test_evidence.py`, `test_research_export.py`, `test_saved_queries.py`, `test_geography_global.py`
- `backend/tests/unit/test_graph_index.py` — catalog/viewport budgeting
- `backend/tests/test_provenance_events_ingest.py`, `test_provenance_events_rdf.py` — Rule W-32 event channel feeding the maps
