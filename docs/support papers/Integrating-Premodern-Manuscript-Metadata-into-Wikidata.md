# Integrating Premodern Manuscript Metadata into Wikidata: A Case Study in Ontology Design and Linked Data Reuse

**Authors:** Rose A. McCandless & L. P. Coladangelo  
**Journal:** Journal of Open Humanities Data, 11: 69, pp. 1–12 (2025)  
**DOI:** [https://doi.org/10.5334/johd.431](https://doi.org/10.5334/johd.431)  
**Source PDF:** `Integrating Premodern Manuscript Metadata into Wikidata- A Case Study in Ontology Design and Linked Data Reuse.pdf`  
**Extracted for:** MHM Pipeline Web Wikidata Studio data-model work

---

## Abstract

This article presents Digital Scriptorium’s efforts to model and integrate premodern manuscript metadata into Wikidata, addressing challenges posed by inconsistent cataloging practices and the limitations of existing data models. Building on the WikiProject Manuscripts Data Model, the authors identified key gaps, proposed ontology refinements, and developed scalable workflows for crosswalking, reconciliation, and upload. The work shows how domain-informed ontology design can enhance interoperability, machine-actionability, and data reuse.

**Keywords:** ontology design; Wikidata; premodern manuscript metadata

---

## 1. Context and motivation

### 1.1 Linked Open Data in premodern manuscript studies

- Premodern manuscript metadata is often institutionally siloed, inconsistently structured, and uneven in descriptive granularity.
- Libraries, archives, and DH projects increasingly contribute manuscript metadata to Wikidata.
- **Digital Scriptorium (DS)** (`https://digital-scriptorium.org/`) is a North American consortium whose DS Catalog is a Wikibase union catalog of premodern manuscript holdings.
- DS ingests institutional datasets, enriches/reconciles them, and uploads into the DS Catalog to improve discoverability and reuse.

### 1.2 Challenges of integrating manuscript metadata into Wikidata

- Wikidata was not designed for manuscripts; its general-purpose schema struggles with:
  - artistic attribution
  - ambiguous production dates
  - multilingual titles in original script
- There is no universal manuscript metadata standard; cataloging practices vary widely.
- Bibliographic/MARC frameworks and AMREMM-style prose cataloging inhibit machine-actionability.
- Interoperability must be addressed at schema, record, and repository levels (crosswalks).

### 1.3 Motivation for revising and extending the WikiProject Manuscripts Data Model

- WikiProject Manuscripts (WPM) provides a recommended data model for material, place of production, scribe, language, and intellectual content.
- WPM DM is grounded in earlier Biblissima / Wikibase work, but was **not sufficient** for full DS Catalog richness.
- Gaps called out by DS:
  - nuanced **provenance chains**
  - complex **artistic attributions**
  - ambiguous / approximate **dating**
  - **multilingual titles**
- Goal: expand WPM DM + build scalable DS→Wikidata transformation/reconciliation/upload workflows.

Further motivations:

- Unstructured manuscript description lacks reusability; Wikidata enables SPARQL-scale handlists.
- Cyclical improvement loop: Wikidata scholarly additions → institutional review → DS Catalog refresh.

---

## 2. Dataset description

| Field | Value |
|---|---|
| Repository | DS Catalog Wikibase: `catalog.digital-scriptorium.org` |
| SPARQL / GitHub inputs | [DigitalScriptorium/ds-data `ds-to-wikidata`](https://github.com/DigitalScriptorium/ds-data/tree/20bacd98eda2e6e267f86696dfd36a5b831e1835/ds-to-wikidata) |
| Zenodo release | [doi:10.5281/zenodo.17435362](https://doi.org/10.5281/zenodo.17435362) (2025-10-24) |
| Formats | SPARQL-extracted CSVs → OpenRefine → Wikidata |
| Creation window | 2024-11-05 to 2025-08-08 |
| Language | Primarily English; multilingual values from institutions |
| License | CC BY 4.0 |

**Creators / roles (paper):** Coladangelo (SPARQL CSVs, reconciliation), McCandless (WPM↔DS mapping, OpenRefine schema, uploads), Blair (reconciliation/enrichment), Stephenson (additional Wikidata enrichment).

---

## 3. Method

### 3.1 Developing the DS-to-Wikidata crosswalk

Source schema = DS Catalog; target schema = WPM DM / Wikidata.

Descriptive categories used for mapping:

1. **Core identity** — e.g. instance of manuscript
2. **Material and production** — made from material, location of creation, inception
3. **Content** — language, exemplar of
4. **Creator / contributor roles** — scribe, illustrator, calligrapher
5. **Provenance** — owned by
6. **Housing / catalog** — collection / held by, catalog code

Alignment patterns observed:

| Pattern | Example |
|---|---|
| One-to-one | DS “instance of” → Wikidata **P31** |
| One-to-many (direct) | physical description → **P2048** height / **P2049** width / **P1104** pages/folios |
| One-to-many (qualifier) | DS top-level author → qualifier under **P1574** exemplar of |
| Many-to-one | production date as recorded + earliest/latest → **P571** (+ qualifiers) |

Direct material mapping cited: DS material authority → Wikidata **P186** (*made from material*).

Crosswalk documented in a spreadsheet and reused for OpenRefine automation.

### 3.2 Modelling complex metadata

#### Artistic attribution

- WPM/Wikidata assume a named **illustrator (P110)**.
- Many MSS are attributed to schools / followers / workshops (e.g. “Ghent-Bruges school”, “follower of the Master of the Dresden Hours”).
- **P1780** (*school of*) and **P1774** (*workshop of*) currently work mainly as **qualifiers of P110**, which is awkward when no illustrator item should be created.
- Desired: standalone attribution properties for school/workshop/follower.

#### Provenance

- **P127** (*owned by*) + **P11811** / **P11812** (beforehand/afterward owned by) + **P580** / **P582** (start/end time) support ownership chains.
- Missing clear pathways for **sale / transfer / auction** (e.g. Sotheby’s as selling agent ≠ owner).
- Ownership ≠ possession ≠ stewardship ≠ curatorial housing.

#### Paper’s recommended future properties / expansions

- Selling agent (bookseller / auction house distinct from owner)
- Expanded artistic attribution (school / follower / workshop as first-class)
- More granular physical structure: binding types/styles, layout (columns), foliation systems

### 3.3–3.4 Ontology refinement, workflow, reconciliation

Workflow:

1. Build DS↔WPM crosswalk
2. Reconcile controlled values (materials, places, scripts, centuries) via data dictionaries (Getty TGN/AAT ↔ Wikidata QIDs)
3. OpenRefine reconciliation (engine + manual review)
4. Create OpenRefine Wikidata schema from expanded WPM model (qualifiers + references)
5. Batch upload; sample review; scale

Institutional upload examples in the paper:

- Vassar College — multilingual titles / script modeling
- Rutgers University — many **Hebrew** manuscripts; multilingual titles
- Newberry Library — 700+ records; script reconciliation; batches of ~100 items

### 3.5 Ontological considerations for complex data

Core modelling principle (shared with WPM):

> The Wikidata **item is the physical manuscript object**. Intellectual contents are linked via **P1574** (*exemplar of*), not by treating the manuscript as the work itself.

Reason: every handwritten witness differs; the MS is a unique carrier of text.

**Multilingual / original-script titles (DS P13):**

- Prefer linking a work via **P1574**
- Record original-script / as-catalogued title with qualifier **P1932** (*object named as*)
- If no suitable work item exists, use fallback work value **Q234460** (*text*) + **P1932** title string  
  (paper footnote points to `Q234460`; OCR of the PDF sometimes misreads this as `Q2344602`)

---

## 4. Results and discussion

### 4.1 Overview

Outcomes:

1. Confirmed WPM baseline viability for core MS metadata
2. Identified critical modelling gaps in WPM DM / Wikidata
3. Built scalable DS→Wikidata workflows

As of August 2025: **>3,500** structured Wikidata manuscript items from **27** institutions (Saint Louis University, Vassar, Ohio State, Newberry, etc.).

Key limitation areas:

- ambiguous / multi-century production dates
- anonymous / collective artistic attribution
- sales and provenance events
- title complexity and language variation
- physical description and structure

### 4.2 Challenges in modelling humanities data

Manuscript description balances physical description, historical attribution, intellectual content, and codicology — often under heterogeneous institutional standards. Wikidata prefers point-specific, item-linked, reference-backed claims; mapping therefore requires epistemological translation (how much uncertainty to encode; when to mint new items).

### 4.3 Representing ambiguity and uncertainty

| Problem | Wikidata constraint | DS practice |
|---|---|---|
| Century / range dating (e.g. ca. 1375–1425) | **P571** wants discrete values; multiple centuries push “preferred” rank | Prefer **earliest/latest** qualifiers; avoid false preferred ranks |
| School / follower attribution | Hard to reduce to one creator | Qualify with **P1780** / **P1932**; keep references via **P248** |
| Competing scholarly interpretations | Prefer clean single values | Preserve ambiguity with qualified, sourced statements |

### 4.4 Multilingual titles and scripts

- Institutions disagree on assigned vs transcribed titles.
- Titles may be Latin, Arabic, Hebrew, etc.
- Wikidata supports multilingual labels / monolingual text, but not rich transliteration metadata.
- Strategy: keep original-script forms under **P1574 → P1932**; reference DS / institutional record.
- Gap: no standardized transliteration-scheme property → discovery pain for non-Roman scripts.

---

## 5. Implications / applications

- Domain-expert ontology design makes humanities metadata more interoperable and reusable.
- Crosswalk + OpenRefine schema is reusable by other Wikibase manuscript projects.
- Ontology work for manuscripts must be iterative and community-informed.
- Propose new properties where bibliographic practice and Wikidata diverge (selling agent, attribution, physical structure).

---

## Data accessibility

- GitHub inputs under Digital Scriptorium `ds-data` (see §2)
- License: CC BY 4.0

---

## Competing interests

None declared.

---

## Extracted modelling rules for MHM code

These are the paper’s actionable invariants for our Wikidata projection (see also `docs/wikidata-manuscripts-data-model.md`):

1. **Item = physical manuscript**; works attach via **P1574**, never **P50** directly on the manuscript for contained works’ authors (authors belong on the work, or as local/string evidence).
2. **Title-in-original-script** survives as **P1932** on **P1574** (or equivalent content statement), not only as item label.
3. **Unknown / unnotable work** → **P1574 = Q234460** (*text*) + **P1932** title string, rather than inventing a work QID.
4. **Date ranges** use **P571** + **P1319** / **P1326** (and uncertainty qualifiers); do not invent a preferred century.
5. **Provenance ownership** uses **P127** (+ **P580**/**P582**, **P11811**/**P11812**); do not conflate auction houses with owners.
6. **Illumination attribution** prefers named **P110** when justified; school/workshop/follower need qualifiers (**P1780**/**P1774**/**P1932**) and must not force fake person items.
7. Every projected claim should carry **references** (**P248** / **P854** / **P813**) back to catalog evidence.
8. Hebrew and other non-Latin titles are first-class: preserve original script for queryability.

---

## Selected references (from paper)

- Bauer, Bleier, & Sonnberger (2025) — LOD in manuscript studies
- Groß & Pellizzari di San Girolamo (2025) — modelling challenges
- Morlock et al. (2025) — WPM / Biblissima lineage
- Pass (2003) — AMREMM
- Cashion (2016) — manuscript as carrier of text
- Coladangelo & McCandless (2024a, 2024b)
- Steinova (2020) — handlist labour
- WikiProject Manuscripts Data Model: https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model

Full APA reference list remains in the PDF; this Markdown extract prioritises modelling content for implementation.
