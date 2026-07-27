# WikiProject Manuscripts — skill sources

Scraped / mirrored for eval-agent judge context (Rules W-104 / W-124).
Do **not** dump full wiki HTML into prompts; the structured pack in
`skill.json` is the only runtime source the judges see.

| Source | URL | Role |
|---|---|---|
| WikiProject hub | https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts | Scope, Data Model pointer, community goals |
| Data Model | https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model | Fingerprint, P31 classes, material/creation/content/housing properties |
| Tasks | https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Tasks | Conflation (work vs witness), illumination, DS import |
| Data access | https://www.wikidata.org/wiki/Wikidata:Data_access | Read/write etiquette (reconcile, rate limits) — write path, not rubric |
| MHM code contract | `docs/wikidata-manuscripts-data-model.md` | Hebrew/NLI application + hard gates (P50, P7416, P195) |
| HMO → Wikidata bridge | `backend/converter/wikidata/hmo_wikidata_pq_mapper.py` | Ontology local-name → public P/Q only |

**Last scrape review:** 2026-07-27 (Data Model page + curator upload
`Data_Model-0.md`). Refresh by re-fetching the Data Model page and updating
`skill.json` slices when WPM adds or retires properties; then bump `version`
and verdict schema salts (`w124_v1` → next).

**Rule W-124 evidence channels** (passed as `verify_evidence` on each
Wikidata Studio item, not only as skill text):

1. MARC projected slice (canonical control-number join)
2. VIAF (authority rows + P214)
3. Mazal / NLI (authority rows + P8189)
4. Existing Wikidata (`existing_qid` + authority Wikidata rows + optional live)
5. HMO Wikibase (`hmo_wikibase_id`, source URI, browseable Item:Q, P2888/P973)
