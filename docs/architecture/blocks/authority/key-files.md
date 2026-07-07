# Authority Enrichment — Key files

> Up: [Authority Enrichment](README.md)

| File | Purpose |
|---|---|
| `backend/app/pipeline/authority.py` | `DesktopMatcher._match_one` — the routing orchestrator (KIMA → gazetteer → Mazal typed → VIAF → Wikidata), confidence tiering, payload assembly |
| `backend/app/pipeline/authority_backend.py` | `AuthorityBackend` Protocol + `PostgresAuthorityBackend` (production), `LocalAuthorityBackend` (SQLite dev), `ModalAuthorityBackend` (legacy); `build_authority_backend()` reads `AUTHORITY_MODE` |
| `backend/app/pipeline/authority_hardening.py` | `GuardVerdict`, ~19 pure guards, `apply_hardening_guards` orchestrator (flag accumulation, confidence downgrade, ID stripping) |
| `backend/app/pipeline/homonym_scoring.py` | `pick_mazal_candidate` scoring + abstain (Rule W-37) |
| `backend/app/pipeline/authority_post_enrich.py` | Post-passes with sibling context: personality cross-links + Wikidata crosscheck (`finalize_authority_matches`) |
| `backend/app/pipeline/authority_re_enrich.py` | Shared re-enrich orchestration (`re_enrich_run`, `match_key`) for POST + SSE endpoints |
| `backend/app/pipeline/authority_re_enrich_job.py` | Background job variant with progress/cancel via `run_job_service` |
| `backend/app/pipeline/ashkenazi_gazetteer.py` | Curated diaspora-community fallback consulted only after a KIMA miss |
| `backend/app/pipeline/research_geo_enrich.py` | `owner_place` (P551/P937/P20/P19) + `institution_place` (P159→P276→P131→P625) SPARQL seat resolution |
| `backend/app/pipeline/entity_normalize.py` | `normalize_entity_text/key`, `normalize_role/role_key`, `MAZAL_PERSONALITY_PREFER_ROLE_KEYS` |
| `backend/app/pipeline/entity_kind_infer.py` | `infer_entity_kind(name, tag)` — person/corporate/meeting routing for 7xx headings |
| `backend/app/pipeline/date_entity_normalize.py` | Provenance DATE-span → 4-digit Gregorian year (gematria, `[=1826]`, NLI `7[5]16` brackets) |
| `backend/converter/authority/` | Vendored desktop matchers: `mazal_matcher`, `kima_matcher`, `viaf_matcher`, `wikidata_matcher`, `wikidata_crosscheck`, `stage3_guards`, `biodata_enrich` |
| `backend/converter/authority/stage3_guards.py` | Shared guard primitives: `is_placeholder_name`, `is_short_name_homonym`, `evaluate_date_conflict`, `HARD_REJECT_GUARD_FLAGS`, `authority_payload_blocked` |
| `backend/app/routers/runs.py` | Matches CRUD, `auto-approve(/preview)`, `candidates` + `pick-candidate`, `edit`, `ai-verify`, `authority/rebuild`, `authority/re-enrich(/stream)`, `backfill-dates` |
| `backend/app/models/run.py:77` | `AuthorityMatch` model (JSONB `payload`, `approved` triple) |
| `backend/app/migrations/versions/0018_authority_pg_tables.py` | Creates `mazal_authorities`, `mazal_name_index`, `kima_places`, `kima_name_index` (hash indexes on `normalized_name`) |
| `backend/app/migrations/versions/0020_mazal_heading_metadata.py` | Adds `mazal_authorities.main_marc_tag` + btree `(entity_type, normalized_name)` |
| `backend/scripts/import_mazal_to_postgres.py`, `import_kima_to_postgres.py` | Idempotent TRUNCATE + re-import from desktop SQLite (~10 min / ~15 s) |
| `frontend/src/components/authority/` | `AuthorityTable`, `AuthorityDetailDrawer` (with homonym Pick control), `AuthorityAutoApproveRuleBuilder`, `AuthorityMatchingHelp` |
