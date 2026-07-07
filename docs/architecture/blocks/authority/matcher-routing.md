# Authority Enrichment — How it works: matcher routing

> Up: [Authority Enrichment](README.md)

**Entity production and dedup.** `marc_ingest.extract_named_entities` emits
entities with `{text, kind, role, field, dates}` (MARC `$d` dates ride along
from 100/600/700 for homonym resolution). A final dedup pass keys on
`(normalize_entity_key(text), kind)` and merges duplicate roles by priority
(author/scribe 4 > contributor 3 > subject/former_owner 2 > production_place 1
> place 0), recording losers in `alt_roles` (`marc_ingest.py:1165-1225`).
Different kinds with the same text are NOT collapsed.

**Routing by kind** (`authority.py::_match_one`, ~line 996). The entity is
classified once: `is_place` when kind ∈ {place, location, geographic}, role
key ends with `_place`, or a subject text `_looks_like_place` (651/752/260/264
or place-typed subject slots). Non-person kinds are {work, corporate,
organization, topic, meeting}; everything else is a person. Then:

1. **Places**: KIMA (`match_place`) → on miss, Ashkenazi gazetteer fallback
   (never overrides KIMA; supplies `kima_lat/lon` + optional QID, source stays
   heuristic-ish) → Mazal place (`match_mazal_place`, NLI ID). A KIMA row's
   `mazal_nli_id` backfills `mazal_id` when the Mazal place lookup misses
   (`authority.py:1202`).
2. **Works**: `_mazal_match_work` over `work_title_variants` (exact →
   containment → trigram fuzzy in Postgres).
3. **Corporate/meeting**: `_mazal_match_corporate`; **topic**:
   `_mazal_match_subject` (ORDER BY tag 150 > 450).
4. **Persons**: `_mazal_match_person(dates=$d, ms_year, role)` → homonym
   scoring may abstain → personality rematch to tag-100 when a non-100 row won
   and the role `prefers_mazal_personality` → VIAF SRU (**skipped** when Mazal
   אישיות tag-100 is confirmed, or on homonym abstain) → Wikidata: P8189 (by
   Mazal ID) first, then person-label search only if no personality
   confirmation → date backfill by QID → `_wikidata_enrich_qid` (he_label,
   en_description, P214 VIAF cross-enrichment).
5. **Non-person external enrichment** (`_apply_non_person_external_enrichment`)
   is conservative: VIAF SRU typed searches (geographic/uniform-title/corporate)
   fire only when *anchored* (Mazal hit, or KIMA/gazetteer for places), and
   SRU results are rejected unless the VIAF `nameType` matches
   (`_viaf_name_type_allowed`, fail-closed). Wikidata label search for works/
   corporates requires an anchor; human QIDs (Q5/Q15632617) are rejected on
   non-person rows.

Every Mazal `entity_type` result passes `_apply_mazal_entity_type_gate` — a
person row that resolved to a place/work/corporate/subject record (or vice
versa) gets the ID cleared and `mazal_entity_type_mismatch` stamped.

**Homonym scoring + abstain** (`homonym_scoring.py`). All backends fetch up to
8 candidate rows ordered `CASE main_marc_tag WHEN '100' THEN 1 WHEN '400' THEN
2 ELSE 3 END, dates DESC, nli_id`. `pick_mazal_candidate` scores: +100 tag
100, −40 tag 150/450, +50 MARC-$d↔Mazal-dates overlap (fuzzy, via
`dates_overlap`), +20 ms_year plausibility (no `evaluate_date_conflict`), −30
fuzzy match. **Abstain** when: ≥2 personalities with no MARC dates and the gap
≤15 with no overlap; or top-two gap ≤15 with neither overlapping; or best
score ≤0 without overlap. Abstain returns `{_abstain, homonym_candidates
(top 8 scored), homonym_abstain_reason, personality_count}` — the match row is
kept with empty `mazal_id`, flag `homonym_unresolved`, source `unresolved`,
and person VIAF/Wikidata searches are suppressed.

**Confidence.** ≥2 surviving sources → `high` + `source="cross_source"`; one
source → `_single_source_bucket` heuristic (length, patronymic ` בן `,
inverted-heading comma, known role); Mazal-without-tag-100 + VIAF caps `high`
to `medium`. Guards can only lower the bucket.
