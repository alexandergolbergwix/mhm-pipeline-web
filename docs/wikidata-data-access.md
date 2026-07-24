# Wikidata data access (MHM Studio contract)

**Source:** [Wikidata:Data access](https://www.wikidata.org/wiki/Wikidata:Data_access) (reviewed 2026-07-24)  
**Consumers:** Wikidata Studio reconcile, ownership checks, live upload (`wikidata_upload.py`, `reconciler.py`, `uploader.py`)

This page is the access-method map the code must follow. Prefer the **cheapest correct** API for each job; never put unnecessary load on Wikidata.

---

## 1. Access best practices (mandatory)

From Wikidata:Data access:

| Practice | MHM application |
|---|---|
| Good `User-Agent` | Set on every WDQS / Action API / EntityData client |
| `Accept-Encoding: gzip,deflate` | Default on HTTP clients |
| Honour **429** + `Retry-After` | Fail closed on reconcile outage — never “absent → CREATE” |
| Lowest sensible timeout (WDQS) | Bounded SPARQL timeouts in reconciler |
| Action API **`maxlag`** + [API etiquette](https://www.mediawiki.org/wiki/API:Etiquette) | Live writes via wikibaseintegrator / Action API |
| Stable Interface Policy | Treat APIs here as **not** guaranteed stable; pin behaviour in tests |

Data is **CC0**. Attribute Wikidata in user-facing surfaces (“Powered by Wikidata” / equivalent). Feed data-quality issues back to the community when possible.

---

## 2. Which API for which job

| Need | Use | Do **not** use |
|---|---|---|
| Dedup by **identifier / structured properties** (P3959, P214, P8189, label+author) | **Wikidata Query Service** SPARQL | Regex/FILTER text search on WDQS |
| Confirm a known QID still exists / not deleted | **MediaWiki Action API** `wbgetentities` or **Linked Data Interface** `Special:EntityData/Q….json` | Blind CREATE |
| Who **created** an item (first revision) | **Action API** `prop=revisions&rvdir=newer&rvlimit=1` + `list=usercontribs&uctype=new` | SPARQL alone |
| Edit / create items | **Action API** (wikibaseintegrator) or Wikibase REST API | Dumps / EventStreams |
| Fuzzy label search when IDs unknown | **CirrusSearch** / `wbsearchentities` | WDQS `FILTER(REGEX(…))` |
| Bulk offline analytics | **Dumps** | Hammering WDQS/API |
| Real-time change feed | EventStreams | Polling EntityData in a tight loop |

### Linked Data Interface (URI)

- Concept URI: `http://www.wikidata.org/entity/Q…`
- Data URL: `https://www.wikidata.org/wiki/Special:EntityData/Q….json`
- Optional `?flavor=dump|simple|full`, `?revision=…`
- Use when the QID is **already known** (post-reconcile verification)

### Wikidata Query Service

- Endpoint: `https://query.wikidata.org/sparql`
- Best for narrowly scoped characteristic queries (our manuscript/person/work reconcile)
- Scholarly graph is separate (`query-scholarly.wikidata.org`) — not used for MSS

### MediaWiki Action API

- Endpoint: `https://www.wikidata.org/w/api.php`
- Use for: editing, revision history, `wbgetentities` (≤50 ids/request), `wbsearchentities`
- Preferred for **current JSON of small entity batches**? Prefer EntityData for single known QIDs when cache helps; Action API for auth’d ownership + writes

### Wikibase REST API / GraphQL

- REST: growing replacement for Action API entity CRUD
- GraphQL: labels of linked entities, bulk GET, search by statement — optional future path
- Studio writes today stay on Action API via `WikidataUploader`

### Search / Vector / MCP / Enterprise / Dumps

- Search: simple text / `haswbstatement:` — optional secondary hint, never sole CREATE gate
- Vector / MCP / Enterprise: not on the Studio write path
- Dumps: offline only

---

## 3. MHM smart existence + write policy

Every Studio item goes through this gate **before** any CREATE/UPDATE (live and dry-run):

```text
1. Ledger hit?           → candidate QID
2. Type-aware SPARQL     → manuscript P3959→P217; person IDs (conflict-checked);
                           work label+author
3. Confirm QID alive     → Action API wbgetentities / EntityData
4. Ownership             → Action API first-revision + usercontribs (+ SPARQL exists)
5. Write decision
```

### Write decision matrix

| Existing QID? | Ownership | Curator accept foreign modify (bound to that QID)? | Action |
|---|---|---|---|
| No (confirmed absent) | — | — | **CREATE** (after validator) |
| Yes | **own** (acting token is first-revision author) | — | **UPDATE** allowed |
| Yes | **foreign** | **No** (default) | **SKIP / BLOCK** — never CREATE a duplicate, never silent modify |
| Yes | **foreign** | **Yes** (per-entity, QID-bound) | **UPDATE** allowed (audited exception; identity-conflict guards still apply) |
| Yes | **unknown** (no token / API fail) | — | **BLOCK** fail-closed for UPDATE; do not CREATE |

Defaults:

- User may **add** new entities only after smart non-duplication checks.
- User may **modify** only entities they created (token-proven), unless they explicitly accept each foreign QID.

Accept is stored on `WikidataItemOverride` as `accept_foreign_modify` + `accepted_foreign_qid` and must match the reconciled QID at upload time.

---

## 4. Code map

| Concern | Module |
|---|---|
| SPARQL reconcile | `backend/converter/wikidata/reconciler.py` |
| QID alive + ownership classify | `backend/app/pipeline/wikidata_existence.py` |
| Prepare / write policy | `backend/app/pipeline/wikidata_upload.py` |
| Live Rule-38 gates | `backend/converter/wikidata/uploader.py` (`_is_our_item`) |
| Per-item accept | `WikidataItemOverride` + Studio PATCH + drawer UI |
| HMO / project Wikibase → public P/Q | `backend/converter/wikidata/hmo_wikidata_pq_mapper.py` (Rule W-100) |
| Contract | this file + `docs/wikidata-manuscripts-data-model.md` |

---

## 5. References

- https://www.wikidata.org/wiki/Wikidata:Data_access  
- https://query.wikidata.org  
- https://www.wikidata.org/wiki/Special:EntityData  
- https://www.wikidata.org/w/api.php  
- https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual  
- https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy  
