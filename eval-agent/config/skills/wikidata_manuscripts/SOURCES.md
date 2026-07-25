# WikiProject Manuscripts — skill sources

Scraped / mirrored for eval-agent judge context (Rule W-104).
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

**Last scrape review:** 2026-07-25 (Data Model oldid ~2472606649).
Refresh by re-fetching the Data Model page and updating `skill.json`
slices when WPM adds or retires properties; then bump `version` and
verdict schema salts (`w104_v1` → next).
