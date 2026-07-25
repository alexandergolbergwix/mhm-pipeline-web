# HMO Wikibase Studio Item Rubric

You judge whether a **built HMO Wikibase item** (an RDF projection of one
Hebrew-manuscripts ontology instance) is safe for curator approval.

These items are **not** raw NER spans. They are typed scholarly entities
(manuscript, work, person, codicological unit, production event,
epistemology step, …) exported from a full RDF graph. Many items are
**derived** from a parent manuscript record and will not have their label
text appear verbatim in MARC.

Return JSON with:

| Field | Allowed values | Meaning |
|-------|----------------|---------|
| `name_ok` | yes / partial / no | Labels + descriptions are accurate and readable |
| `type_ok` | yes / partial / no | `entity_type` + `class_qid` match the claims |
| `role_ok` | yes / partial / no / n/a | Claims + SHACL issues acceptable (`n/a` for structural-only items) |
| `overall` | full / partial / fail | See table below |
| `reasoning` | string | 1–2 sentences; cite MARC when available |

## Critical rules (read first)

1. **`class_qid` is the project's HMO Wikibase ID**, not Wikidata. Never
   interpret `Q60` using wikidata.org — use the `entity_type` field
   (`CatalogStep`, `EvidenceStep`, `Codicological_Unit`, …).

2. **MARC context may be keyed by `control_numbers`**, not by whether the
   item label appears in MARC. When multiple control numbers are listed, the
   MARC block is a **union** across all linked manuscripts. When
   `control_numbers` is non-empty and the MARC block is populated, treat the
   item as **manuscript-scoped** and verify persons/works/places against that
   merged record's authors, title, subjects, notes, colophon, provenance, etc.

3. **Structural / epistemology entities** (`CatalogStep`, `EvidenceStep`,
   `EvidenceChain`, `Evidence`, `PhilologicalView`, paradigm individuals):
   - `role_ok = n/a` unless SHACL blocking issues exist.
   - `name_ok = yes` when labels/descriptions identify the step/chain and
     are not generic placeholders like `X in the Hebrew Manuscripts Ontology (HMO)`.
   - Do **not** fail solely because the label is a system identifier such as
     `CatalogStep 990000403370205171`.

3b. **`Codicological_Unit`** — English labels such as
   `Main codicological unit of manuscript 9900…` are intentional system
   labels. `name_ok = yes` when the description names the MS and content
   scope; do not downgrade for English CU wording alone.

3c. **`F2_Expression`** — short scholarly title in the Hebrew label is
   correct; manuscript scope and folio ranges belong in the description.
   `name_ok = yes` when the title matches MARC 245/505 content and the
   description is substantive (not the generic HMO fallback).

3d. **`E12_Production` / `E52_Time-Span` / `F27_Work_Creation` /
   `TransmissionWitness` / `TextTradition`** — English system labels such as
   `Production of MS 9900…`, `Production period 1460`, or
   `Witness of '…' in MS 9900…` are intentional. `name_ok = yes` when the
   label carries the MS control number (or period) AND the description is
   substantive (place, date, scribe, folio, or tradition scope). Do **not**
   downgrade for the `Production of MS …` wording alone; `role_ok = n/a`
   unless SHACL blocking issues exist.
   - **Empty-production addendum:** when a `E12_Production` description states
     that the catalog record carries **no** production place/date/scribe
     (e.g. `… production place and date are not recorded in the catalog
     record.`), that IS substantive — it is an honest negative finding, not a
     placeholder → `name_ok = yes`. Only the exact old template
     `Production event for manuscript {cn}.` (which merely repeats the label)
     is `partial`.

3e. **`E74_Group`** (organizations / collections, e.g. a named library or a
   `Sassoon` collection) — `name_ok = yes` when the English org name is
   present and the description carries manuscript linkage. Never expect a
   Hebrew personal-name format for an organization; `role_ok` follows the
   org's custody/ownership role, not authorship.

3f. **Person name order + subject-heading persons.**
   - A MARC-heading `Surname, Given` order (`נשיא, דוד בן אהרן`) is the
     scholarly-standard inverted form → do **not** downgrade `name_ok` for the
     order alone.
   - A **subject-heading person** (`E21_Person` whose description says
     `Subject heading (person) … from MARC 600 …`) needs no biographical
     substance — the controlled heading + manuscript linkage is complete →
     `name_ok = yes`.

3g. **Generic manuscript titles + controlled-vocabulary terms.**
   - A manuscript (`F4_Manifestation_Singleton`) whose `en` label carries the
     shelfmark (`Jerusalem, NLI, {shelfmark}`) or whose `he` label carries the
     shelfmark in parentheses is sufficiently identified even when the bare
     `he` title is a single generic word (`תורה`, `תכלאל`) → `name_ok = yes`.
   - A `SubjectType` / `E53_Place` that is a controlled-vocabulary term with a
     description carrying manuscript linkage (`Subject heading … from MARC …`,
     `Place '…', {role} location of manuscript …`) is complete →
     `name_ok = yes`; do not require biographical/gazetteer prose.

4. **Generic fallback descriptions** (`… in the Hebrew Manuscripts Ontology (HMO)`)
   without substantive content → `name_ok = partial` at best; `fail` when the
   item is a primary scholarly entity (Work, Expression, Person, Manuscript).
   When `marc_context` is empty but `control_numbers` are present, the linked
   manuscripts may be outside the current run's MARC slice — judge from labels,
   descriptions, and claims instead of requiring MARC verification.

5. **Malformed labels** (unbalanced quotes, trailing punctuation inside quotes,
   doubled backslashes, `und` language codes) → `name_ok = partial` or `no`.

6. **Blocking SHACL** (`Violation` / `Error` in `shacl_issues`) → `role_ok = no`,
   `overall = fail`, regardless of labels. When `shacl_issues` is **empty**,
   do **not** set `role_ok = no` or mention hypothetical validation failures.

## `overall` computation

| name_ok | type_ok | role_ok | overall |
|---------|---------|---------|---------|
| yes | yes | yes / n/a | full |
| yes | yes | partial | partial |
| yes | partial | yes / n/a | partial |
| partial | yes | yes / n/a | partial |
| partial | any | any | partial |
| no | any | any | fail |
| any | no | any | fail |
| any | any | no | fail |

Tiebreaker: prefer `fail` over `partial` when both apply.

## Per-entity guidance

| entity_type | type_ok | name_ok hints |
|-------------|---------|---------------|
| `F4_Manifestation_Singleton` / manuscript | Match title/shelfmark in MARC | Hebrew title from MARC 245 |
| `F1_Work` / `F2_Expression` | Match contained titles | Title may come from 245/505 |
| `Codicological_Unit` | Unit of a known MS | Description should mention MS + content/folios |
| `E21_Person` / `E74_Group` | Person/org | Name in authors/contributors/subjects/colophon |
| `E53_Place` | Place | production place / subjects / provenance |
| `E12_Production` / `E52_Time-Span` / `F27_Work_Creation` | Production/date/creation event | System label with MS/period + substantive description OK (rule 3d) |
| `TextTradition` / `TransmissionWitness` | Philological transmission | System label with MS + tradition scope OK (rule 3d) |
| `CatalogStep` / `EvidenceStep` / `EvidenceChain` | Structural | System label OK; generic-only description is partial |

Be conservative on **claims** (do not invent P/Q statements), but do not
require every derived entity label to be a MARC substring.

## WikiProject Manuscripts + HMO→Wikidata skill (injected below)

Every prompt includes a compact **SKILL** block from
[WikiProject Manuscripts](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts)
/ [Data Model](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model)
plus an **HMO → public Wikidata** projection checklist.

- `class_qid` remains a **project Wikibase** id — never interpret it as
  wikidata.org.
- For manuscript / person / work / place entities, also ask: *would this
  item project cleanly to public Wikidata under WPM?* Fail or partial when
  claims would imply P50-on-manuscript, wrong illuminated/palimpsest P31,
  folio counts as P7416, or treating a project Q-number as a Wikidata QID.
- Structural / epistemology entities stay HMO-only; do not demand WPM
  manuscript fingerprint completeness for them.
