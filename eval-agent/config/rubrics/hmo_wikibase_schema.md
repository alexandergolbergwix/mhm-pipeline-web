# HMO Wikibase Schema Rubric

You judge one class or property created on the project's own Wikibase
Cloud instance (`mhm-hmo.wikibase.cloud`) during the HMO ontology
schema bootstrap. This is NOT wikidata.org — it is a self-hosted
Wikibase modeling the full Hebrew Manuscripts Ontology (HMO), so the
correctness bar is "faithful to the OWL source and internally
coherent," not "acceptable on Wikidata." Use Wikidata's own
manuscript-modeling conventions (below) only as an external sanity
check for the small overlap where HMO and Wikidata describe the same
real-world concepts.

Return JSON with the standard verdict keys:

- `name_ok`: `"yes"` when the label and description are accurate,
  non-empty, and genuinely describe the OWL class/property rather than
  just echoing its raw local name. **The `description` field in the
  prompt is authoritative** — if it is present and non-empty, do NOT
  claim the entry lacks a description. A generated fallback description
  (`"HMO class: X"` / `"HMO property: X"`) is acceptable ONLY when the
  ontology source truly had no `rdfs:comment`/`skos:definition` — treat
  it as `"partial"`-worthy evidence, not automatic failure, since it is
  a documented, deliberate fallback, not a bug.
- `type_ok`: for properties, `"yes"` when the assigned Wikibase
  datatype matches what the label/description say the property stores,
  **and** matches the OWL declaration when `OWL kind` / `rdfs:range`
  are shown in the prompt. Common mistakes to catch:
  - A count/measurement (page counts, dimensions in mm, numeric years)
    typed as `string` instead of `quantity` or `time`.
  - A property whose description says "URL"/"link"/"IIIF manifest"
    typed as `string` instead of `url`.
  - A property whose description says it stores a catalog/authority
    identifier typed as `string` instead of `external-id`.
  - An `owl:ObjectProperty` whose `rdfs:range` is another class
    (CIDOC/LRMoo/HMO) NOT typed as `wikibase-item`.
  - **Exception:** when the ontology explicitly declares
    `owl:DatatypeProperty` with `rdfs:range xsd:string`, a Wikibase
    datatype of `string` is correct even for catalog metadata like
    `holding_institution`, holding collection, folio designations
    (`15r`, `2a`), IIIF region coordinate tuples (`x,y,w,h`), or
    free-text role labels copied from MARC — do NOT fail these as
    missing `wikibase-item`.
  - **Do not confuse homonymous properties:** `holding_institution`
    (free-text string on a PhysicalHolding) is different from
    `has_holding_institution` (object property to `E74_Group`) —
    judge each by its own OWL kind and range.
  - **CIDOC/LRMoo object properties** such as `P102_has_title`,
    `has date of creation`, `has production time`, `mentions date`,
    `has digital access`, and `IIIF region` link to Appellation,
    Time-Span, DigitalAccess, or other class nodes — `wikibase-item`
    is correct even when the English label sounds like a literal string.
  - **`owl:sameAs` / external authority URIs** modeled as Wikibase
    `url` datatype are intentional — the value is a URI string on the
    claim, not a reference to another Wikibase item.
  - **WGS84 `geo:lat` / `geo:long`** are modeled as separate
    `quantity` properties in this schema (the item exporter does not use
    `globe-coordinate`) — do NOT fail these for not being
    `globe-coordinate`.
  - **`xsd:boolean` ranges** (certainty flags, supersession flags) are
    correctly typed as `boolean` on Wikibase.
  For classes, `"type_ok"` is `"yes"` when the class is not redundant
  with an existing, more specific HMO class for the same concept.
- `role_ok`: judges consistency with established manuscript-cataloging
  conventions where HMO and Wikidata overlap (see below). Use `"n/a"`
  when nothing comparable exists on Wikidata for this class/property.
  Use `"no"` only for a genuine, avoidable modeling collision — not
  for HMO legitimately going beyond what Wikidata models (HMO's much
  larger custom class/property set is the whole point of this Wikibase
  instance, not a defect).
- `overall`: `"pass"` only when `name_ok` and `type_ok` are `"yes"` and
  `role_ok` is not `"no"`.
- `reasoning`: one concise explanation citing the specific label,
  datatype, or Wikidata convention involved.

Be conservative. Do not invent an OWL definition the context doesn't
show you; judge only what the ontology_uri, label, description,
datatype, OWL kind, and rdfs:range fields actually say.

## Cross-reference: Wikidata's manuscript-modeling conventions

(from `Wikidata:WikiProject_Manuscripts/Data_Model`, for informational
comparison only — HMO is not required to match these, but a class or
property that quietly re-derives the same distinction differently, in
a way that would make future Wikidata cross-linking (Rule W-26/W-32 in
this repo) ambiguous, is worth flagging via `role_ok`.)

- The physical manuscript object is its own class, kept distinct from
  generic "codex" or "book" classes: Wikidata explicitly discourages
  `instance of` → `codex (Q213924)` or `instance of` → `book (Q571)`
  for a manuscript; the same distinction (a manuscript-specific class,
  not a generic "book"-like fallback) should hold in HMO.
- **Numeric page/folio counts** (e.g. "42 folios") are `quantity`-shaped
  facts (`number of pages P1104`), not free text — but **folio
  designations** (`15r`, `15v`, `2a`, `15b`) stored as `xsd:string`
  are labels for a location, not counts; `string` is correct for those.
- Physical dimensions (height/width) are `quantity`-shaped facts.
- Creation properties are split by role: scribe/copyist, illuminator,
  and commissioner/patron are three DIFFERENT properties, not one
  generic "creator" or "associated person" bucket, when the ontology
  actually distinguishes these roles.
- Holding institution and owner are properties that can change over
  time (Wikidata qualifies them with start/end time and preferred
  rank for the current value) — a class/property pair that can only
  express "the current owner" with no way to represent a prior owner
  is a weaker model than Wikidata's, worth a `role_ok = "partial"`
  note (not a hard failure — HMO's provenance-event model, Rule W-32
  in this repo, may already cover this at the instance level rather
  than the schema level).
- External identifiers (VIAF/GND/ISNI/shelfmark/catalog codes) are
  `external-id`-typed properties, never generic `string`.
- Palimpsests keep upper and lower script within the same manuscript
  item rather than splitting into two separate manuscript records —
  if HMO models palimpsests as two unrelated manuscripts instead of
  one manuscript with two script layers, flag it.
