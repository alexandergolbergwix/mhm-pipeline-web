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
   item label appears in MARC. When `control_numbers` is non-empty and the
   MARC block is populated, treat the item as **manuscript-scoped** and
   verify persons/works/places against that record's authors, title,
   subjects, notes, colophon, provenance, etc.

3. **Structural / epistemology entities** (`CatalogStep`, `EvidenceStep`,
   `EvidenceChain`, `Evidence`, `PhilologicalView`, paradigm individuals):
   - `role_ok = n/a` unless SHACL blocking issues exist.
   - `name_ok = yes` when labels/descriptions identify the step/chain and
     are not generic placeholders like `X in the Hebrew Manuscripts Ontology (HMO)`.
   - Do **not** fail solely because the label is a system identifier such as
     `CatalogStep 990000403370205171`.

4. **Generic fallback descriptions** (`… in the Hebrew Manuscripts Ontology (HMO)`)
   without substantive content → `name_ok = partial` at best; `fail` when the
   item is a primary scholarly entity (Work, Expression, Person, Manuscript).

5. **Malformed labels** (unbalanced quotes, trailing punctuation inside quotes,
   doubled backslashes, `und` language codes) → `name_ok = partial` or `no`.

6. **Blocking SHACL** (`Violation` / `Error` in `shacl_issues`) → `role_ok = no`,
   `overall = fail`, regardless of labels.

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
| `E12_Production` | Production event | dates/places from MARC |
| `CatalogStep` / `EvidenceStep` / `EvidenceChain` | Structural | System label OK; generic-only description is partial |

Be conservative on **claims** (do not invent P/Q statements), but do not
require every derived entity label to be a MARC substring.
