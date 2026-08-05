# Authority matching + MARC ingest

Architectural invariants for this area. Each rule records a real
production incident — read the whole rule before changing the code it
names. Index: [CLAUDE.md](../../../CLAUDE.md).

### Rule W-23 — KIMA / VIAF / Mazal payload completeness (added 2026-06-03)

- `payload.cluster_ids` MUST be populated from VIAF `meta`
  (gnd/lc/isni/bnf/j9u) — never `{}` when VIAF matched.
- `payload.preferred_name_lat` uses VIAF authority form > Mazal >
  MARC heading fallback.
- `payload.preferred_name_heb` stores Mazal Hebrew preferred name
  when Mazal details are available.
- `payload.kima_*` fields store the full KIMA index row (id, heb,
  rom, lat, lon, geonames) for place matches via
  `DesktopMatcher._kima_enrich_place`.
- `payload.sources` MUST include `"kima"` when KIMA resolved the
  place — not attributed to `"wikidata"` alone when only KIMA
  supplied the QID.

### Rule W-28 — Mazal + KIMA live in Heroku Postgres (added 2026-06-05)

**Production default since 2026-06-05.** Mazal (2.5 M authority records +
5.3 M name-index rows, ~980 MB SQLite → ~600 MB Postgres) and KIMA
(48 K places + 129 K name-index rows, 15 MB) are imported into the same
Heroku Essential-1 Postgres instance via:

```bash
# One-time import — run locally pointing at Heroku DATABASE_URL
# Idempotent (TRUNCATE + re-import).
cd backend && DATABASE_URL=... KIMA_DB_PATH=backend/data/kima/kima_index.db \
  .venv/bin/python -m scripts.import_kima_to_postgres   # ~15 s

cd backend && DATABASE_URL=... MAZAL_DB_PATH=.../mazal_index.db \
  .venv/bin/python -m scripts.import_mazal_to_postgres  # ~10 min
```

`backend/app/pipeline/authority_backend.py` now has three implementations:

| Class | `AUTHORITY_MODE` | Notes |
|---|---|---|
| `PostgresAuthorityBackend` | `postgres` | Direct Postgres SELECT over `mazal_*` / `kima_*` tables. **Production.** |
| `LocalAuthorityBackend` | `local` | Local SQLite via `asyncio.to_thread`. Works in dev when SQLite files are present. |
| `ModalAuthorityBackend` | `modal` | Legacy Modal HTTPS endpoint. Kept as fallback; no longer deployed. |

Set on Heroku:
```bash
heroku config:set AUTHORITY_MODE=postgres
```

**Postgres tables (created by migration `0018_authority_pg_tables`):**
- `mazal_authorities` — nli_id PK, entity_type, preferred_name_heb,
  preferred_name_lat, dates, aleph_id
- `mazal_name_index` — normalized_name (hash idx), nli_id, entity_type,
  script
- `kima_places` — kima_id PK, primary_heb/rom, wikidata_id, viaf_id,
  geonames_id, mazal_nli_id, lat, lon
- `kima_name_index` — normalized_name (hash idx), kima_id, script

Hash indexes are used for both `normalized_name` columns — exact-match
only, no btree 8191-byte limit. A corrupt KIMA name_index entry
(~900 KB blob) is silently skipped by the import script.

The inference cache (`Rule W-12`) still sits ABOVE this layer —
`authority.mazal` (90-day Postgres, 24 h Redis) and `authority.kima`
(180-day Postgres, 24 h Redis) wrap every Postgres lookup, so the first
resolution populates the cache and subsequent calls hit Redis or the
inference_cache table without touching `mazal_*`/`kima_*`.

**Within-request double-call elimination** is preserved:
`_mazal_match_person` and `_kima_match_place` store full detail dicts in
`_mazal_detail_cache`/`_kima_detail_cache` (per-instance) so
`_mazal_get_details`/`_kima_enrich_place` don't issue a second SELECT.

### Rule W-29 — Authority payload completeness (added 2026-06-05)

Every `AuthorityMatch.payload` produced by `DesktopMatcher._match_one`
MUST carry these fields when available:

| Field | Source | Used by |
|---|---|---|
| `viaf_uri` | VIAF match | RDF `owl:sameAs`, Wikidata P214 |
| `wikidata_uri` | Wikidata QID | RDF `owl:sameAs` |
| `preferred_name_lat` | VIAF > Mazal > MARC | RDF labels, Wikidata preferred label |
| `preferred_name_heb` | Mazal > `wikidata_he_label` | RDF labels, Wikibase Hebrew label |
| `cluster_ids` | VIAF cluster (gnd/lc/isni/bnf/j9u) | Wikidata identifier statements |
| `mazal_aleph_id` | Mazal details | NLI identifier in Wikidata (P8189) |
| `mazal_dates_raw` | Mazal dates string | Debug / date-resolver fallback |
| `wikidata_he_label` | SPARQL `rdfs:label@he` | `preferred_name_heb` fallback |
| `wikidata_en_description` | SPARQL `schema:description@en` | Wikidata/Wikibase description |
| `kima_id/heb/rom/lat/lon/geonames/viaf_id` | KIMA row | RDF place nodes (WGS84, GeoNames) |

New `_wikidata_enrich_qid` makes one SPARQL call per confirmed QID for
`he_label` + `en_description` + P214 VIAF cross-reference. Cached 30 days
under `authority.wikidata / op=enrich_qid`. VIAF cross-enrichment from
Wikidata P214 fills `viaf_id` when the VIAF SRU matcher returned no hit.

### Rule W-33 — Authority matcher routing, deduplication, and notes grounding (added 2026-06-17)

Five invariants established during the authority-enrichment fix session
(supervisor review, Gilla's 2026-06 feedback):

**Matcher routing by entity kind:**
- Person matchers (`_mazal_match_person`, VIAF, Wikidata person-name SPARQL) must
  NOT fire for place or work entities. The guard is in `_match_one` via `is_place`
  and `normalized_kind != "work"` checks.
- Place entities first run KIMA (`_kima_match_place`), then Mazal place
  (`_mazal_match_place_authority`). Person matchers are bypassed.
- Work entities (kind="work") call `_mazal_match_work` only; VIAF and Wikidata
  person matchers are not invoked.
- `kima_payload.mazal_nli_id` is backfilled into `mazal_id` when the Mazal place
  lookup misses but KIMA has a linked NLI ID.

**MARC $d dates for homonym resolution:**
- `_collapse_marc_subfields` captures `$d` from MARC 100/600/700 fields and
  propagates them as `dates` on author/contributor/subject entity dicts.
- `PostgresAuthorityBackend.match_person(dates=...)` tries an exact name + dates
  match first, then falls back to the main_marc_tag-ordered query.
- Postgres ORDER BY: `CASE a.main_marc_tag WHEN '100' THEN 1 … END` so the
  אישיות record (tag 100) is always preferred over the נושא record (tag 150)
  when both share the same normalized name. Requires migration 0020 + re-import.

**Mazal personality guard (`guard_mazal_subject_heading`):**
- Fires when `main_marc_tag != '100'` for a person author/contributor entity.
  Downgrades confidence to "medium" and stamps flag `mazal_subject_not_personality`.
  Does NOT fire for subject-role entities (600), which may legitimately hit tag 150.
- `HardeningContext` now carries a `role` field; `apply_hardening_guards` passes it
  to the guard. The `prelim["payload"]["main_marc_tag"]` slot is populated from
  `mazal_details` so the guard reads the actual Mazal match metadata.

**Deduplication (ingest + run):**
- `extract_named_entities` dedupes by `(normalize(text), kind)` with role-priority
  merge (author > contributor > subject > place). The replaced role is recorded in
  `alt_roles` on the winning entity for audit. Different kinds (place vs person)
  with the same name text are kept as separate entities — they are not collapsed.
- `run.py execute_run` uses a `(control_number, normalize(text), kind, role)` key to
  prevent inserting duplicate `AuthorityMatch` rows within a single ingest run.
- Re-enrich upsert key in `runs.py` includes `role` so author and subject rows for
  the same entity text are updated independently.

**Notes / colophon / work-title grounding:**
- `_collapse_marc_subfields` detects colophon keywords (קולופון, colophon, …) in
  `500$a` and sets `colophon_text`. `_extract_colophon_fields` then extracts
  `colophon_year` (Hebrew gematria or Gregorian) and `colophon_scribe` (patronymic).
- `_extract_work_mentions` scans `500$a` raw text for `כולל:` / `ובו:` / `מכיל:`
  patterns and emits `work_mentions` list. These flow into `extract_named_entities`
  as `{kind: "work", role: "contained_work"}` entities.
- `MarcStructuredIndex` keys now include `notes`, `colophon_text`, `colophon_year`,
  `colophon_scribe`, `work_mentions` so the Exists-in badge reflects note-sourced hits.
- `extraction.py` now wires `filter_person_role_dedup` (collapse same-name NER
  multi-segment duplicates) and `filter_with_marc_grounding` (stamps grounded /
  exists_in fields) after the existing post-filters.

**Mazal Postgres schema:**
- Migration 0020 adds `main_marc_tag TEXT` to `mazal_authorities`.
- `mazal_index.py` parse_record now ingests 150/450 (subject) tags as
  `entity_type='subject'` and records the primary heading tag in `main_marc_tag`.
- `import_mazal_to_postgres.py` detects whether the source SQLite has the new column
  (idempotent: old SQLite files still import cleanly with `main_marc_tag=NULL`).
- Re-import command after migration:
  `cd backend && DATABASE_URL=... MAZAL_DB_PATH=... .venv/bin/python -m scripts.import_mazal_to_postgres`

**Canonical enrichment playbook:**
Authority refresh now happens inside HMO Studio creation. Rebuild with
`refresh_authority=true`, upload/update the HMO items, read them back, and run
`backend/scripts/run_hmo_production_e2e.py` plus the canonical gate. The former
standalone re-enrich routes/jobs are retired by default and return HTTP 410.

Tests pinning the contract:
`backend/tests/unit/test_authority_routing.py` (11),
`backend/tests/unit/test_authority_supervisor_examples.py` (13),
`backend/tests/unit/test_colophon_structured.py` (6),
`backend/tests/unit/test_notes_work_extraction.py` (5).
Any new matcher route, dedup policy change, or note-extraction pattern MUST extend
at least one of these suites.

### Rule W-37 — Homonym abstain, scoring, and curator picker (added 2026-06-25)

When multiple Mazal **personality** rows share a normalized name and MARC `$d`
does not disambiguate, the matcher **abstains** rather than guessing:

- `homonym_scoring.pick_mazal_candidate` scores candidates (+100 tag 100,
  +50 date overlap, +20 ms_year plausibility, penalties for fuzzy / tag 150).
  **Abstain** when top two scores are within 15 points and neither has date overlap.
- On abstain: empty `mazal_id`, no VIAF SRU / Wikidata label search for persons,
  flag `homonym_unresolved`, `payload.homonym_candidates` (top 5).
- Curator resolution: `GET/POST …/matches/{id}/candidates` + `pick-candidate`;
  Authority detail drawer **Pick** control.
- `is_short_name_homonym` (stage3_guards): ≤2 tokens or ≤12 Hebrew letters;
  Mazal corroboration only when tag 100 + (date overlap OR single personality).
- Auto-approve blocks: `homonym_unresolved`, `short_name_homonym`,
  `mazal_subject_not_personality`, `viaf_date_mismatch`, `cross_source_conflict`,
  `wikidata_disagrees`.
- VIAF: skip SRU when Mazal personality confirmed; `guard_viaf_date_mismatch` strips
  `viaf_id`; crosscheck enabled in match path unless `MHM_DISABLE_WIKIDATA_CROSSCHECK=1`.
- Editorial notes: `_extract_editorial_metadata` → `editor_names`, edition fields,
  `role=editor` entities, searchable via note-index.

Tests: `test_homonym_scoring.py`, `test_viaf_mazal_guards.py`,
`test_editorial_extraction.py`, extended supervisor examples,
`frontend/e2e/authority-homonym-picker.spec.ts`.
Sync `stage3_guards.py` edits to desktop `converter/authority/` when changed.

### Rule W-74 — Hebrew date punctuation MUST NOT abort record normalization (added 2026-07-15)

A full-corpus scan of 123,621 filtered manuscript records found 5,129 MARC
normalization crashes. The Hebrew date parser treated geresh punctuation in
century tokens such as `כ'` as a thousands marker (`20,000`) and raised before
other fields could be prepared. The parser now strips geresh/gershayim before
gematria, rejects values outside the century range, and falls through to the
ordinary Hebrew-year parser for mixed catalogue prose. Tests: `test_hebrew_date_parse.py`;
full-corpus scan: 123,621 records, zero normalization errors.


### Rule W-81 — MARC coverage MUST be loss-aware (added 2026-07-16)

Every non-empty MARC tag in a supported upload must be either normalized into a
canonical extraction field or explicitly classified as evidence-only by the
streaming coverage audit. TSV/JSON ingestion reuses the desktop handlers so
values such as RDA carrier terms, alternate titles, edition notes, and local
shelfmarks reach RDF/Wikibase review. Evidence-only fields must not be turned
into speculative Wikidata claims, but they must remain inspectable. This closes
the silent-drop boundary between MARC ingestion and all downstream mappers.

### Rule W-83 — Place authority identifiers MUST preserve their namespace (added 2026-07-21)

A project Wikibase QID (for example `Q1370`) is not a Wikidata QID. MARC
bracket notation must be removed before KIMA/name-overlap matching; resolved
Wikidata place URIs are retained separately as HMO `external_wikidata_uri`
claims. Person `viaf_id`/Wikidata identifiers likewise remain explicit
authority claims so HMO entities can enrich without conflating identifier
spaces.

### Rule W-84 — Ambiguous KIMA names MUST abstain (added 2026-07-21)

A normalized place name may resolve to multiple KIMA rows. If those rows carry
conflicting Wikidata IDs, matching must abstain unless one exact primary name
uniquely disambiguates it; the UI must display local Wikibase IDs separately
from external authority IDs.

### Rule W-166 — An unconfirmed authority identity may not name or date a person (added 2026-08-05)

Amends Rules W-37 / W-53.

Incident: `mazal:987007299516905171` shipped labelled `יצחק בן שלמה בן חיים גבאי`
with a death year of 1640, for a manuscript created 1655–1660 whose MARC
contributor is `גבאי, טוביה בן חיים יצחק` (role `מעתיק`, scribe). Same family, a
different given name, and a scribe who was already dead. The authority row carried
`wikidata_crosscheck_fail` the whole time. **13 persons** in run `48ba6c13` are in
this state.

Three independent gaps:

1. **The flag gated nothing.** It is in neither `_HARD_REJECT_AUTHORITY_FLAGS` set,
   and `guard_wikidata_crosscheck` deliberately strips the Wikidata and VIAF ids
   while KEEPING Mazal — so the surviving `mazal_id` became a publishable P8189
   (Rules W-153 / W-154 are satisfied by P8189 alone) and dragged the unconfirmed
   row's dates along with it.
2. **`preferred_name_heb` overwrote the MARC heading unconditionally**, with the
   MARC form demoted to an alias and no comparison of any kind.
3. **Homonym scoring had no name term at all** — tag-100 +100, date overlap +50,
   MS plausibility +20, `_fuzzy` −30 — and `pick_mazal_candidate` returned a LONE
   candidate with zero checks. That is the branch this row came through.

Invariant:

1. **`wikidata_crosscheck_fail` is a SOFT reject.** It means "we cannot confirm
   this identity for this heading", not "this is not a person", so the item
   survives on its MARC attestation — but P569/P570 are suppressed and the dates
   are stripped from the generated description. It MUST stay out of the hard-reject
   set, which drives `_drop_conflicted_person_items`: a soft flag may never remove
   an item.
2. **An authority heading may not overwrite a MARC heading it does not match.**
   `converter/authority/heading_fidelity.py` reuses
   `wikidata_crosscheck.hebrew_label_matches` so both sides of the pipeline agree
   on what "the same name" means. Token overlap alone is not enough — the two
   Gabbai headings share a surname and two patronymics and score 0.6 — so the
   comparator strips patronymic connectors and requires the GIVEN name and the
   family name to match within one edit. On mismatch the MARC heading keeps the
   label, the authority form becomes an alias, and `heading_mismatch` records why.
3. **Homonym scoring has a name term**, and a lone mismatched candidate abstains
   unless a tag-100 heading with an overlapping date range corroborates it.
   Shipped behind `AUTHORITY_HOMONYM_NAME_TERM`, **default OFF**: this changes
   *matching*, whose output feeds person-link evidence (Rule W-162), the date
   suppression above, and the Rule W-155 drop. Measure it against live authority
   data with `scripts/dryrun_homonym_name_term.py` before enabling — the Studio
   export cannot measure it, because it never records WHICH MARC heading each
   authority row matched, so any pairing reconstructed from it mispairs and reports
   artifact flips.
