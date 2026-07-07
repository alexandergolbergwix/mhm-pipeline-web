# Authority Enrichment — How it works: payload, backends, caching

> Up: [Authority Enrichment](README.md)

**Payload completeness contract** (Rules W-23/W-29, `authority.py:1643-1699`).
Every payload carries: `sources` + `source_count`, `guard_flags`,
`birth_year/death_year/ms_year` + year provenance (`catalog_year`,
`colophon_year`, `ms_year_source`), `preferred_name_lat` (VIAF > Mazal > MARC
text), `preferred_name_heb` (Mazal > `wikidata_he_label`), `viaf_uri` /
`wikidata_uri` canonical URIs, `cluster_ids` (gnd/lc/isni/bnf/j9u from the
VIAF cluster — never `{}` when VIAF matched), `mazal_aleph_id`,
`mazal_dates_raw`, `wikidata_he_label`, `wikidata_en_description`,
`kima_id/heb/rom/lat/lon/geonames/viaf_id/mazal_nli_id`, fuzzy indicators,
`main_marc_tag`, `role_kind`, `reasoning`, `ai_verdict`, biodata slice, and
`homonym_*` metadata. KIMA-resolved QIDs stay attributed to `"kima"` in
`sources`, never `"wikidata"` alone.

**Backends** (`authority_backend.py`). `AUTHORITY_MODE=postgres` (production,
Rule W-28) queries `mazal_*`/`kima_*` tables directly over a lazily-reopened
autocommit psycopg2 connection, with normalization mirroring the SQLite
matchers byte-for-byte and a trigram `%%`-similarity fuzzy fallback
(`FUZZY_MIN_SIM=0.45`); it wraps a `LocalAuthorityBackend` as exception
fallback. `local` uses the vendored SQLite matchers via `asyncio.to_thread`;
`modal` is the legacy HTTPS app (no work/corporate/subject/personality
endpoints — returns None).

**Caching tiers above the backend.** Every external lookup routes through
`DesktopMatcher._cached` → `inference_cache.cache_lookup_or_call` (Redis L1 →
Postgres → fetch) when a `db_session` is provided. Kinds and TTLs
(`inference_cache.py:82-106`): `authority.mazal` 90 d Postgres / 24 h Redis;
`authority.viaf` + `authority.wikidata` 30 d / 24 h; `authority.kima` 180 d /
24 h. `_wikidata_enrich_qid` results are cached 30 d under
`op=enrich_qid`. A per-request in-instance cache (`_mazal_detail_cache`,
`_kima_detail_cache`) eliminates within-request double SELECTs.

**Dedup keys.** Ingest: `(normalize_entity_key(text), kind)` with
role-priority merge. Run insert (`run.py:66-97`):
`(control_number, normalize_entity_key(text), kind, normalize_role(role))`.
Re-enrich upsert uses the identical `match_key` (`authority_re_enrich.py:22`)
— duplicates collapse to the first row, and rows whose key no longer appears
in extraction are purged as orphans.
