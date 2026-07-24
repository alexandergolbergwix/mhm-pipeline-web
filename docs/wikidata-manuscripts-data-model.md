# Wikidata Manuscripts Data Model (code contract)

**Status:** implementation source of truth for MHM Pipeline Web Wikidata Studio projection  
**Primary community source:** [Wikidata:WikiProject Manuscripts/Data Model](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model) (scraped 2026-07-24)  
**Secondary source:** McCandless & Coladangelo (2025), Digital Scriptorium → Wikidata case study — extract at [`docs/support papers/Integrating-Premodern-Manuscript-Metadata-into-Wikidata.md`](support%20papers/Integrating-Premodern-Manuscript-Metadata-into-Wikidata.md)  
**Code mirror:** `backend/converter/wikidata/property_mapping.py` (+ Studio builders / validators)  
**HMO → public P/Q bridge:** `backend/converter/wikidata/hmo_wikidata_pq_mapper.py` (Rule W-100)  
**Write policy:** `docs/wikidata-data-access.md` (Rule W-99)

This document is the **full data model the code should use**. Constants in `property_mapping.py` must stay consistent with the tables below. Live P/Q pages must be re-checked before adding or changing a constant (Rule W-26).

---

## 0. Entity graph (what an item is)

```text
                    ┌─────────────────────────┐
                    │  written work (Q47461344)│
                    │  (+ author P50 on WORK) │
                    └────────────▲────────────┘
                                 │ P1574 exemplar of
                                 │   qualifiers: P958 section/folio,
                                 │               P1932 object named as,
                                 │               P50/P2093 only when
                                 │               modelling work-level
                                 │               authorship on the link
┌────────────────────────────────┴────────────────────────────────┐
│  MANUSCRIPT ITEM (physical object)                              │
│  P31 ∈ {Q87167, Q48498, Q274076, Q30103158, …}                  │
│  material / production / housing / provenance / identifiers     │
└─────────────────────────────────────────────────────────────────┘
        │ P11603 transcribed by (scribe)
        │ P110 illustrator / P11105 annotator / …
        │ P127 owned by  ·  P195 collection  ·  P217 inventory number
```

**Hard rule (WPM + DS + MHM Rule W-30):** the manuscript item is the **physical carrier**. Contained texts are **P1574 → work**. Do **not** put work authors as **P50 on the manuscript**.

---

## 1. Fingerprint (label / description / alias)

From WPM DM:

| Field | Rule |
|---|---|
| **Label priority** | (1) familiar trivial name → (2) canonical catalog code for a manuscript group → (3) library signature (city · institution · fonds/record set · shelfmark) in least-ambiguous form |
| **Description** | Specific; do not repeat the label. May summarise contents, illuminations, palimpsest status, date, commissioner. Mass imports may use generic but distinguishable descriptions. |
| **Aliases** | Full/abbrev signatures not used as label; alternate catalog codes; obsolete trivial names; edition **sigla** |

### MHM Hebrew / NLI application

| Preference | Value |
|---|---|
| Default label | Hebrew catalog title when usable; else shelfmark / control-number signature |
| English label | Only when trusted catalog romanization exists (do not machine-invent) |
| Alias | Shelfmark, NLI catalog id, alternate headings |
| Description | Holder + date/place when known; never invent NLI ownership (Rule W-75 / W-82) |

---

## 2. Allowed instance classes (`P31`)

### Preferred (WPM)

| QID | Label | When |
|---|---|---|
| **Q87167** | manuscript | Default for any manuscript item |
| **Q30103158** | manuscript fragment | Single leaves / scraps (WPM: use under review) |
| **Q48498** | illuminated manuscript | Only with **confirmed** illumination evidence (MHM Rule W-73: MARC “Illustrated works” alone is **not** enough) |
| **Q274076** | palimpsest | Reused scraped support (**never** Q179808 Palme d'Or) |
| **Q1641020** | palm-leaf manuscript | Palm-leaf tradition |

Optional additive class cited by WPM: **Q19602268** chained book.

### Discouraged as primary `P31` (WPM)

Do not use as the main class in place of manuscript:

| QID | Label (approx.) | Why discouraged |
|---|---|---|
| Q213924 | codex | Form subclass noise; prefer Q87167 |
| Q571 | book | Printed book / facsimile path only when explicitly printed |
| Q113016548 | (manuscript-related subclass) | Taxonomy tangle |
| Q95065857 | (manuscript-related subclass) | Taxonomy tangle |
| Q284465 | lectionary | Prefer genre (`P136`) over replacing `P31` |

MHM may still emit **Q571** for an explicit **printed facsimile** semantic subtype (Rule W-79), never as a silent default.

### Palimpsest scripting (WPM)

On the **same** manuscript item, qualify lower vs upper script with:

| Qualifier | Value |
|---|---|
| `P518` applies to part | **Q122901270** lower script |
| `P518` applies to part | **Q122901275** upper script |

---

## 3. Property statements for manuscripts (WPM core)

### 3.1 Material / physical

| PID | Label | Datatype | Notes / values |
|---|---|---|---|
| **P31** | instance of | item | See §2 |
| **P186** | made from material | item | Support first: **Q125576** papyrus, **Q226697** parchment, **Q378274** vellum, **Q11472** paper; also ink/binding/cover when known |
| **P1104** | number of pages | quantity | WPM: **number of folia**. MHM: use unit **Q107256474** (*leaf*) for folio counts. **Do not** use **P7416** as a count |
| **P5816** | conservation status | item | See §3.1.1 |
| **P2048** | height | quantity | Whole page, not text block |
| **P2049** | width | quantity | Whole page, not text block |

#### 3.1.1 Condition vocabulary (`P5816`)

| QID | Label |
|---|---|
| Q56557591 | preserved |
| Q20734200 | not completed |
| Q107531416 | mildly damaged |
| Q106379705 | damaged |
| Q56556915 | demolished or destroyed |
| Q106959824 | unlocated, probably destroyed |
| Q66890153 | unknown preservation status |
| Q61962974 | disassembled |
| Q75505084 | restored (optional) |

Qualifiers: `P585` point in time; or `P580`/`P582` for dated condition spans.

#### 3.1.2 Missing in WPM (do not invent public claims yet)

- page/folio numbering scheme
- quire organisation / number of quires
- ruling type
- text-area size
- book format (dimensions already cover exact size)
- watermarks (paper)

DS paper agrees these need future properties / richer modelling.

### 3.2 Creation / agents

| PID | Label | Notes |
|---|---|---|
| **P571** | inception | Creation time; ranges via **P1319** earliest / **P1326** latest; uncertainty via **P1480** (e.g. circa) |
| **P1071** | location of creation | Place of production |
| **P11603** | transcribed by | Scribe(s). WPM notes overlap with **P6819** calligrapher — prefer **P11603** for copyists |
| **P11105** | annotator | Annotator(s) |
| **P110** | illustrator | Named illuminator only when item-worthy |
| **P9260** | music transcriber | Musical notation hand |
| **P9302** | script style | Value type: script-style items (see Q118872183) |
| **P88** | commissioned by | Patron / commissioner |

DS extensions for attribution nuance (qualifiers, not fake persons):

| PID | Label | Role |
|---|---|---|
| **P1780** | school of | Qualifier on artistic attribution |
| **P1774** | workshop of | Qualifier on artistic attribution |
| **P1932** | object named as | As-catalogued / original-script string |

### 3.3 Content

| PID | Label | Notes |
|---|---|---|
| **P407** | language of work or name | Qualify with **P518** when language applies to a part |
| **P1574** | exemplar of | Target: **Q47461344** written work (or known work QID). Folio/section: **P958**. Title as in MS: **P1932**. Unknown work: **Q234460** (*text*) + **P1932** |
| **P136** | genre | Intended/actual use, e.g. lectionary, book of hours — evidence-gated in MHM |
| **P18** | image | Illustration / representative image |
| **P12041** | type of musical notation | When present |

MHM content extras (Studio / NLI corpus):

| PID | Label | Notes |
|---|---|---|
| **P1476** | title | Manuscript / work title when appropriate; script-aware |
| **P921** | main subject | Evidence-gated subjects only (Rule W-72) |
| **P282** | writing system | e.g. Hebrew alphabet **Q33513** |
| **P1922** / **P3132** | first / last line | Incipit / explicit when catalogued |
| **P1684** | inscription | Colophons / scribal notes — catalog-note markers filtered (Rule W-72) |
| **P7535** | scope and content | Archival summary only when appropriate (not ordinary MS notes) |

### 3.4 Stemmatic / editorial

| PID | Label | Notes |
|---|---|---|
| **P361** | part of | Manuscript group / family (WPM may prefer a dedicated “part of manuscript group” later) |
| **P144** | based on | Exemplar(s) used as model; qualify with **P518** |
| **P4969** | derivative work | Apographa / copies; qualify with **P958** |
| **P747** | has edition or translation | Editions that used this MS (WPM may prefer “used in edition”) |

### 3.5 Catalog, provenance, housing

| PID | Label | Notes |
|---|---|---|
| **P127** | owned by | Ownership chain; **P580**/**P582**; current owner **preferred rank**. May equal `P195` |
| **P195** | collection | Holding collection/institution; dated with **P580**/**P582**; current **preferred**. MHM: only verified holder QID (no default NLI) |
| **P12095** | fonds | Fonds within institution; same ranking/date pattern |
| **P217** | inventory number | Shelfmark (short vs scholarly long form TBD by community; MHM uses catalog shelfmark) |
| **P528** | catalog code | With qualifier **P972** catalog |
| **P373** | Commons category | Wikimedia Commons |
| **P953** | work available at URL | Digital copy URL |

DS / MHM provenance extras:

| PID | Label | Notes |
|---|---|---|
| **P11811** | beforehand owned by | Ownership-chain qualifier |
| **P11812** | afterward owned by | Ownership-chain qualifier |
| **P1028** | donated by | Gift events when evidenced |
| **P793** | significant event | Only when event modelling is justified; sales/auctions still under-specified in Wikidata |

**Do not** model auction houses as `P127` owners (DS gap: needs selling-agent property).

---

## 4. Qualifiers & ranks (shared toolkit)

| PID | Label | Typical use |
|---|---|---|
| **P518** | applies to part | Language/script/part scope; palimpsest lower/upper |
| **P958** | section, verse, paragraph, or clause | Where a work appears in the MS (prefer over P7416 for content location) |
| **P7416** | folio(s) | **String citation qualifier only** — never a folio *count* |
| **P580** / **P582** | start / end time | Ownership, housing, condition spans |
| **P585** | point in time | Single dated condition / event |
| **P1319** / **P1326** | earliest / latest date | Inception ranges |
| **P1480** | sourcing circumstances | circa / presumably / etc. |
| **P1932** | object named as | As written / original script / catalog string |
| **P972** | catalog | Qualifier on catalog codes |
| **P5102** | nature of statement | hypothesis / dubious (MHM epistemology) |
| **P887** | based on heuristic | Inferred claims |
| **P3831** | object has role | Role on inscriptions / agents when needed |

Ranks: current housing/owner = **preferred**; historical = **normal**; contested = consider **deprecated** + `P2241` only with evidence.

---

## 5. References (every public claim)

| PID | Label | Use |
|---|---|---|
| **P248** | stated in | Catalog / DS / bibliography item |
| **P854** | reference URL | Catalog permalink |
| **P813** | retrieved | Retrieval date |

MHM: NLI/Ktiv catalog pages and HMO Wikibase URIs are valid reference targets; **P2888** exact match bridges to HMO when mapped.

---

## 6. External identifiers for manuscripts (WPM table)

Community manuscript ID properties listed on the WPM page:

| PID | Label |
|---|---|
| P1577 | Gregory-Aland-Number |
| P1948 | BerlPap ID |
| P3702 | Catalogue of Illuminated Manuscripts ID |
| P3768 | Medieval Libraries of Great Britain ID |
| P4752 | Manus Online manuscript ID |
| P6108 | IIIF manifest URL |
| P7989 | Mirabile manuscript ID |
| P8532 | Trismegistos text ID |
| P9015 | Medieval Manuscripts in Oxford Libraries manuscript ID |
| P10236 | Initiale ID |
| P10481 | Mapping Manuscript Migrations manuscript ID |
| P12042 | Diktyon ID |
| P12109 | Catenae Catalogue ID |
| P12116 | Rahlfs number |
| P12131 | cagb manuscript ID |
| P12207 | BnF archives and manuscripts ID |
| P13100 | Innovating Knowledge manuscript ID |

### MHM / NLI identifiers (required for this corpus)

| PID | Label | Source |
|---|---|---|
| **P3959** | NNL item ID | MARC 001 / NLI bibliographic id (primary manuscript reconcile key) |
| **P8189** | J9U entity ID | NLI authority / entity id (persons/places; not MS shelf reconcile) |
| **P214** | VIAF cluster ID | Persons (and places when present) |
| **P1566** | GeoNames ID | Places |
| **P973** | described at URL | Catalog description page |
| **P6108** | IIIF manifest URL | When available |
| **P2888** | exact match | HMO Wikibase / ontology URI bridge |
| **P5008** | on focus list of Wikimedia project | WikiProject Manuscripts tagging when used |

---

## 7. Related entity types (non-manuscript items)

### 7.1 Works

| Field | Contract |
|---|---|
| `P31` | **Q47461344** written work (or more specific work class when verified) |
| Authors | **P50** on the **work**, or **P2093** author name string when no safe person QID |
| Labels | Exact verified aliases only; preserve Hebrew gershayim |
| Link from MS | MS `--P1574-->` work |

### 7.2 Persons

| Field | Contract |
|---|---|
| `P31` | **Q5** human (never corporate-as-human) |
| Identifiers | P214, P8189, … |
| Roles on MS | scribe **P11603**, illustrator **P110**, annotator **P11105**, commissioner **P88**, owner **P127** — **not** P50-on-MS for work authorship |

### 7.3 Places / organizations

| Field | Contract |
|---|---|
| Creation place | **P1071** |
| Significant place | **P7153** (associated, not creation) |
| Collection / holder | **P195** with verified org QID |
| Place IDs | keep Wikidata vs project-Wikibase namespaces distinct (Rule W-83) |

---

## 8. DS paper extensions (adopted modelling policies)

From McCandless & Coladangelo (2025), treated as **policy** for MHM:

1. Crosswalk categories: identity · material/production · content · creator roles · provenance · housing/catalog.
2. Physical description fields split into discrete PIDs (height/width/extent).
3. Authors of contained texts are modelled on the **work** side of **P1574**, not as manuscript `P50`.
4. Original-script titles → **P1932** under content links.
5. Unknown work → **Q234460** + **P1932**, fail closed on fuzzy work QIDs.
6. Date ambiguity preserved (earliest/latest; no false preferred century).
7. School/workshop attribution uses qualifiers; do not mint unsupported person items.
8. Ownership ≠ sale agent; leave sales unmapped until a selling-agent property exists.
9. Always attach references to institutional / DS / NLI evidence.

---

## 9. MHM projection profile (what builders emit)

Minimal viable manuscript item:

| Priority | Statements |
|---|---|
| Required | `P31`, identity id (**P3959** or equivalent), label |
| Strongly expected | `P195` **or** honest description without fake holder; `P217` shelfmark when known |
| Content | ≥1 `P1574` when work evidence exists; else no fabricated work |
| Production | `P571` / `P1071` / `P11603` when evidenced |
| Access | `P973` / `P953` / `P6108` when URLs exist |
| Focus | `P5008` → WikiProject Manuscripts when tagging is enabled |

Fail-closed (validator / upload guards):

- No `P50` on `entity_type=manuscript`
- No `P7416` quantity counts
- No known-bad `P31` (e.g. Q179808)
- No default `P195=Q188915` without verified holder evidence
- No `P31=Q48498` from weak illustration prose alone
- Block ERROR-severity `validate_item` issues before write

---

## 10. Code mapping checklist (`property_mapping.py`)

| Concern | Constant(s) |
|---|---|
| Core MS class | `Q_MANUSCRIPT`, `Q_ILLUMINATED_MANUSCRIPT`, `Q_PALIMPSEST`, `Q_MANUSCRIPT_FRAGMENT` |
| Work / person | `Q_WRITTEN_WORK`, `Q_HUMAN` |
| Extent | `P_NUMBER_OF_PAGES` + `Q_LEAF_UNIT`; `P_NUMBER_OF_FOLIOS` qualifier-only |
| Content link | `P_EXEMPLAR_OF`, `P_FOLIO` (P958), `P_OBJECT_NAMED_AS` |
| Scribe | `P_TRANSCRIBED_BY` |
| NLI ids | `P_NLI_CATALOG_ID` (P3959), `P_NLI_J9U_ID` (P8189) |
| Provenance chain | `P_OWNED_BY`, `P_BEFOREHAND_OWNED_BY`, `P_AFTERWARD_OWNED_BY` |
| Epistemology | `P_SOURCING_CIRCUMSTANCES`, `P_NATURE_OF_STATEMENT`, `P_BASED_ON_HEURISTIC` |

When adding a PID/QID from this model into code:

1. Open the live Wikidata Property/Item page.
2. Confirm label, datatype, and constraints.
3. Add constant + unit test / validator guard if removing a wrong ID.
4. Update this document in the same change.

---

## 11. External references

- WPM Data Model: https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model
- FactGrid manuscript model: https://database.factgrid.de/wiki/FactGrid:Data_model_for_manuscripts
- Biblissima metadata: https://github.com/biblissima/bibma-metadata
- DS paper extract: [`docs/support papers/Integrating-Premodern-Manuscript-Metadata-into-Wikidata.md`](support%20papers/Integrating-Premodern-Manuscript-Metadata-into-Wikidata.md)
- TEI MS description: https://www.tei-c.org/release/doc/tei-p5-doc/en/html/MS.html
- CIDOC CRM / CRMtex: https://www.cidoc-crm.org/
