# Research Surface — Skills

> Up: [Research Surface](README.md)

### Skill: add a new map stop kind

1. Emit it in `research_provenance_map.build_provenance_map` (and, if it is a
   provenance-event type, add it to `_TYPED_EVENT_KINDS` at
   `research_provenance_map.py:181` and make sure ingest populates
   `record["provenance_events"]` — see Rule W-32).
2. Include it in `corpus_movement._extract_corpus_item` `event_places` if it
   should appear on the corpus map / place facet.
3. Add the literal to `MapStopKind` in `frontend/src/api/research.ts` and to
   `KIND_COLOR` + `KIND_LABEL` in `ProvenanceMapPanel.tsx` (both the marker
   loop and the `Legend`).
4. Extend `backend/tests/test_provenance_map_guards.py` with a
   guard case (coords-missing → skipped; date-missing → undirected edge).

### Skill: add a new pre-defined analytics query

1. Write a pure `query_*` function in `research_queries.py` — takes a graph,
   returns JSON-serialisable data, catches its own exceptions, returns an
   empty shape on failure. Use `_INIT_NS` and `_label_map`.
2. Add a `GET /projects/{id}/research/<name>` endpoint in `research.py`
   calling it via `_load_or_404` + `asyncio.to_thread`.
3. Add the typed client function in `frontend/src/api/research.ts`, a lazy
   panel in `frontend/src/components/research/`, and a tab in
   `LinkedDataExplorer.tsx`.
4. If it is expensive, cache per R10 with a content fingerprint (see
   `_summary_fingerprint`).

### Skill: add a saved query / new SPARQL backend

- Saved queries are plain rows (`app/models/saved_query.py`) — no new "type"
  concept; the `params` JSON column carries any per-query knobs. CRUD is in
  `saved_queries.py`; viewer reads, editor+ writes.
- A new SPARQL backend goes in `linked_data_explorer.py`: reuse
  `_validate_query`, `_sparql_json_to_response`, the 30 s timeout, and map
  transport errors to 408/502 — never leak a raw exception.

### Skill: debug missing map points

1. Check the response's `dropped` list first — every gated owner carries a
   reason (`unapproved`, `low_confidence`, `anachronism`, `no_location`).
2. Place stop missing → the authority match has no valid `kima_lat`/`kima_lon`
   in `payload` (Rule W-23/W-28 pipeline) or the place text doesn't fuzzy-match
   `entity_text` (`_event_coords`).
3. Owner stop missing → probe `owner_place(qid)` / `institution_place(qid)`
   manually; remember A8 abstains on conflicting coordinates and the result is
   cached (`kind="wikidata.person_place"`) — use `skip_cache=True` when
   re-testing.
4. Whole map stale after a curator action → check `_project_fingerprint`
   includes the mutated field; the Redis-cached corpus items only invalidate
   when the fingerprint changes.

### Skill: debug a wrong/empty research tab

1. Confirm the TTL exists: the tab 404s with "build the graph first" when no
   `rdf_artifacts` row exists for any run.
2. Summary shows zeros but other tabs work → the coherence gate should
   self-heal; if not, the cached row predates `_SUMMARY_ALGORITHM_VERSION` —
   bump it in `research.py:50` to force a global key rotation.
3. Geography empty → places lack `wgs84:lat/long` in the TTL; rebuild RDF so
   KIMA coords flow through (Rule W-32 note: DB-plane maps benefit
   immediately, the RDF-plane Geography tab needs a rebuild).
