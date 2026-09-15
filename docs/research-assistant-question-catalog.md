# Research Assistant — Question Catalog

**Status:** user-facing guide, grounded in the MHM research proposal
(מהכרכים הגדולים ועד למסמכים הבודדים — Mapping Hebrew Manuscripts, A. Golberg)
and in the shipped tool surface of the Research Assistant
(`docs/architecture/blocks/research/agent.md`, Rule R26).

**How to read this:** each section maps one of the proposal's four research
questions to what you can ask the Research Assistant **today**, the flow it
runs, and what lands on the canvas. Questions marked ⚠ are **roadmap gaps**
— the system cannot answer them (fully) yet; they drive the next features.

Ask in Hebrew or English. Cite control numbers (`9900…`), QIDs (`Q…`), or
URIs whenever you have them — the agent then answers from records, not
guesses.

---

## RQ1 — LLM enrichment of unstructured MARC (the "הערות" fields)

*Extract scribes, owners, places, censorship, dedications and colophons from
free-text notes, and turn them into Wikidata-compatible entities.*

**What you can ask today:**

| Question (EN / עברית) | Flow | Canvas artifact |
|---|---|---|
| What did the AI extract from manuscript X's notes? (`9900…`) | `research_evidence` → MARC source + approval trail | table / answer |
| Which scribes did the pipeline find, and who approved them? | summary + authority matches per run | answer |
| Show the extraction evidence for scribe Y on MS X. | `research_evidence?uri=` — reverse `MS_<cn>` minting | answer |
| What is still unapproved and why? | authority review queue semantics | answer |

⚠ **Gaps:** free-text *search across notes* ("which manuscripts mention a
censorship act in their notes?") is not yet a tool — the notes live in MARC
and the RDF, but no cross-corpus note-search skill exists yet.

## RQ2 — What belongs in a Wikidata record for a Hebrew manuscript

*The WPM data-model contract: what we upload and why.*

| Question | Flow | Canvas artifact |
|---|---|---|
| Which properties do our uploaded items carry, and how many? | `wikidata_fetch_items` → `data_distinct(property)` | table + answer |
| Chart the types of links on our Wikidata items. | `show_link_types` | `link-types` chart |
| Show the live claims of item Q… (labels, claims, sitelinks). | `wikidata_entity` | answer |
| Did our item change on Wikidata since the upload? | `wikidata_entity` vs `wikidata-items` dataset | answer |
| Which of our items are works vs manuscripts vs persons? | `wikidata_uploaded_items` → `data_distinct(entity_type)` | table |

⚠ **Gaps:** reference-level provenance reports (which references back each
claim) are not surfaced as a tool yet.

## RQ3 — Verification: can the automatic metadata be trusted?

*The AI-verify spine: every stage judged by tier-1 models + curator review.*

| Question | Flow | Canvas artifact |
|---|---|---|
| What is the pass/fail mix of our Wikidata publication review? | Studio verify endpoints (via curator UI; ask for the digest) | answer |
| Which authority matches are approved vs rejected? | per-run authority data | table |
| Show me SHACL/validation issues before an upload. | HMO Studio verify (curator UI) | answer |

⚠ **Gaps:** the research agent cannot *launch* verify jobs — verification is
curator-UI-first by design (fail-closed writes). Ask for digests, not runs.

## RQ4 — Historical & cultural insight from LOD (geo, time, trends)

*The payoff question: migration, cultural centers, literary trends.*

| Question | Flow | Canvas artifact |
|---|---|---|
| Map the places mentioned in our manuscripts' Wikidata entities, with links to each entity. | `wikidata_fetch_items` → `show_wikidata_places` | `wikidata-places` interactive map |
| Where were our manuscripts produced, and where are they held now? | `show_movement_map` (corpus) | `movement-map` Leaflet map |
| Show one manuscript's full provenance chain on a map. | `show_movement_map` + control number | `movement-map` (single MS) |
| Which manuscripts moved from one place to another (excluding Israel)? | provenance edges via `research_provenance` / movement data | answer + map |
| Find manuscripts that shared a scribe or owner. | `research_cooccurrence` / `research_network` | table / network |
| How are two entities connected? | `research_shortest_path` (needs URIs) | path |
| What genres/topics dominate the corpus, and where? | `research_summary` + SPARQL templates | answer + tables |
| Rebuild the predicate/property list from our live claims. | `research_sparql` (wikidata source) → `sparql-results` | dataset + table |
| Export any canvas artifact as PDF / CSV / BibTeX / RIS. | `export_pdf` / `create_download_link` | download |

⚠ **Gaps:** cross-corpus *temporal trend lines* (topics/genres over centuries)
and *cultural-center evolution maps over time* are exactly the proposal's
RQ4 — the movement map covers single-MS chains and corpus production→NLI,
but aggregate "trend over time" charts are not built yet.

---

## One-round-trip packs

- **`wikidata_pack`** — refresh live claims, then place the link-type chart
  AND the place map in a single tool call. The fastest "give me the full
  Wikidata picture" ask: *"Refresh our Wikidata data and show me the links
  and the places map."*

## Canvas hygiene (what the agent can and cannot do)

- The canvas renders markdown as text and **never executes** HTML/JS — the
  agent is forbidden from hand-writing code artifacts; maps/charts come only
  from the skills.
- Interactive outputs available today: **Leaflet maps** (movement +
  place-mentions, with per-place manuscript link lists), **grouped bar
  charts** (link types), **tables** (datasets), **markdown notes**.
- Artifacts are versioned; the canvas dropdown lists every version, newest
  first. Downloads work from every artifact.
