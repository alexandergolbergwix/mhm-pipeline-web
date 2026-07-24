# Authority Enrichment — Tests pinning this block

> Up: [Authority Enrichment](README.md)

- `backend/tests/unit/test_authority_retirement.py` — retired mutation 410
	behavior, structured telemetry, and explicit rollback configuration.

- `backend/tests/unit/test_authority_routing.py` — matcher routing by kind (person/place/work), KIMA→Mazal backfill
- `backend/tests/unit/test_authority_supervisor_examples.py` — Gilla's 2026-06 real-record regression examples
- `backend/tests/unit/test_authority_hardening.py` — guard verdicts, flag accumulation, ID stripping, idempotency
- `backend/tests/unit/test_homonym_scoring.py` — scoring weights, abstain conditions, tie threshold
- `backend/tests/unit/test_viaf_mazal_guards.py` — VIAF date mismatch, cross-source conflict, SRU skip on tag-100; the authority pipeline rejects a Wikidata QID when live P214 disagrees with the independent VIAF match
- `backend/tests/unit/test_authority_auto_approve.py` — rule filter + blocked guard flags
- `backend/tests/unit/test_authority_post_enrich.py` — personality cross-links, sibling crosscheck pass
- `backend/tests/unit/test_kima_disambiguate.py` — multi-QID KIMA abstain + cluster sameAs URI minting (W-84 / W-101)
- `backend/tests/unit/test_postgres_authority_matchers.py` — Postgres backend SQL / normalization parity
- `backend/tests/test_place_coords_in_rdf.py` — KIMA geo + person Mazal/VIAF/WD cluster claims in RDF
- `backend/tests/unit/test_colophon_structured.py`, `test_notes_work_extraction.py` — note-sourced entities feeding this block
- `backend/tests/test_provenance_events_ingest.py`, `test_institution_place.py`, `test_ashkenazi_gazetteer.py` — place chain
- `frontend/e2e/authority-homonym-picker.spec.ts`, `authority-biodata.spec.ts`, `authority-grouping.spec.ts` — curator UI click paths

- `backend/scripts/run_hmo_production_e2e.py` — read-only production audit for enrichment false-positive guards, live HMO read-back, canonical RDF, canonical Wikidata Studio, and legacy/canonical shadow differences
