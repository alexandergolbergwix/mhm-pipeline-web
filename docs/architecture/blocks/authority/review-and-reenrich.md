# Authority Enrichment — How it works: review & re-enrich

> Up: [Authority Enrichment](README.md)

**Re-enrich surfaces.** Three flavours share the same key/upsert logic:
`POST /runs/{id}/authority/re-enrich?skip_cache=` (synchronous),
`.../re-enrich/stream` (SSE, one `authority.entity` event per entity), and the
background `run_authority_re_enrich_job` (progress + cancel via the job
service). `POST /runs/{id}/authority/rebuild` only re-applies hardening guards
with full sibling context (no network); `POST .../matches/backfill-dates`
patches years in-place from stored ids without re-matching. All finish with
`finalize_authority_matches`: subject rows get
`linked_personality_mazal_id` from a same-record tag-100 author row, and the
Wikidata crosscheck re-runs with siblings.

**Curator review UI** (Rule W-31). `AuthorityTable` — 9 sortable columns
(Record · Entity · Role · Source · Conf. · Guards · AI verdict · Approved ·
Edit) with per-column `ColumnFilterPopup` (reused from extraction), header
free-text search, guard chips with `guardExplain(g)` tooltips, and
`onFilteredChange(ids)` reporting to `RunDetail`. `AuthorityDetailDrawer` —
640 px right drawer with Match / Confidence & Sources / Dates / AI Verdict
cards, its own edit dialog and MARC popup, and the **homonym picker**: when
`homonym_unresolved`, it lists `payload.homonym_candidates` (or live
`GET .../candidates`) with a Pick button per row.
`POST .../pick-candidate` validates the picked `mazal_id` against
`homonym_candidates`, sets it, clears abstain flags, stamps
`main_marc_tag="100"` + `personality_picked_by_curator`, sets confidence to
`medium`, and appends a `match.edited` project event.

**Auto-approve** (`runs.py:371-420`). `AuthorityAutoApproveRuleBuilder` posts
`{confidence_levels, sources, entity_kinds, min_source_count, require_ai_pass,
respect_ai_fail, match_ids?}` to `/auto-approve/preview` (debounced 350 ms
live count) then `/auto-approve`. Rows carrying any of
`AUTO_APPROVE_BLOCKED_GUARDS` — `homonym_unresolved`, `short_name_homonym`,
`mazal_subject_not_personality`, `viaf_date_mismatch`,
`cross_source_conflict`, `wikidata_disagrees`, `wikidata_crosscheck_fail` —
are never auto-approved regardless of the rule.

**Provenance-event places** (Rule W-32). `*_place` roles from 541$b/583$j fire
the KIMA→gazetteer chain like production places; `owner_place` /
`institution_place` in `research_geo_enrich.py` resolve owner residences and
institutional seats via SPARQL (P159→P276→P131→P625, abstaining on humans) for
the maps layer.
