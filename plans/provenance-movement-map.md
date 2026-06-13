# Plan: Interactive Manuscript Provenance Movement Map

## Goal

Let a researcher pick a manuscript and see its **full provenance history laid out
on an interactive map** — production place, places it passed through, the people
who owned it, and its current home — with the movement drawn as arcs between
geo-located points and a synced time axis. Phase 2 adds a corpus view: filter
many manuscripts (by date / place / genre / owner) and watch their movement
animate by year.

This upgrades the current Provenance tab (a CSS horizontal strip with no map)
and the Geography tab (an SVG dot cloud, no real map) into a true
event-on-a-map view, following the [Mapping Manuscript Migrations](https://www.dhi.ac.uk/books/dhc2018/tracing-the-history-of-medieval-and-renaissance-manuscripts/)
model (CIDOC-CRM event-based, production → current location, time slider).

## Locked decisions (from the user)

1. **Owner geolocation** — enrich each owner that has a Wikidata QID with a place
   (residence P551 → work location P937 → death P20 → birth P19), resolve to
   coords, **cache** it. Coverage is partial and locations are biographical
   proxies → every owner point is flagged **inferred**.
2. **Chronology** — no ownership dates exist. Order stages: production date →
   owners sorted by birth year (proxy) → current holder (present). Every inferred
   stage is rendered **uncertain** (dashed arc, "inferred" badge), matching the
   pipeline's existing P1480/certainty conventions.
3. **Scope** — **Phase 1 = single-manuscript map.** Phase 2 = corpus filter view.
4. **Map library** — `leaflet` + `react-leaflet` (OSM tiles, markers, mature, DH
   standard) + a small Bézier-curve layer for arcs + a custom year slider. Not
   deck.gl (WebGL trips overkill for this corpus size).

## What data we already have (verified)

| Stage | Geo source | Date source | Status |
|---|---|---|---|
| Production place | KIMA lat/lon (`authority_matches.payload` kima_lat/kima_lon; `wgs84:lat/long` in graph) | MARC 008 → `hm:earliest/latest_possible_date` | ✅ solid |
| Related / significant places (751/752) | KIMA lat/lon | — (no per-place date) | ✅ solid, undated |
| Current holder | NLI = Jerusalem (hardcoded Q1218; lat 31.78 / lon 35.22) | present day | ✅ anchor |
| Owners (`hm:has_owner`) | **none today** → Phase-1 enrichment | birth/death in `authority_matches.payload` (`birth_year`/`death_year`) | ⚠️ new |

Key gaps Phase 1 fills: owners get a geographic point (Wikidata enrichment) and a
time position (lifespan proxy), both flagged inferred.

## Global caching via Heroku Redis (cross-cutting, required)

Heroku Redis (`heroku-redis:mini`, `REDIS_URL`, `rediss://`) is provisioned. The
existing two-tier `cache_lookup_or_call` (**Redis L1 → Postgres L2**, in
`app/pipeline/inference_cache.py`, client in `app/cache/redis_client.py`) is the
single caching primitive this feature uses. Everything expensive goes through it
— no parallel cache, no per-dyno-only memoisation for shared results.

Three caching layers, all Redis-backed and global (shared across requests +
dynos, surviving the WEB_CONCURRENCY=1 dyno restarts that cause cold loads):

1. **Owner→place enrichment** — `kind="wikidata.person_place"`, key `{qid}`.
   Cheap per call, reused across every manuscript an owner appears in and across
   runs/projects. (Already in the Phase-1 design.)

2. **Endpoint response cache** — wrap each new research endpoint's computed JSON
   in `cache_lookup_or_call`:
   - provenance-map → `kind="research.provenance_map"`, key
     `{run_ids_fingerprint, ms_uri}`.
   - manuscripts picker → `kind="research.manuscripts"`, key `{run_ids_fingerprint}`.
   - (Phase 2) movement → `kind="research.movement"`, key
     `{run_ids_fingerprint, filters}`; facets → `kind="research.movement_facets"`.
   Building the merged rdflib graph + SPARQL is the dominant cost (it is what
   wedged the wikidata-studio endpoint earlier); caching the JSON result makes
   repeat loads instant and removes the graph build from the hot path entirely on
   a hit.

3. **Graph-fingerprint invalidation** — a `research_graph_fingerprint(db, run_ids)`
   helper (new, in `research_graph.py`) returns a short hash of the run set + each
   run's RDF build/`updated_at` marker. It is the cache key component that makes
   stale results impossible: rebuild the RDF or re-run authority → fingerprint
   changes → all dependent Redis entries miss and recompute. No manual purging.

**Graceful degradation**: `get_redis()` already returns `None` when `REDIS_URL`
is unset (dev/CI), and `cache_lookup_or_call` then falls back to Postgres L2, and
to direct compute if neither is present. The feature must work with and without
Redis; Redis only makes it fast and global.

**Not cached in Redis**: the rdflib `Graph` object itself (not cheaply
serialisable) — we cache the *computed JSON rows*, not the graph. `load_merged_graph`
keeps its existing in-process memoisation as a same-request/same-dyno optimisation
beneath the Redis JSON cache.

## Data-integrity guards (cross-cutting, required — no false data on the map)

This feature *manufactures* geographic + temporal claims (owner locations from
biographical proxies, inferred ordering, movement arcs). A wrong QID or an
anachronistic placement would publish a false provenance claim on a scholarly
map. So it carries the same defense-in-depth the desktop Wikidata pipeline does
(CLAUDE.md Rules 23/26/29/30/40/49 §C). **Every guard below gets a unit test** in
`test_provenance_map_guards.py` — that file is the regression barrier; do not
weaken it. The governing rule: **when any signal is missing or fails a check, the
stop/edge is dropped or visibly marked uncertain — never silently fabricated.**

**Read-only invariant.** This is a pure projection over already-stored data. It
performs **zero writes** to Wikidata, authority tables, or the RDF graph. The only
external calls are *read* SPARQL lookups for owner coordinates, cached. No code
path here can mutate pipeline data (it cannot trip Rules 25/38 because it never
writes).

### A. Owner geolocation guards (the highest-risk path)

| # | Guard | Behaviour |
|---|---|---|
| A1 | **Approval gate** | Only owners whose authority match is **approved** (shared approval store, Rule 54) place a map point by default. Unapproved/pending → side list only, never a coordinate. A `?include_unapproved=true` preview is allowed but every such point is hard-flagged "unapproved". |
| A2 | **Confidence gate** | Even when approved, only `confidence ∈ {high, medium}` matches geolocate; `low` → side list. Mirrors Stage-3 verdict gating. |
| A3 | **Entity-type guard** | The owner QID must be `P31=Q5` (human). An owner match that resolves to an org/place/disambiguation page is rejected (mirrors Rule 29/30 nameType cross-validation). No point. |
| A4 | **Coordinate provenance honesty** | Record exactly which property produced the location (`P551` residence > `P937` work > `P20` death > `P19` birth) and surface it on the marker. A birth place is **not** "where they owned it" — the UI says "approx. location via P19", dashed, inferred. |
| A5 | **Anachronism guard** | If the owner's `birth_year` is **after** the manuscript's `latest_possible_date` (+ small buffer), the person cannot have owned it → drop the stop + log. Mirrors Rule 49 §C subject date guard. |
| A6 | **Coordinate sanity** | `lat ∈ [-90,90]`, `lon ∈ [-180,180]`, both finite, not the `(0,0)` null-island placeholder. Reject otherwise. |
| A7 | **No-coords → no point** | If Wikidata returns no `P625` for the chosen place, the owner gets **no** map point (side list only). Never invent or approximate coordinates. |
| A8 | **External-input validation** | The Wikidata SPARQL response is validated at the boundary before trust: QID regex, float-parseable coords in range, single unambiguous binding (multiple conflicting coords → abstain, per the Rule-40 "abstain on disagreement" pattern). |

### B. Place coordinate guards (production + significant places)

| # | Guard | Behaviour |
|---|---|---|
| B1 | **Real coords only** | Use `wgs84:lat/long` from the graph, else `kima_lat/kima_lon` from the match payload. Never fabricate or geocode from a name string. |
| B2 | **KIMA integrity** | Honour Rule 40: a KIMA place must carry a real `wikidata_id`; reject the VIAF-URI-leak shape. Coordinate-range validated (B/A6). |
| B3 | **Role honesty** | A place is labelled `production` only when the role actually says so (MARC 751 `$e` production roles / 260, per Rule 49). Other places are `significant_place` / `mentioned` — never upgraded to "production". |

### C. Chronology guards (inferred order is never shown as fact)

| # | Guard | Behaviour |
|---|---|---|
| C1 | **No fabricated years** | An owner with no `birth_year` gets **no** time position — it is not guessed. It renders as an undated stop, off the time axis. |
| C2 | **Range honesty** | Production dates show `earliest/latest` as a band; no fake midpoint is invented to make a tidy point. |
| C3 | **Inferred ordering is visibly inferred** | Lifespan-proxy order → dashed arcs + "inferred order" badge. The only *certain* anchors are production (dated) and current holder (present). |
| C4 | **Stable, documented tie-break** | Equal/again-missing years use a deterministic, documented order; the UI never implies precision the data lacks. |

### D. Arc / movement-semantics guards

| # | Guard | Behaviour |
|---|---|---|
| D1 | **Arcs = association, not proven route** | Legend + tooltips state an arc is an inferred *sequence of associated places*, not a proven physical journey. The current-holder arc especially is "eventually reached NLI", not a direct trip. |
| D2 | **No direction/time implied between two undated stops** | If both endpoints are undated, the edge is drawn neutral (no arrowhead, no animation position) rather than implying a temporal direction. |

### E. Cache-integrity guards

| # | Guard | Behaviour |
|---|---|---|
| E1 | **Fingerprint invalidation** | The graph fingerprint is in every cache key (see caching section) so a data fix upstream can never be masked by a stale cached map. |
| E2 | **Never cache errors as truth** | `cache_lookup_or_call` already skips `None`/empty results. Negative "no location" results get a **shorter** TTL so a later authority/Wikidata fix surfaces without manual purge. |

### F. Evidence-first auditability

Every map point carries a back-link to its evidence (MARC field + authority match
row + the exact Wikidata property used for the geo), reusing the existing
`EvidenceDrawer`. A reviewer can audit any dot to its source — nothing on the map
is unsourced.

## Conventions to reuse (do not reinvent)

- **Backend auth/graph**: `research_provenance.py` already does `ms` → events.
  Reuse `_require_viewer`, `load_merged_graph` / `_load_graph_or_404`,
  `_run_ids_for_project`, and the `research_queries.query_provenance` /
  `query_geography` SPARQL helpers. Register new routers in `app/main.py` next to
  the existing research routers.
- **Inference cache**: owner place enrichment routes through
  `app.pipeline.inference_cache.cache_lookup_or_call` (kind=`wikidata.person_place`)
  — same pattern as the existing VIAF/Wikidata enrichment, Postgres-backed,
  cross-request. Honour `MHM_NO_NETWORK`; never raise (graceful None).
- **Frontend**: panels live under `frontend/src/components/research/`, lazy-loaded
  and tab-registered in `routes/LinkedDataExplorer.tsx` (the `TABS` array). API
  wrappers in `frontend/src/api/research.ts`. Glass/theme styling per the existing
  panels.
- **KIMA coords**: a place match already carries `kima_lat`/`kima_lon` in
  `authority_matches.payload`; the merged graph carries `wgs84:lat/long` on place
  URIs. Prefer the graph for places already mapped; fall back to the payload.
- **Certainty UI**: mirror the dashed-line / "inferred" badge convention already
  used for uncertain attributions.

---

## Phase 1 — single-manuscript movement map

### 1.1 Backend: owner place enrichment (new)

`backend/app/pipeline/research_geo_enrich.py` (new):
- `async def owner_place(qid, *, db, user_id) -> {label, lat, lon, source_prop} | None`
  - One SPARQL `OPTIONAL` query against Wikidata for P551, P937, P20, P19 (in
    precedence order); for the first present, resolve its coords via `wdt:P625`
    (coordinate location). Single round-trip per owner.
  - Wrapped in `cache_lookup_or_call(kind="wikidata.person_place", query={qid})`.
  - Honours `MHM_NO_NETWORK`; returns `None` on any failure.
- Unit-testable pure parser `_parse_place_binding(rows) -> dict|None`.

### 1.2 Backend: provenance-map endpoint (new)

`backend/app/routers/research_provenance.py` — add
`GET /projects/{id}/research/provenance-map?ms=<uri>`:

Returns a geo+time-ordered stop list (superset of the existing timeline):

```jsonc
{
  "ms": "...", "ms_label": "...",
  "stops": [
    { "kind": "production", "label": "ʻAmrān (Yemen)", "uri": "...",
      "lat": 15.6, "lon": 43.9, "year": 1651, "year_earliest": 1651,
      "year_latest": 1651, "certain": true, "inferred_geo": false },
    { "kind": "owner", "label": "Yiḥyaʾ ben David", "uri": "...",
      "lat": 15.3, "lon": 44.2, "year": 1700, "certain": false,
      "inferred_geo": true, "geo_source": "P551",
      "birth_year": 1660, "death_year": 1730 },
    { "kind": "significant_place", "label": "...", "lat": .., "lon": ..,
      "year": null, "certain": false, "inferred_geo": false },
    { "kind": "current_holder", "label": "National Library of Israel",
      "lat": 31.78, "lon": 35.22, "year": null, "is_present": true }
  ],
  "edges": [ { "from": 0, "to": 1, "inferred": true }, ... ]   // chronological arcs
}
```

Logic:
- **Whole response wrapped in `cache_lookup_or_call(kind="research.provenance_map",
  query={fingerprint, ms_uri})`** — graph build + SPARQL + owner enrichment run
  only on a miss; a hit returns the cached JSON from Redis (→ Postgres) instantly.
- Production stop: reuse `query_provenance` production event + production place
  coords (graph `wgs84` → payload `kima_lat/lon` fallback).
- Owner stops: for each `hm:has_owner`, look up its `authority_matches` row for
  `wikidata_qid` + `birth_year`/`death_year`; call `owner_place(qid)` (itself
  Redis-cached). Resolve owners concurrently. Drop owners with no resolvable coords
  from the map layer but keep them in a side list.
- Significant/related places: KIMA-coorded places (751/752) attached to the ms.
- Current holder: NLI Jerusalem anchor (constant).
- Order stops by `(year or birth_year or +inf)`, production first, current holder
  last; build `edges` between consecutive geo-located stops, `inferred=true` when
  either endpoint is undated/inferred.

### 1.3 Backend: manuscript picker source

`GET /projects/{id}/research/manuscripts` (new, small): `[{ms_uri, label,
control_number, production_year}]` for the searchable picker. Derived from the
merged graph (one SPARQL over manuscripts + labels + production year). Reuses the
viewer guard + graph cache, and is itself wrapped in
`cache_lookup_or_call(kind="research.manuscripts", query={fingerprint})` so the
picker loads instantly after the first build.

### 1.4 Frontend: map deps + panel

- `frontend/package.json`: add `leaflet`, `react-leaflet`, `@types/leaflet`, and a
  small curve helper (`react-leaflet-curve` or a ~40-line inline quadratic-Bézier
  `Polyline` builder — prefer inline to avoid an unmaintained dep). Add Leaflet CSS
  import.
- `frontend/src/api/research.ts`: add `provenanceMap(projectId, msUri)` and
  `manuscripts(projectId)` typed wrappers + interfaces.
- New `frontend/src/components/research/ProvenanceMapPanel.tsx`:
  - **Manuscript picker** (searchable combobox over `/research/manuscripts`,
    replacing the raw-URI text box).
  - **Leaflet map**: OSM tiles; one marker per stop (color by kind: production /
    owner / significant / current); **curved arcs** between consecutive stops —
    solid for dated/certain, dashed for inferred; arrowheads in travel direction.
  - **Year slider + play button**: scrubbing reveals stops/arcs up to year `t`;
    play animates production → present. Undated stops (significant places, undated
    owners) attach at their inferred position and are styled dashed.
  - **Synced side timeline**: the existing vertical event list, highlighting the
    stop under the slider; click a stop → pan/zoom the map + open its evidence.
  - **Inferred legend**: explains dashed = inferred order/location, badges on owner
    markers showing the Wikidata place property used (P551/P19/…).
  - Empty/partial states: ms with only a production place still renders that one
    point + "no further provenance geo-located".
- Register a new **"Movement"** tab in `LinkedDataExplorer.tsx` (keep the existing
  Provenance strip; Movement is the map view). Lazy-load the panel.

### 1.5 Tests (Phase 1)

- Backend `pytest`:
  - `test_research_geo_enrich.py`: `_parse_place_binding` precedence (P551 > P937 >
    P20 > P19), no-coords → None, `MHM_NO_NETWORK` short-circuit, cache hit path.
  - `test_provenance_map.py`: production-only ms → 1 stop; ms with owners → ordered
    stops + inferred edges; current-holder always last; non-member → 403; unknown
    ms → empty-but-200.
  - `test_manuscripts_endpoint.py`: lists ms with labels + production_year; viewer
    guard.
  - `test_research_cache.py`: `research_graph_fingerprint` changes when a run's RDF
    marker changes (→ cache key changes); provenance-map second call is served from
    cache without re-running the graph build (assert via a compute spy/counter);
    Redis-absent path still returns correct data (Postgres-only / direct compute).
  - `test_provenance_map_guards.py` (**the integrity regression barrier**): one
    test per guard A1–A8, B1–B3, C1–C4, D1–D2, E1–E2 — e.g. unapproved owner → no
    coordinate (A1); `low` confidence → side list (A2); non-`Q5` owner QID rejected
    (A3); geo property precedence + label surfaced (A4); owner born after
    `latest_possible_date` dropped (A5); out-of-range / `(0,0)` coords rejected
    (A6); missing `P625` → no point (A7); ambiguous multi-coord binding → abstain
    (A8); fabricated-name geocoding never attempted (B1); VIAF-leak KIMA shape
    rejected (B2); non-production place never labelled production (B3); owner with
    no birth year has no time position (C1); no midpoint fabrication (C2); inferred
    edges flagged (C3); undated-pair edge has no direction (D2); negative-cache TTL
    shorter than positive (E2); **read-only assertion** — the module exposes no
    write/POST/SPARQL-update path (structural grep test, mirroring
    `test_safety_guards` structural tests).
- Frontend `vitest`: arc-builder (two points → Bézier control point), stop ordering
  + edge `inferred` flags, slider `revealedUpTo(year)` filter.
- Frontend Playwright `e2e/provenance-map.spec.ts` (mock backend per Rule W-19):
  pick a ms → markers + arcs render; drag slider → fewer/more stops; dashed arcs on
  inferred edges; click stop → map pans + evidence opens.

---

## Phase 2 — corpus movement view (after Phase 1 review)

- Backend `GET /projects/{id}/research/movement` with filters `from_year`,
  `to_year`, `place`, `genre`, `owner`, `source`: returns per-ms
  `{production: {lat,lon,year}, current_holder: {lat,lon}, label}` + a per-year
  aggregation for the slider. Reuses `query_geography` + facet filters.
- Backend `GET /projects/{id}/research/movement/facets`: distinct places, genres,
  owners, and the year range for the filter controls.
- Frontend: corpus mode in `ProvenanceMapPanel` (toggle single ⇄ corpus): smart
  filter bar + a single Leaflet map showing all matching production→current-holder
  arcs, animated by production year (play), marker clustering for dense regions.
- Tests mirror Phase 1 (filter isolation, year-window aggregation, e2e filter+play).

---

## Critical files

- **Reuse/extend (backend)**: `app/routers/research_provenance.py`,
  `app/pipeline/research_queries.py`, `app/pipeline/research_graph.py`
  (+ new `research_graph_fingerprint`), `app/pipeline/inference_cache.py`
  (`cache_lookup_or_call` — the one caching primitive), `app/cache/redis_client.py`
  (existing global Redis L1), `app/main.py` (router registration),
  `app/models/run.py` (AuthorityMatch payload).
- **New (backend)**: `app/pipeline/research_geo_enrich.py`; endpoints added to
  `research_provenance.py` (provenance-map + manuscripts) — Phase 2 adds
  `research_movement.py`.
- **Reuse/extend (frontend)**: `routes/LinkedDataExplorer.tsx` (tab),
  `api/research.ts`, existing panel styling.
- **New (frontend)**: `components/research/ProvenanceMapPanel.tsx` + arc/slider
  utils; `leaflet`/`react-leaflet` deps.

## Verification

1. Backend red→green per new test file, then full `pytest` green; every new
   endpoint enforces the viewer guard.
2. Frontend `vitest` (arc/order/slider units) + Playwright `provenance-map.spec.ts`
   (mocked) green.
3. Manual smoke against the dev/Heroku stack: pick a real ms (e.g. the Maimonides
   Mishneh Torah with `751 ʻAmrān (Yemen)`) → production point in Yemen, current
   holder in Jerusalem, an inferred owner arc if the owner resolves to a Wikidata
   place; slider animates production → present.
4. No regressions: existing Provenance + Geography tabs and the research/perm tests
   still pass.
4b. Data integrity: `test_provenance_map_guards.py` green (every A/B/C/D/E guard +
   the read-only structural assertion). Manually confirm on a real ms that an
   unapproved or anachronistic owner produces **no** map point, and that every
   inferred point is visibly flagged + traceable to evidence.
5. Performance + global caching: owner enrichment AND the whole provenance-map /
   manuscripts JSON responses route through the existing two-tier
   `cache_lookup_or_call` (Heroku Redis L1 → Postgres L2). A cold single-ms map
   completes well under the 30 s request window (few owners per ms, enriched
   concurrently); every subsequent load is a Redis hit (instant, shared across
   dynos, survives restarts). Verify a warm load issues no graph build / Wikidata
   call (compute-spy assertion + Heroku log check), and that changing a run's RDF
   flips the fingerprint and forces a recompute.

## Phasing / checkpoints

Ship **Phase 1** (single-manuscript map) and review before starting **Phase 2**
(corpus filter view). Each phase is independently mergeable behind its tests.
