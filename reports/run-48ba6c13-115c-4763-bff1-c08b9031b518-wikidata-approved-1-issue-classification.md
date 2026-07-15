# Wikidata non-passing issue classification and remediation plan

- Source: `/Users/alexandergo/Downloads/run-48ba6c13-115c-4763-bff1-c08b9031b518-wikidata-approved (1).json`
- Run: `48ba6c13-115c-4763-bff1-c08b9031b518`
- Scope: `71` entities whose AI verdict is `partial` or `fail`; passing/full entities are excluded.
- Purpose: classify each observed issue by the layer that should be fixed, then rank the fixes by difficulty and expected pass-rate impact.

## Executive conclusion

The remaining partials are primarily caused by the item projection making claims that are broader or more certain than the MARC/authority evidence supports. This is not primarily a transport or job-service failure. The highest-leverage change is to make the RDF-to-Wikidata projection evidence-gated and fail closed: preserve raw evidence for curator review, but do not publish an item-valued or semantic claim when the source only supports an ambiguous inference.

Using the existing verdict fields:

| Measure | Result | Interpretation |
|---|---:|---|
| Non-passing entities | **71** | 70 partial and 1 fail in this snapshot |
| Entity types | **58 manuscripts, 7 persons, 6 works** | Distribution of the 71 non-passing entities |
| `name_ok=partial` | **12** | 6 work-label quote problems and 6 person-name/authority problems |
| `role_ok=partial` | **61** | 61 entities have at least one semantic, role, evidence, or identity issue |
| `type_ok=no` | **1** | One facsimile/print was emitted as a manuscript |

The issue-family counts below are **overlapping** mentions extracted from the verdict reasoning; they are not a claim that each family is a separate entity set.

| Issue family | Entities mentioning it | Primary fix layer | Difficulty | Expected effect |
|---|---:|---|---|---|
| Scribe/transcriber claim missing or unresolved | 28 | Authority reconciliation + RDF creator | Medium–High | Very high |
| Over-broad or secondary P921 main subject | 24 | RDF creator / subject projection | Medium | Very high |
| Wrong or ambiguous authority target | 23 | Authority enrichment / reconciliation | High | Very high |
| Ownership/provenance role misuse | 21 | RDF creator / property semantics | Medium | Very high |
| Unsupported or over-specific P136 genre | 18 | RDF creator / genre projection | Medium | Very high |
| Missing or incorrect holding institution | 14 | RDF creator / collection projection | Low–Medium | Medium–High |
| Wrong or malformed date | 12 | RDF creator / date normalization | Medium | High |
| Catalog notes emitted as P1684 inscriptions | 10 | RDF creator / note-field extraction | Low–Medium | High |
| Work-label quote/ISBD normalization | 6 | RDF creator / label normalization | Low | High for affected items |
| Person label and authority-name quality | 6 | Authority enrichment / reconciliation | Medium | High for affected items |
| HMO/Wikibase links used as exact or primary evidence | 6 | Wikibase staging / reference serialization | Medium | High |
| Contained-work / folio-range projection | 4 | RDF creator / work-candidate parser | High | Medium–High |
| Missing author/work chain | 2 | RDF creator / work modeling | Medium | Medium–High |
| Commentator/annotator identity qualifier | 1 | Authority enrichment / role projection | Medium | Medium |
| Entity type classification | 1 | RDF creator / entity-kind inference | Medium | Very high for affected items |

## Recommended architecture decision: Wikibase as staging, not as a blind source

Yes—base the public Wikidata projection on normalized HMO Wikibase entities, but only through an explicit publishable projection. HMO Wikibase should be the staging and reconciliation layer for manuscript, work, and person entities; it should not be treated as proof that every staged statement is safe for Wikidata.

Recommended flow:

1. **MARC/RDF extraction** creates rich evidence objects and candidate claims, including source field, role, confidence, and raw text.
2. **HMO Wikibase staging** stores canonical local entities, normalized names, stable internal IDs, authority matches, and all evidence—including uncertain candidates.
3. **Reconciliation gate** resolves local people/works to Wikidata QIDs only when name, role, chronology, and source record agree. A mismatch remains a string/evidence record, not an item-valued claim.
4. **Wikidata publish projection** applies a strict property whitelist and semantic guards. It emits only claims that are supported and correctly modeled; HMO URLs are not automatically `P2888` exact matches.
5. **AI verification** judges the publish projection. Its partial/fail output feeds back into the projection rules and the curator queue—not into a blanket relaxation of the rubric.

This architecture would directly reduce the local-target and QID mismatches, missing labels, and source-link ambiguity. It will not, by itself, fix over-broad P921/P136/P127/P1684 claims; those mappings must still be corrected in the RDF creator.

## Priority plan: difficulty versus effectiveness

| Priority | Change | Difficulty | Expected effectiveness | Why this order |
|---|---|---|---|---|
| P0 | Evidence-gate P921/P136/P127/P1684 and drop unsupported claims | Low–Medium | Very high; addresses the dominant role failures | A conservative omission is safer than a false semantic claim and can flip many partials immediately |
| P0 | Correct entity-kind/date handling for `דפוס צלום`/facsimiles | Medium | Very high; fixes the only fail and prevents future type failures | Wrong type and wrong date invalidate the whole item |
| P0 | Normalize ISBD quote noise without stripping legitimate Hebrew abbreviations | Low | High; directly addresses all 6 work name partials | Small deterministic parser fix with a narrow regression suite |
| P0 | Filter catalog/workflow text before P1684 | Low–Medium | High | Removes statements the evaluator correctly treats as non-inscriptions |
| P1 | Add authority-target acceptance checks for every P11603/P127/P9046 link | Medium–High | Very high | Wrong people are worse than missing people; this fixes the largest identity cluster |
| P1 | Replace former-owner/censor mappings with correct provenance roles | Medium | Very high | One role map fixes many manuscript partials at once |
| P1 | Restrict P921 to primary/canonical subjects | Medium | Very high | 24 entities are penalized for secondary or overly generic subjects |
| P1 | Make P136 mappings exact and evidence-backed | Medium | Very high | 18 entities carry unsupported genre claims |
| P1 | Resolve current holding institution from MARC 710; stop defaulting to NLI | Low–Medium | Medium–High | Fixes obvious P195 omissions and wrong descriptions |
| P1 | Normalize dates and reject cataloging dates/BCE syntax errors | Medium | High | Prevents false chronology and malformed Wikibase time values |
| P2 | Validate P1574/P958 folio ranges and work boundaries | High | Medium–High | Fewer rows, but high data-integrity value for anthology manuscripts |
| P2 | Make HMO/Wikibase the canonical staging graph with a publish projection | High | High long-term | Prevents recurring local-ID/QID drift and enables repeatable reconciliation |

### What not to do

- Do not loosen the evaluator until unsupported claims pass. The verdicts are correctly identifying semantic overreach in this snapshot.
- Do not copy every HMO statement to Wikidata. HMO can retain richer evidence than Wikidata should publish.
- Do not replace a wrong authority target with a string-only claim and call it resolved. Keep the evidence and route it to reconciliation.
- Do not strip all quote characters; that would destroy legitimate Hebrew abbreviation marks. Normalize parser wrappers, not the linguistic content.

## Issue-family remediation details

### Scribe/transcriber claim missing or unresolved (28 entities; overlapping count)

- **Fix layer:** Authority reconciliation + RDF creator
- **Difficulty:** Medium–High
- **Expected effect:** Very high
- **Code areas:** `converter/wikidata/person_linking.py`, local-reference target attachment, `app/pipeline/wikidata_verdict_cache.py`
- **Recommended change:** Attach P11603 only when the target identity matches the MARC 700/500 role and name. Require a resolved label for final export; retain `P1932` as the catalog name, but do not use it to mask a wrong target.
- **Entities:** `990000759620205171`, `990000776020205171`, `990000825080205171`, `990000856010205171`, `990000880550205171`, `990000880710205171`, `990001028160205171`, `990001056990205171`, `990001089360205171`, `990001135400205171`, `990001136560205171`, `990001205840205171`, `990001219490205171`, `990001254240205171`, `990001286970205171`, `990001376840205171`, `990001499320205171`, `990001580110205171`, `990001792890205171`, `990001827870205171`, `990001830400205171`, `990001856120205171`, `990001858880205171`, `990001869850205171`, `990001875220205171`, `990001878130205171`, `990035044030205171`, `990038692590205171`

### Over-broad or secondary P921 main subject (24 entities; overlapping count)

- **Fix layer:** RDF creator / subject projection
- **Difficulty:** Medium
- **Expected effect:** Very high
- **Code areas:** `converter/wikidata/content_projection.py` (`_add_canonical_subjects`), `converter/wikidata/marc_subject_resolve.py`
- **Recommended change:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.
- **Entities:** `990000439040205171`, `990000569120205171`, `990000592310205171`, `990000597080205171`, `990000633490205171`, `990000749240205171`, `990000825080205171`, `990000827290205171`, `990000864590205171`, `990000927260205171`, `990001028160205171`, `990001039720205171`, `990001056990205171`, `990001089360205171`, `990001118020205171`, `990001136560205171`, `990001192130205171`, `990001205840205171`, `990001343040205171`, `990001376840205171`, `990001580110205171`, `990001869850205171`, `990001948980205171`, `990038692590205171`

### Wrong or ambiguous authority target (23 entities; overlapping count)

- **Fix layer:** Authority enrichment / reconciliation
- **Difficulty:** High
- **Expected effect:** Very high
- **Code areas:** `converter/wikidata/person_linking.py`, `app/pipeline/authority_hardening.py`, local-reference target construction
- **Recommended change:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.
- **Entities:** `mazal:987007522759105171`, `mazal:987007268036905171`, `mazal:987007306870005171`, `mazal:987007339380005171`, `mazal:987007270155705171`, `mazal:987007453092705171`, `990000825080205171`, `990000856010205171`, `990000880550205171`, `990001028160205171`, `990001205840205171`, `990001219490205171`, `990001253400205171`, `990001254240205171`, `990001379460205171`, `990001406710205171`, `990001499320205171`, `990001800310205171`, `990001856120205171`, `990001875220205171`, `990001878130205171`, `990025903200205171`, `990035044030205171`

### Ownership/provenance role misuse (21 entities; overlapping count)

- **Fix layer:** RDF creator / property semantics
- **Difficulty:** Medium
- **Expected effect:** Very high
- **Code areas:** `converter/wikidata/manuscript_metadata.py`, `converter/wikidata/person_linking.py`, ownership-role mapping
- **Recommended change:** Map current 710 ownership to P195/P127 as appropriate; do not use P127 for former owners, sellers, or censors. Represent historical ownership with provenance/event semantics and qualifiers, keeping the MARC role explicit.
- **Entities:** `990000403370205171`, `990000592310205171`, `990000776020205171`, `990000825080205171`, `990000856010205171`, `990000864590205171`, `990000880550205171`, `990001135400205171`, `990001219490205171`, `990001253400205171`, `990001254240205171`, `990001286970205171`, `990001340200205171`, `990001406710205171`, `990001800310205171`, `990001827870205171`, `990001875220205171`, `990001878130205171`, `990001901440205171`, `990025903200205171`, `990038692590205171`

### Unsupported or over-specific P136 genre (18 entities; overlapping count)

- **Fix layer:** RDF creator / genre projection
- **Difficulty:** Medium
- **Expected effect:** Very high
- **Code areas:** `converter/wikidata/content_projection.py` (`_add_marc_genres`), `converter/wikidata/marc_subject_resolve.py`, genre mapping tables
- **Recommended change:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.
- **Entities:** `990000403370205171`, `990000439040205171`, `990000860360205171`, `990000880710205171`, `990000927260205171`, `990001039720205171`, `990001056990205171`, `990001238980205171`, `990001340200205171`, `990001343040205171`, `990001376840205171`, `990001400870205171`, `990001580110205171`, `990001858880205171`, `990001875220205171`, `990001882630205171`, `990001948980205171`, `990038384110205171`

### Missing or incorrect holding institution (14 entities; overlapping count)

- **Fix layer:** RDF creator / collection projection
- **Difficulty:** Low–Medium
- **Expected effect:** Medium–High
- **Code areas:** `converter/wikidata/manuscript_metadata.py`, 710/current-owner mapping
- **Recommended change:** Read the current holding institution from 710 and emit P195 with a verified QID/label. Do not default every item to NLI when the record names the Russian State Library, Bodleian, British Library, JTS, or another institution.
- **Entities:** `990000403370205171`, `990000439040205171`, `990000759620205171`, `990000860360205171`, `990000880710205171`, `990001028160205171`, `990001056990205171`, `990001343040205171`, `990001580110205171`, `990001827870205171`, `990001869850205171`, `990001901440205171`, `990001948980205171`, `990019020880205171`

### Wrong or malformed date (12 entities; overlapping count)

- **Fix layer:** RDF creator / date normalization
- **Difficulty:** Medium
- **Expected effect:** High
- **Code areas:** `converter/wikidata/manuscript_metadata.py`, MARC date parsing and description builders
- **Recommended change:** Separate publication/copy dates from cataloging dates, normalize BCE Wikibase time syntax (for example `-0199-00-00T00:00:00Z`), retain precision, and suppress dates that are only inferred from a generic description.
- **Entities:** `990000569120205171`, `990000592310205171`, `990000597080205171`, `990001028160205171`, `990001056990205171`, `990001286970205171`, `990001402000205171`, `990001406710205171`, `990001878130205171`, `990019020880205171`, `990025903200205171`, `990035044030205171`

### Catalog notes emitted as P1684 inscriptions (10 entities; overlapping count)

- **Fix layer:** RDF creator / note-field extraction
- **Difficulty:** Low–Medium
- **Expected effect:** High
- **Code areas:** `converter/wikidata/manuscript_metadata.py`, `converter/authority/ner_post_filters.py`, P1684 projection
- **Recommended change:** Classify 500/561 text before emitting P1684. Only actual colophons, inscriptions, marginalia, or corrections become P1684; catalog subjects, workflow notes, and rejected suggestions remain source evidence.
- **Entities:** `990000759620205171`, `990000825080205171`, `990000880710205171`, `990001039720205171`, `990001286970205171`, `990001340200205171`, `990001827870205171`, `990001875220205171`, `990019020880205171`, `990035044030205171`

### Work-label quote/ISBD normalization (6 entities; overlapping count)

- **Fix layer:** RDF creator / label normalization
- **Difficulty:** Low
- **Expected effect:** High for affected items
- **Code areas:** `converter/wikidata/work_candidates.py`, `converter/wikidata/item_builder.py`, `converter/rdf/rdf_helpers.py`
- **Recommended change:** Normalize doubled/escaped ISBD quotes exactly once at the MARC boundary while preserving legitimate Hebrew abbreviation marks (for example רס"ג, רש"י, האר"י, רמב"ם, חז"ל, תשב"ץ).
- **Entities:** `work:ענף_הג'_פע_ח_והוא_תיקוני_עוונות`, `work:תרגום_רס_ג_לתורה`, `work:פרוש_רש_י`, `work:כתובים_(אמ_ת)_עם_ניקוד_טעמים_ומסורה_קטנה`, `work:תשב_ץ`, `work:מאמרי_חז_ל_הפותחים_בג'_דברים_ד'_דברים_וכו`

### Person label and authority-name quality (6 entities; overlapping count)

- **Fix layer:** Authority enrichment / reconciliation
- **Difficulty:** Medium
- **Expected effect:** High for affected items
- **Code areas:** `converter/wikidata/person_linking.py`, `app/pipeline/authority_hardening.py`
- **Recommended change:** Choose labels from the matched authority record, preserve inverted catalog forms as aliases, retain patronymics, do not truncate English labels, and quarantine corrupted transliteration/name conflicts for review.
- **Entities:** `mazal:987007522759105171`, `mazal:987007268036905171`, `mazal:987007306870005171`, `mazal:987007339380005171`, `mazal:987007270155705171`, `mazal:987007453092705171`

### HMO/Wikibase links used as exact or primary evidence (6 entities; overlapping count)

- **Fix layer:** Wikibase staging / reference serialization
- **Difficulty:** Medium
- **Expected effect:** High
- **Code areas:** `backend/app/pipeline/wikidata_studio.py`, `converter/wikidata/item_builder.py`, URL/reference projection
- **Recommended change:** Use the NLI Ktiv URL as the source reference. Emit P2888 only for a proven exact match; use P973 or an internal staging link for HMO/Wikibase mirrors. Never make a self-referential staging URL the sole authority.
- **Entities:** `990000860360205171`, `990000864590205171`, `990000880710205171`, `990001136560205171`, `990001402000205171`, `990001882630205171`

### Contained-work / folio-range projection (4 entities; overlapping count)

- **Fix layer:** RDF creator / work-candidate parser
- **Difficulty:** High
- **Expected effect:** Medium–High
- **Code areas:** `converter/wikidata/content_projection.py`, `converter/wikidata/work_candidates.py`
- **Recommended change:** Parse 505 ranges with a monotonic/non-overlap check, preserve gaps explicitly, reject contradictory ranges, and attach P958 qualifiers only after validation. Keep accepted work evidence even when a folio qualifier is withheld.
- **Entities:** `990000856010205171`, `990001089360205171`, `990001205840205171`, `990001801390205171`

### Missing author/work chain (2 entities; overlapping count)

- **Fix layer:** RDF creator / work modeling
- **Difficulty:** Medium
- **Expected effect:** Medium–High
- **Code areas:** `converter/wikidata/content_projection.py`, `converter/wikidata/person_linking.py`
- **Recommended change:** Keep the constraint-safe chain manuscript → P1574 → work → P50/P2093. Create or resolve the work author when MARC 100/700 evidence identifies one; never reintroduce direct P50 on the manuscript.
- **Entities:** `990001056990205171`, `990001948980205171`

### Commentator/annotator identity qualifier (1 entities; overlapping count)

- **Fix layer:** Authority enrichment / role projection
- **Difficulty:** Medium
- **Expected effect:** Medium
- **Code areas:** `converter/wikidata/person_linking.py`, role/qualifier normalization
- **Recommended change:** Resolve P9046 targets using the same identity checks as scribes and keep P1932 in the catalog form that matches the authority record.
- **Entities:** `990001379460205171`

### Entity type classification (1 entities; overlapping count)

- **Fix layer:** RDF creator / entity-kind inference
- **Difficulty:** Medium
- **Expected effect:** Very high for affected items
- **Code areas:** `backend/app/pipeline/entity_kind_infer.py`, `converter/wikidata/item_builder.py`, description/date builders
- **Recommended change:** Detect catalog indicators such as דפוס צלום (photographic print/facsimile) before defaulting to manuscript; derive the date from publication/copy evidence, not a cataloging timestamp; add a regression fixture.
- **Entities:** `990019020880205171`

## Per-entity classification

The following sections cover every non-passing entity. Tags are generated from the verdict reasoning and may overlap; the “primary fix layer” is the first implementation owner, not the only code that may change.

### 1. `work:ענף_הג'_פע_ח_והוא_תיקוני_עוונות` — partial

- **Label:** ענף הג' פע"ח והוא תיקוני עוונות
- **Entity type:** `work`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `LABEL_QUOTE_NOISE`
- **Issue tags:** `LABEL_NORMALIZATION`
- **Primary fix layer:** RDF creator / label normalization
- **Primary difficulty/effect:** Low / High for affected items
- **First implementation action:** Normalize doubled/escaped ISBD quotes exactly once at the MARC boundary while preserving legitimate Hebrew abbreviation marks (for example רס"ג, רש"י, האר"י, רמב"ם, חז"ל, תשב"ץ).

**AI evidence and issue explanation:**

> The label contains ISBD quote noise (doubled quotes and escaped quotes from the 500 field extraction: ""ענף הג' פע""ח והוא תיקוני עוונות""). The work candidate evidence shows this was extracted from a catalog note with embedded quotation marks, and the LABEL_QUOTE_NOISE validator issue confirms this. The clean title should be 'ענף הג' פע"ח והוא תיקוני עוונות' without the doubled wrappers. The entity type (work), P31 (written work), and P1476 title statement are all appropriate and well-referenced to Ktiv. The description is generic but acceptable.

### 2. `work:תרגום_רס_ג_לתורה` — partial

- **Label:** תרגום רס"ג לתורה
- **Entity type:** `work`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `LABEL_QUOTE_NOISE`
- **Issue tags:** `LABEL_NORMALIZATION`
- **Primary fix layer:** RDF creator / label normalization
- **Primary difficulty/effect:** Low / High for affected items
- **First implementation action:** Normalize doubled/escaped ISBD quotes exactly once at the MARC boundary while preserving legitimate Hebrew abbreviation marks (for example רס"ג, רש"י, האר"י, רמב"ם, חז"ל, תשב"ץ).

**AI evidence and issue explanation:**

> The Hebrew label 'תרגום רס"ג לתורה' is supported by work_candidate_evidence with source_text from field 500. However, the LABEL_QUOTE_NOISE warning indicates the label contains doubled quotes (רס""ג) from ISBD parsing that should be cleaned to רס"ג. The description is generic but acceptable. P31 and P1476 are properly sourced to Ktiv with NLI identifier and URL. No authority evidence conflicts. The quote noise is a real data quality issue that should be fixed before approval.

### 3. `work:פרוש_רש_י` — partial

- **Label:** פרוש רש"י
- **Entity type:** `work`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `LABEL_QUOTE_NOISE`
- **Issue tags:** `LABEL_NORMALIZATION`
- **Primary fix layer:** RDF creator / label normalization
- **Primary difficulty/effect:** Low / High for affected items
- **First implementation action:** Normalize doubled/escaped ISBD quotes exactly once at the MARC boundary while preserving legitimate Hebrew abbreviation marks (for example רס"ג, רש"י, האר"י, רמב"ם, חז"ל, תשב"ץ).

**AI evidence and issue explanation:**

> The label 'פרוש רש"י' contains unbalanced/doubled ISBD quote noise (LABEL_QUOTE_NOISE warning) from the source text 'פרוש רש""י' in the 500 field. The work candidate evidence clearly supports this as a named work, and P31=Q47461344 (written work) and P1476 (title) are appropriate. However, the label should be cleaned to 'פרוש רש"י' with proper escaping or 'פרוש רשי' to remove the catalog artifact. The description is generic but acceptable. No authority evidence conflicts.

### 4. `work:כתובים_(אמ_ת)_עם_ניקוד_טעמים_ומסורה_קטנה` — partial

- **Label:** כתובים (אמ"ת) עם ניקוד טעמים ומסורה קטנה
- **Entity type:** `work`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `LABEL_QUOTE_NOISE`
- **Issue tags:** `LABEL_NORMALIZATION`
- **Primary fix layer:** RDF creator / label normalization
- **Primary difficulty/effect:** Low / High for affected items
- **First implementation action:** Normalize doubled/escaped ISBD quotes exactly once at the MARC boundary while preserving legitimate Hebrew abbreviation marks (for example רס"ג, רש"י, האר"י, רמב"ם, חז"ל, תשב"ץ).

**AI evidence and issue explanation:**

> The Hebrew label contains doubled ISBD quote marks (אמ""ת) which is flagged by the LABEL_QUOTE_NOISE warning. The work_candidate_evidence shows the raw_title has the same doubled quotes from the 505 field source_text ('אמ""ת'), but the clean label should normalize this to single quotes (אמ"ת) as shown in the accepted title. The description is generic but acceptable. The P31 and P1476 statements are properly sourced to the NLI catalog. The entity type (work) is correct for a manuscript work. Fix: normalize the doubled quotes in the Hebrew label.

### 5. `work:תשב_ץ` — partial

- **Label:** תשב"ץ
- **Entity type:** `work`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `LABEL_QUOTE_NOISE`
- **Issue tags:** `LABEL_NORMALIZATION`
- **Primary fix layer:** RDF creator / label normalization
- **Primary difficulty/effect:** Low / High for affected items
- **First implementation action:** Normalize doubled/escaped ISBD quotes exactly once at the MARC boundary while preserving legitimate Hebrew abbreviation marks (for example רס"ג, רש"י, האר"י, רמב"ם, חז"ל, תשב"ץ).

**AI evidence and issue explanation:**

> The Hebrew label 'תשב"ץ' contains unbalanced/doubled ISBD quote wrappers per the LABEL_QUOTE_NOISE warning. The raw_title in work_candidate_evidence shows 'תשב""ץ' with doubled quotes, and the source_text shows 'תשב""ץ' as well. The label should be cleaned to 'תשב"ץ' (single pair of quotes). The entity type (work), description, and statements (P31, P1476, P2093) are all appropriate and supported by the work_candidate_evidence showing this is a named work in the 505 field with author 'שמשון בן צדוק'. The references to Ktiv catalog are proper.

### 6. `work:מאמרי_חז_ל_הפותחים_בג'_דברים_ד'_דברים_וכו` — partial

- **Label:** מאמרי חז"ל הפותחים בג' דברים ד' דברים וכו
- **Entity type:** `work`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `LABEL_QUOTE_NOISE`
- **Issue tags:** `LABEL_NORMALIZATION`
- **Primary fix layer:** RDF creator / label normalization
- **Primary difficulty/effect:** Low / High for affected items
- **First implementation action:** Normalize doubled/escaped ISBD quotes exactly once at the MARC boundary while preserving legitimate Hebrew abbreviation marks (for example רס"ג, רש"י, האר"י, רמב"ם, חז"ל, תשב"ץ).

**AI evidence and issue explanation:**

> The Hebrew label contains doubled ISBD quote wrappers (חז""ל) from the source_text, which should be normalized to single quotes (חז"ל) per the LABEL_QUOTE_NOISE warning. The work_candidate_evidence clearly supports this as a named work from 505 field, and P31/Q47461344 (written work) and P1476 (title) are appropriate. However, the label pollution from catalog artifacts prevents a clean 'pass'.

### 7. `mazal:987007522759105171` — partial

- **Label:** משה בן ידידיה בן עזרא
- **Entity type:** `person`
- **Verdict dimensions:** `name_ok=partial, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PERSON_NAME_AUTHORITY`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label 'משה בן ידידיה בן עזרא' uses natural order (Given Surname), but the authority evidence shows the preferred Hebrew name as inverted 'בן עזרא, משה בן ידידיה' and the Latin form as 'טריף, משה בן ידידיה' (with 'טריף' not 'בן עזרא'). The label appears to conflate two different name forms and lacks the inverted Hebrew alias. The description 'person associated with Hebrew manuscripts' is generic but acceptable given the 'former owner' role. The P31/P8189/P1559 statements are properly sourced to Ktiv. However, the name modeling needs correction: the natural-order label should be verified against the authority, and the inverted form should be preserved as an alias. The Latin transliteration 'טריף' vs 'בן עזרא' discrepancy suggests possible data quality issues in the source that should not be papered over.

### 8. `mazal:987007268036905171` — partial

- **Label:** שמואל בן יוסף
- **Entity type:** `person`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PERSON_NAME_AUTHORITY`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label 'שמואל בן יוסף' is clean and matches authority_evidence. However, the English label 'Samuel' is incomplete—it omits the patronymic 'ben Yosef' present in the Hebrew form and the authority evidence shows 'Samuel,' (likely incomplete). The description 'Hebrew manuscript scribe' is supported by authority_evidence role='scribe'. Birth/death years (993/1056) are directly from authority_evidence. All statements are properly referenced to Ktiv with J9U ID. The main issue is the truncated English label which should be 'Samuel ben Yosef' or similar to match the full Hebrew form.

### 9. `mazal:987007306870005171` — partial

- **Label:** אברהם בן שמואל גדליה
- **Entity type:** `person`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PERSON_NAME_AUTHORITY`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The English label 'Avraham Gedalyah' appears to be a natural-order conversion from the authority's inverted Latin form 'Gedalyah, Avraham', but the authority evidence shows the Hebrew preferred name as 'גדליה, אברהם בן שמואל' (inverted) with 'Gedalyah, Avraham' as Latin. The English label 'Avraham Gedalyah' drops the patronymic 'ben Shmuel' entirely, which is incomplete. The Hebrew label 'אברהם בן שמואל גדליה' uses natural order with full patronymic, which is acceptable as a label form. The description 'person associated with Hebrew manuscripts' is generic but supported by the 'former owner' role in authority evidence. The entity type (person/human) is correct. Statements are properly sourced to Ktiv with NLI ID and reference URL. No validation issues. The main concern is the incomplete English label that omits the patronymic present in the authority record.

### 10. `mazal:987007339380005171` — partial

- **Label:** ח. בן אפרים
- **Entity type:** `person`
- **Verdict dimensions:** `name_ok=partial, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PERSON_NAME_AUTHORITY`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label 'ח. בן אפרים' uses natural order (Given Surname) while the authority evidence shows the preferred Hebrew form is inverted 'בן אפרים, ח.' and the Latin transliteration is 'חנניה בן אפרים'. The label should use the full given name 'חנניה' not just the initial 'ח.' The description 'person associated with Hebrew manuscripts' is vague; authority evidence specifies 'former owner' role which should be reflected. The P1559 native name claim repeats the same incomplete label. No validation issues are present but the core identity data is underdeveloped relative to available authority evidence.

### 11. `mazal:987007270155705171` — partial

- **Label:** יצחק בן עובדיה יוסף
- **Entity type:** `person`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PERSON_NAME_AUTHORITY`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The English label 'Yitsḥaḳ Yosef' appears to be a given-name-first construction, but the authority evidence shows the preferred Latin form as 'Yosef, Yitsḥaḳ' (inverted) and the Hebrew as 'יוסף, יצחק בן עובדיה,' (inverted with patronymic). The English label omits the surname 'Yosef' and the patronymic 'ben Obadiah', making it incomplete and potentially misleading. The Hebrew native name (P1559) 'יצחק בן עובדיה יוסף' is in natural order and matches the authority evidence's Hebrew form when reordered. The entity type (person), role (scribe), and statements (P31, P569, P8189, P1559) are all supported by the authority evidence. However, the English label should be corrected to include the full name with surname.

### 12. `mazal:987007453092705171` — partial

- **Label:** דליה אליהו-קאולי
- **Entity type:** `person`
- **Verdict dimensions:** `name_ok=partial, role_ok=yes, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PERSON_NAME_AUTHORITY`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label 'דליה אליהו-קאולי' is in natural order (Given Surname), which is acceptable per policy, but the authority evidence shows the preferred Hebrew name as 'אליהו-קאולי, דליה' (inverted). The Latin preferred name 'דלפוגיט, אליהו' appears corrupted or mismatched—this looks like a data quality issue in the source authority, not a valid Latin transliteration of the Hebrew name. The description 'Hebrew manuscript scribe' is supported by the authority evidence role='scribe'. The entity type 'person' is correct. Statements are properly referenced with NLI authority data. However, the name discrepancy and apparent corruption in the Latin preferred name warrant caution.

### 13. `mazal:987007383133305171` — partial

- **Label:** מוסי בן דאוד
- **Entity type:** `person`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** 
- **Primary fix layer:** Manual curator review

**AI evidence and issue explanation:**

> The Hebrew label 'מוסי בן דאוד' matches the authority_evidence preferred_name_heb exactly. The entity type 'person' is correct per authority_evidence (entity_kind: person, name_type: Personal). However, the description 'person associated with Hebrew manuscripts' is generic and unsupported—the authority_evidence specifies 'former owner' as the role, which should be reflected in the description or statements. The P31 human, P8189 identifier, and P1559 native name are all properly sourced to Ktiv with appropriate references. No statements capture the 'former owner' role from the authority evidence, which is a notable omission. The validation issues are empty, but the role modeling is incomplete.

### 14. `990000403370205171` — partial

- **Label:** שער שברי לוחות : פירוש המסורת אשר חבר הרב ר' אלי' המדקדק זל, הה מבאר בו כל ראשי תיבות והמילות זרות אשר במסורה קטנה, וקרא שמו שער שברי לוחות יען כי בו יתבארו כל מלות זרות ותיבות קצרות וחסרות ושבורות הנמצאות בגליונות
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`, `PROVENANCE_MAPPING`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / genre projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.

**AI evidence and issue explanation:**

> The Hebrew label matches the MARC title field exactly. The entity type 'manuscript' is correct. However, P136 (genre) = 'illuminated manuscript' is unsupported: the MARC 500$a mentions 'שער מעוטר בדיו' (decorated title page in ink) but this does not constitute an illuminated manuscript (Q48498). The 655 genre field shows 'Illustrated works (Manuscript)' which is not the same as illuminated manuscript. P31 already has 'manuscript' (Q87167) which is sufficient; the additional 'illuminated manuscript' claim is unsupported by the evidence. P195 = Q188915 has no visible label in the context, but the MARC shows 710 with 'The National Library of Israel' as current owner, so this appears to be the holding institution. The P127 claim with __LOCAL reference to Joseph Sänger as former owner is supported by the MARC 700 field and 561$a provenance note. The date 1697 and other core metadata are supported. Remove the unsupported P136 claim.

### 15. `990000439040205171` — partial

- **Label:** פסק דין : פסק דין מרבני קושטא ר' שמעון ן' חביב ור' משה בנבנשת, להתרת עגונה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `GENRE_MAPPING`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> Labels and descriptions are accurate: Hebrew title matches MARC 245, English description correctly identifies as Hebrew manuscript, 1670, National Library of Israel. Entity type 'manuscript' is appropriate. However, P136='autograph' (Q9026959) is unsupported by MARC evidence—the 500$a describes this as containing an autograph response (טיוטת תשובה אוטוגרפית) by R. Shimon ibn Ḥabib, but the manuscript itself is a composite volume with copied testimonies and clean copies, not fundamentally an autograph manuscript. The 655 genre is 'Autograph manuscripts' but this appears to be a cataloging artifact for the containing collection rather than accurate for this specific item's nature. P136='document' with 'presumably' qualifier is acceptable. P921='Halakha' is reasonably supported by 650$a='Jewish law' and the content description. P195='Q188915' has null value_label but is presumably National Library of Israel; this is acceptable given the holding institution context. The autograph genre claim should be removed or demoted.

### 16. `990000569120205171` — partial

- **Label:** אוצר כלי חמדה : יבואו בו הרבה ... רפואות ... תפלות על דרך הקבלה המעשית
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `DATE_EXTRACTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label matches the MARC 245 title field. The description accurately reflects the manuscript nature, date (1696 from 561$a), and holding institution (implied by NLI catalog reference). P31=manuscript and P571=+1696-00-00T00:00:00Z are correct. However, P921=Kabbalah is only partially supported: while 650$a includes 'Cabala' and the title mentions 'הקבלה המעשית' (practical Kabbalah), the 500$a explicitly lists multiple subjects including 'קבלה', 'קבלה מעשית', and 'פולקלור' (folklore). Asserting only Kabbalah as P921 omits the equally prominent 'Jewish magic' subject (650$a) and overweights one topic when the record treats multiple subjects. The P921 claim should either include additional subjects or be removed as incomplete. No validation issues are present.

### 17. `990000592310205171` — partial

- **Label:** גלא עמיקתא (חלק א, ב)
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `PROVENANCE_MAPPING`, `DATE_EXTRACTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label 'גלא עמיקתא (חלק א, ב)' matches the MARC title field exactly. The description correctly identifies this as a Hebrew manuscript from 1600 at NLI with subjects including Sabbathaians and Messiah, supported by the 500$a field. The entity type 'manuscript' is appropriate. However, P921 'main subject' = 'Kabbalah' (Q123006) is unsupported: while the 500$a mentions 'קבלה' and 'kabalah' in context, the primary subjects in the 650 fields are 'Cabala', 'Sabbathaians', and 'Messiah' — with 'Cabala' being the English form, not 'Kabbalah' as a distinct QID. The P127 claim with '__LOCAL:mazal:987007433198305171' is acceptable as the local reference target exists with matching 'former owner' role and Hebrew name 'אברהם ונטורה' / 'ונטורה, אברהם'. The P571 date +1600-00-00T00:00:00Z with precision=9 is valid year-level dating. The main issue is the unsupported P921 Kabbalah claim which should be removed or corrected to match the actual 650 subject 'Cabala' if a distinct mapping exists.

### 18. `990000597080205171` — partial

- **Label:** מבוא שערים
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `DATE_EXTRACTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label 'מבוא שערים' matches the 245 title field exactly. The description 'Hebrew manuscript, 1698, National Library of Israel' is appropriate. The entity type 'manuscript' (Q87167) is correct. However, P921 'main subject' = 'Kabbalah' (Q123006) is unsupported: the MARC 650 shows 'Cabala' as a subject, but the 500$a note indicates this is a work by Chaim Vital (author of 'Etz Chaim' introduction), and 'Cabala' as a broad heading is too generic per the rubric unless it's the primary subject. The 500$a emphasizes this is a specific kabbalistic work with glosses by named commentators, not a general treatise on Kabbalah. P921 should be removed or narrowed. All other statements (P31, P2888, P973, P3959, P1476, P571) are properly sourced to Ktiv with appropriate references. No validation issues.

### 19. `990000633490205171` — partial

- **Label:** אגרת שדר לר' חיא גבריאל : על השדר כהר חייא גבריאל יצו
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label matches the MARC title field. The manuscript type (Q87167) is correct. The inception year 1672 (precision=9) is valid. However, P921 'shaliah' is unsupported: the MARC 650 shows 'Sheluhe de-rabanan' (שלוחי דרבנן), not 'shaliah' (שליח). The 500$a describes emissaries collecting for Rabbi Ḥiyya Gabriel, but this is about the manuscript's content/provenance, not its primary subject. P921 should be removed or replaced with a supported subject. The genre 'letter' (Q133492) is reasonable given 'Personal correspondence' in 655 and the title 'אגרת' (letter).

### 20. `990000749240205171` — partial

- **Label:** תוצאות חיים : קצור ספר ראשית חכמה להנל
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label matches the MARC 500$a and title fields. The manuscript type (Q87167) is correct. However, P921 'Kabbalah' is weakly supported: the MARC 650 shows 'Cabala' as one of two subjects alongside 'Jewish ethics', with no indication it is primary. The description mentions 'Jewish ethics' but the P921 claim selects only Kabbalah. Additionally, the work candidate evidence shows a rejected fragment ('רק דפים אחדים מסוף החבור') that was not accepted, and no author entity is linked despite the MARC 100/700 showing Elijah ben Moses Widash as the author. The P921 claim should be removed or both subjects should be included with appropriate qualification.

### 21. `990000759620205171` — partial

- **Label:** מגיד מישרים
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `INSCRIPTION_EXTRACTION`, `SCRIBE_RECONCILIATION`, `HOLDING_INSTITUTION`
- **Primary fix layer:** Authority reconciliation + RDF creator
- **Primary difficulty/effect:** Medium–High / Very high
- **First implementation action:** Attach P11603 only when the target identity matches the MARC 700/500 role and name. Require a resolved label for final export; retain `P1932` as the catalog name, but do not use it to mask a wrong target.

**AI evidence and issue explanation:**

> Labels and descriptions are clean and accurate. The manuscript type (Q87167) is correct. However, P921 'Kabbalah' is supported by the 650$a 'Cabala' and 500$a 'נושא נוסף: קבלה', but P1684 inscription contains a colophon transcription that appears accurate from 500$a. The P1684 qualifier P3831=Q372474 (colophon) is appropriate. The main concern is P195 (holding institution) is missing - the 710$a shows 'The Russian State Library' as current owner, not NLI, which contradicts the description's 'National Library of Israel'. The description should reflect the actual holding institution from 710 or be more generic. Also, P11603 (scribe) is missing despite 700$a mentioning 'יוסף הכהן' as '(מוזכר)' which could indicate a scribe or associated person. The item needs P195 corrected and possibly P11603 added.

### 22. `990000776020205171` — partial

- **Label:** נר ה'
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `SCRIBE_RECONCILIATION`
- **Primary fix layer:** RDF creator / property semantics
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Map current 710 ownership to P195/P127 as appropriate; do not use P127 for former owners, sellers, or censors. Represent historical ownership with provenance/event semantics and qualifiers, keeping the MARC role explicit.

**AI evidence and issue explanation:**

> The Hebrew label 'נר ה'' matches the MARC 500$a and title field. The manuscript type (Q87167) is correct. The inception date 1616 (שע"ו) is supported by 500$a. However, P127 'owned by' is used for former owners with P1932 name strings, but P127 semantically means current ownership; former ownership should use P793 (significant event) with P580/P582 qualifiers or similar. The P11603 'transcribed by' claim for the scribe ישועה אזולאי is correctly modeled. The P921 'sermon' subject is reasonable given the 650$a 'Jewish funeral sermons' and 'Jewish sermons, Hebrew'. The __LOCAL references are valid as targets exist in local_reference_targets. The main issue is P127 being used for former owners rather than current ownership.

### 23. `990000825080205171` — partial

- **Label:** ספר המבחר : פרוש התורה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `PROVENANCE_MAPPING`, `INSCRIPTION_EXTRACTION`, `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and description. P31 (manuscript), P1476 (title), P571 (1501), and P3959 are well-supported. However, P921 'Karaite Judaism' is weakly grounded—the MARC 650 shows 'Karaites' as a topic but not as the primary subject warranting P921. The P11603 'transcribed by' claim uses __LOCAL:mazal:987007268036905171 with object named as 'אשנבי, יוסף בן שמואל', but the authority evidence shows this person as 'שמואל בן יוסף' (Samuel ben Joseph) with role 'scribe', not 'Yosef ben Shmuel Ashnavi'. The name string in P1932 appears garbled/inverted compared to the authority record. The P127 former owner claims using __LOCAL references are acceptable as placeholders. The P1684 inscription with P3831 role qualifier lacks clear support in the MARC context. Suggested fix: correct the P1932 value for the scribe to match the authority evidence 'שמואל בן יוסף' or 'Samuel ben Joseph'.

### 24. `990000827290205171` — partial

- **Label:** פיוטים ושירים : הפיוטים בעברית, עברית ואיטלקית משולב, ואיטלקית
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew and English labels/descriptions derived from the MARC record. The entity type (manuscript) and new QID are appropriate. However, P921 'main subject' = Purim is weakly supported: while 'Purim' appears in the 500$a notes as 'נושא נוסף: פורים' (additional subject), the primary content is piyyutim and poetry for multiple occasions including Yom Kippur, not specifically Purim. The description mentions 'Confession; Judaism' as subjects, with Purim being one of several topics. P921 should be reserved for primary subjects, and this claim should be removed or demoted. The genre claims (autograph, poetry, piyyut) are well-supported by 655 fields. No validation issues are present.

### 25. `990000856010205171` — partial

- **Label:** קונטרס בית כנסת בקהילת קנדיאה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `WORK_FOLIO_MAPPING`, `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and description. The P1574 'exemplar of' statements for six works are supported by the 500 field content listing these works. However, P11603 'transcribed by' uses local reference '__LOCAL:mazal:987007258452905171' with object named as 'שלמה נחום בן שבתי', but the MARC 700 field shows the scribe as 'שלמה נחום בן שבתי' with role '(מעתיק)' - the name order differs ('שלמה נחום בן שבתי' vs authority 'נחום בן שלמה'). More critically, P127 'owned by' uses '__LOCAL:mazal:987007267925005171' with object named as 'קזני, משה בן שמואל', but the MARC 700 shows 'קזני, משה בן שמואל' with role 'בעלים קודמים' (former owner), while the authority evidence shows 'שמואל בן משה' (Samuel ben Moses) with birth/death years 1350-1420, which appears to be a different person. The authority record shows 'שמואל בן משה' but the MARC shows 'משה בן שמואל' (Moses ben Samuel) - this is likely a name inversion error in the local reference matching. The scribe name string also appears inverted compared to the authority. These local reference mismatches need correction.

### 26. `990000860360205171` — partial

- **Label:** גורלות החול
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`, `SOURCE_LINKS`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / genre projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.

**AI evidence and issue explanation:**

> Labels and descriptions are clean and accurate. The manuscript type is appropriate. However, P136 genre='illuminated manuscript' (Q48498) is unsupported: the MARC 655 lists 'Illustrated works (Manuscript)' which is not identical to 'illuminated manuscript', and the 500$a mentions 'עיטורים' (decorations/ornaments) not illumination. Additionally, P31 has conflicting ranks: 'illuminated manuscript' is preferred while 'manuscript' is normal, but the evidence only supports 'manuscript' directly. The P2888 exact match property is used with a Wikibase.cloud URL which is non-standard (typically P2888 expects exact external matches, not internal project references). No P195 holding institution is present despite 710 indicating 'The Russian State Library' as current owner. These issues require cleanup before full approval.

### 27. `990000864590205171` — partial

- **Label:** כתר תורה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `PROVENANCE_MAPPING`, `SOURCE_LINKS`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label 'כתר תורה' and appropriate description. P31=manuscript, P1476 title, P571=1651, and P3959 NLI ID are all well-supported. However, P921=Karaite Judaism is weakly supported—the MARC 500 mentions 'כתות קראים' (Karaite sects) only as a secondary subject in a complex note, not as a primary subject warranting P921. The two P127 'owned by' statements use __LOCAL references that resolve correctly to former owners in local_reference_targets with matching Hebrew names and 'former owner' roles, so these are acceptable as provisional claims. The P2888 and P973 URLs to mhm-hmo.wikibase.cloud appear to be circular/self-referential to the same upload system rather than independent evidence sources, but this doesn't block approval. Main concern is the over-specific P921 claim not clearly primary in MARC.

### 28. `990000880550205171` — partial

- **Label:** תרגום ערבי לתורה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> Labels and descriptions are accurate: Hebrew title matches MARC 500$a and title field, description correctly identifies as Hebrew manuscript with date and holding institution. Entity type 'manuscript' is appropriate. However, P11603 'transcribed by' uses local reference __LOCAL:mazal:987007294122705171 which maps to a person with authority evidence role 'scribe', but P11603 specifically means 'transcribed by' (a person who transcribed a written work). The MARC 561$a describes שלמה סידיש as the copyist/scribe ('כתבו') which aligns with 'scribe' role, but 'transcribed by' (P11603) is semantically appropriate for a scribe. The P127 'owned by' with local reference __LOCAL:mazal:987007310752205171 has authority evidence role 'former owner', but the property used is 'owned by' (P127) which is acceptable for former ownership. However, the qualifier P1932 value 'בירב, אהרן' uses inverted Hebrew form while the authority evidence shows 'ביר, אהרן' - there's a discrepancy in the surname (בירב vs ביר). The MARC 561$a shows 'אהרן בירב' but the authority evidence shows 'ביר, אהרן' with birth/death years. This name mismatch needs verification. Also, the P136 genre 'illuminated manuscript' is supported by MARC 655 'Illustrated works (Manuscript)' and 500$a mentions 'שער מעוטר' (decorated gate/title page).

### 29. `990000880710205171` — partial

- **Label:** תרגום ארמי לכתובים (דברי הימים)
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`, `INSCRIPTION_EXTRACTION`, `SOURCE_LINKS`, `SCRIBE_RECONCILIATION`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / genre projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.

**AI evidence and issue explanation:**

> The Hebrew label matches the MARC 245 title. The description is appropriate. However, P136 'illuminated manuscript' with 'presumably' qualifier is unsupported—the MARC 500 notes describe a bilingual translation manuscript with Latin and Arabic content, not illumination. P1684 inscription claim is accurate per the 500$a colophon note. The P2888 and P973 URLs point to a Wikibase.cloud instance which appears to be a mirror/proxy rather than an authoritative source; these should use the NLI Ktiv URL directly. No P195 (collection) or P11603 (scribe) statements are present despite the 710 indicating 'The Bodleian Libraries, University of Oxford' as current owner and the 500 mentioning Samuel Clarke as copyist—these relationships are missing.

### 30. `990000927260205171` — partial

- **Label:** לקט מכתבים
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `GENRE_MAPPING`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label 'לקט מכתבים' is clean and matches the MARC 245 title. The manuscript type (Q87167) is appropriate. However, P136 genre claims include 'negotiable instrument' (Q3359388) which is unsupported—the 500$a describes a collection of letters, sermons, and writings by Eliezer Azikri, not financial instruments. The 'Pinkas' (Q2095829) genre is also weakly supported; while the record mentions a 'פנקס' (community ledger), this is one component among many, not the primary genre. P921 subjects 'Kabbalah' and 'sermon' are acceptable given the 650 fields and content description. The 'autograph' genre is correct per 655 and 500$a. The 'piyyut' genre is supported by mention of 'ידיד נפש'. Remove unsupported 'negotiable instrument' and reconsider 'Pinkas' as primary genre.

### 31. `990001028160205171` — partial

- **Label:** פרוש התורה לבחיי בן אשר
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `DATE_EXTRACTION`, `SCRIBE_RECONCILIATION`, `HOLDING_INSTITUTION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and description. P31 (manuscript), P1476 (title), P571 (1667), P3959, P2888, and P973 are well-supported. P136 (piyyut) is supported by 655$a. P921 (Halakha) is weakly supported by 650$a 'Jewish law' but is acceptable. The P11603 claim uses __LOCAL:mazal:987007507328605171 which is valid per local_reference_targets showing a person entity with scribe role in authority_evidence. However, P11603 'transcribed by' should point to a human (scribe), and the local target is correctly typed as 'person', but the property P11603 requires a human who transcribed a written work. The MARC 700 shows role '(מעתיק)' [scribe/transcriber] for סעדיה בן שלמה, so the role is correct. The issue is that P11603 with a __LOCAL reference and P1932 qualifier 'סעדיה בן שלמה' is acceptable for a two-pass upload, but the qualifier P1480 'presumably' is unnecessary since the MARC explicitly identifies this scribe. The main concern: P11603 should use P195 for holding institution, not for scribe, but here it's correctly used for a person. Actually re-reading: P11603 is correctly used here (transcribed by). The __LOCAL reference is valid. However, there's a mismatch: the authority_evidence shows preferred_name_heb as 'טויל, סעדיה בן שלמה' (inverted) but the P1932 shows 'סעדיה בן שלמה' (natural order). This is acceptable. The real issue: P11603 with __LOCAL is acceptable, but the statement lacks final QID resolution. Given this is a two-pass upload, it's acceptable. However, I notice P136 'piyyut' is claimed but the manuscript is primarily a Torah commentary (פרוש התורה) with piyyutim included per 500$a. The genre might be better as 'commentary' or multiple genres. But P136 is not wrong, just incomplete. The P921 'Halakha' is supported by 650$a 'Jewish law' and 500$a mentions 'הלכה דינים ומנהגים'. Overall safe but P11603 with __LOCAL needs resolution in pass 2.

### 32. `990001039720205171` — partial

- **Label:** שתיל פורח
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `GENRE_MAPPING`, `INSCRIPTION_EXTRACTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label 'שתיל פורח' matches the MARC 500$a and title field. The description is accurate. The manuscript type (Q87167) is correct. However, P136 'autograph' is unsupported—the MARC 500$a mentions 'אוטוגרף' (autograph) as a subject note, not as a genre/form statement; the 655 genre field lists 'Autograph manuscripts' but this is a subject/genre hybrid, not a clean P136 claim. P921 'Kabbalah' is weakly supported by the 650 'Cabala' heading, but the record's primary subject is the specific work 'שתיל פורח', not Kabbalah broadly. P1574 uses a __LOCAL reference which is acceptable since the target exists in local_reference_targets. P1684 inscription contains catalog notes about a colophon, but the P3831 qualifier 'object has role' with Q372474 (which appears to be 'colophon' or similar) lacks a value_label and the property_label is null, indicating incomplete data. The inscription value also mixes catalog commentary with the actual inscription text. These issues require cleanup but do not invalidate the core item.

### 33. `990001056990205171` — partial

- **Label:** תחלת דבר : חבור בחכמת ההגיון
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `GENRE_MAPPING`, `DATE_EXTRACTION`, `AUTHOR_CHAIN`, `SCRIBE_RECONCILIATION`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> Labels and descriptions are clean and accurate. The manuscript type (Q87167) is correct. However, P136 'autograph' is unsupported: the 500$a mentions 'אוטוגרף' (autograph) as a subject note, not as a genre/form designation. The 655 genre field lists 'Autograph manuscripts' but this is a subject/genre heading, not evidence that this specific manuscript is an autograph. P921 'philosophy' is weakly supported by the 650 'Philosophy' heading, but the record's primary focus is the specific work on logic, not philosophy broadly. The 500$a also lists 'פילוסופיה כללית' (general philosophy) as an additional subject, making this a borderline case. Missing: P50/P2093 author link to 'קונציו, יוסף בן גרשום' from 100 field, P195 holding institution (The British Library from 710), and P11603 scribe if applicable. The P571 date 1605 appears in the description but lacks direct MARC evidence in the provided fields; the 561 mentions תקצח (1598) for a different manuscript. These issues warrant partial status.

### 34. `990001089360205171` — partial

- **Label:** תקון חצות
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `WORK_FOLIO_MAPPING`, `SCRIBE_RECONCILIATION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The manuscript item is generally well-constructed. Labels and descriptions are accurate: Hebrew label 'תקון חצות' matches the 500$a and title field; description correctly notes 1676 date, NLI holding, and subjects. Entity type 'manuscript' is correct. However, P921 'main subject' = Kabbalah (Q123006) is weakly supported: while 'Cabala' appears in 650, the record emphasizes 'תשובה' (Repentance) and 'ראש חדש' (Rosh Hodesh) as primary subjects, with Kabbalah as 'נושא נוסף' (additional subject). The P1574 'exemplar of' claim uses a local reference to 'work:סדר_משמרת_החדש' which is present in local_reference_targets with appropriate type/label, so this is acceptable. The P11603 'transcribed by' local reference to 'mazal:987007270155705171' is supported by authority_evidence showing this person as 'scribe'. The main issue is P921 being too generic and not the primary focus; it should be removed or demoted. No other validation issues.

### 35. `990001118020205171` — partial

- **Label:** מצות נשים מלמדה : הלכות נדה, חלה, הדלקה ודרך החיים באיטלקית יהודית (אותיות עבריות)
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> Labels and descriptions are accurate: Hebrew title matches MARC 245/500, description correctly notes 1607 date and NLI holding. Entity type 'manuscript' is appropriate. However, P921 'Halakha' is weakly supported—the MARC 500 mentions 'הלכה דינים ומנהגים' as a secondary subject, but the primary subjects in 650 are 'Jewish women' and 'Women (Jewish law)'. P921 should reflect the primary topical focus, not a generic halakhic category. The work candidate evidence shows 'פחות מבנדפס' was rejected, which is correct as it's a catalog note, not a title. No validation issues. Fix: replace P921 Halakha with more specific subject(s) from 650.

### 36. `990001135400205171` — partial

- **Label:** לוח מאמרי עין ישראל
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `SCRIBE_RECONCILIATION`
- **Primary fix layer:** RDF creator / property semantics
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Map current 710 ownership to P195/P127 as appropriate; do not use P127 for former owners, sellers, or censors. Represent historical ownership with provenance/event semantics and qualifiers, keeping the MARC role explicit.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and description. However, P127 'owned by' uses a __LOCAL reference to Camillo Jagel with qualifier P1932 'object named as' = 'Camillo Jagel', but the MARC 561 field clearly states 'Censor: Camillo Jaghel 1619' and the contributor role is 'censor', not owner. The P127 claim misrepresents the relationship—Jagel was the censor, not the owner. The current owner per 710 is 'The Jewish Theological Seminary of America'. The P127 statement should use P11603 (censor) or be removed, and ownership should reflect the actual holding institution.

### 37. `990001136560205171` — partial

- **Label:** מלות חבור
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `SOURCE_LINKS`, `SCRIBE_RECONCILIATION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label 'מלות חבור' matches the MARC 245 title field. The description correctly identifies this as a Hebrew manuscript from 1651 at NLI. P31=manuscript and P571=1651 are appropriate. However, P921=Halakha is weakly supported: the 500$a mentions 'הלכה' only as a secondary subject ('נושא נוסף: הלכה. פוסקים'), not as the primary subject, and the 650 lists 'Jewish law' generically. The scribe 'סעדיה בן נאחום' from 700 is missing from statements—P11603 should be added. P2888 and P973 both point to the same external Wikibase URL which is redundant but not incorrect. The item needs the scribe statement and should reconsider P921 or add qualifiers.

### 38. `990001192130205171` — partial

- **Label:** תכלאל
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label 'תכלאל' matches the MARC 245 title field. The manuscript type (Q87167) is correct. However, P921 'main subject' = 'prayer' (Q40953) is weakly supported: while 'Prayer' appears in 650$a, the MARC shows it as one of multiple subjects with 'Judaism' appearing three times, and the 500$a describes a complex compilation (Passover Haggadah, Lamentations commentary, calendar) rather than prayer as primary subject. The description's 'Subjects include Judaism' is accurate but P921 should be more specific or omitted. No authority evidence conflicts. The inception date 1654 (תי"ד) is supported by 500$a as likely copying year.

### 39. `990001205840205171` — partial

- **Label:** תורה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `WORK_FOLIO_MAPPING`, `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The manuscript item has clean labels and appropriate type. However, P921 'main subject' = Q9288 'Hebrew' is unsupported: the 650 field shows 'Hebrew language' as a subject, but 'Hebrew' (the language entity) is too generic and not the primary subject of a Torah manuscript. The P11603 'transcribed by' claim uses a local reference to a person with label 'Seʻadyah ben Yosef Ibn Danan' but the MARC 700 field shows the scribe as 'אבהר, יוסף בן סעדיה בן יוסף בן דוד' (Ahar, Yosef ben Saadiah ben Yosef ben David), which appears to be a different name than the local target's labels suggest; the name string qualifier matches the MARC but the target identity may be mismatched. The P1574 'exemplar of' claims to local works are properly supported by the 500 field work candidate evidence.

### 40. `990001219490205171` — partial

- **Label:** תכלאל
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label 'תכלאל' matches the MARC title field. The manuscript type (Q87167) is correct. However, P11603 'transcribed by' uses a __LOCAL reference to mazal:987007312320105171, but the local reference target shows this person (Yosef Yitsḥaḳ Shelush, 1891-1960) has authority evidence identifying them as a 'scribe'—yet the P11603 claim uses P1932 with a completely different name string 'אלחגאג', חיים בן שלום בן דוד בן יצחק' which appears in the MARC 700 field as a contributor with role '(מעתיק)' [transcriber]. The local target person and the P1932 name string do not match; this appears to be a data linkage error where the wrong local person was attached. The P127 'owned by' Q111719671 has no visible label in the context and cannot be verified against the MARC evidence (which mentions 'The British Library' as current owner in 710). These mismatches require correction before approval.

### 41. `990001238980205171` — partial

- **Label:** מנחת יהודה : פרוש על שמואל, מלכים וישעיהו
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`
- **Primary fix layer:** RDF creator / genre projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.

**AI evidence and issue explanation:**

> The Hebrew label matches the 245 title field. The description is accurate for a 1618 Hebrew manuscript at NLI. P31=manuscript, P1476 title, P571=1618, and P3959 NLI ID are all well-supported by the MARC context. However, P136=autograph (Q9026959) is questionable: the 500$a mentions the manuscript was returned to 'אקא' in 1619 and references other manuscripts, suggesting it may not be the author's own autograph but rather a copy. The 655 'Autograph manuscripts' genre heading appears in MARC but the 500 note about return to another location and existence of other copies (Jerusalem, Liverpool) undermines autograph certainty. P136=poetry is partially supported by mention of a kabbalistic poem at the end, but this is a minor component of a biblical commentary manuscript, not primarily poetry. P921=Kabbalah is supported by 650 'Cabala' and 500$a content about 'דרך הקבלה', though the work is primarily biblical commentary with kabbalistic elements. The item needs curator review for genre claims.

### 42. `990001253400205171` — partial

- **Label:** מחזור מנהג אשכנז המערבי (וורמיזא) לכל השנה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> Labels and descriptions are accurate: Hebrew title matches MARC 500$a and title field, description correctly notes 1651 date and NLI holding. Entity type 'manuscript' is appropriate. However, P127 'owned by' uses __LOCAL:mazal:987007307983105171 with object named as 'הוניק, יצחק בן אלכסנדר' — the MARC 561$a and 700 field show the former owner as 'יצחק בר אלכסנדר הוניק' (Yitzhak son of Aleksander Honik), while the local reference target's labels show 'אלכסנדר בן יצחק איזיק' (Aleksander son of Yitzhak Izik) — these are reversed generations. The P127 claim appears to conflate father and son or misassign the name direction. The qualifier P1932 value 'הוניק, יצחק בן אלכסנדר' also inverts the name order compared to MARC evidence. This is a significant role/identity error that needs correction.

### 43. `990001254240205171` — partial

- **Label:** קול ברמה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and appropriate description. The entity type (manuscript) is correct. However, P11603 'transcribed by' uses a local reference to mazal:987007299516905171, but the authority evidence shows this person (Yitsḥaḳ ben Shelomoh Ḥayim Gabai) has role 'scribe' while the MARC 700 field shows 'גבאי, טוביה בן חיים יצחק' with role '(מעתיק)' [transcriber]. The name strings differ: 'טוביה' vs 'יצחק' and the authority record shows 'יצחק בן שלמה בן חיים' vs 'טוביה בן חיים יצחק'. These appear to be different name forms or potentially different people. The P127 'owned by' claim is properly qualified as 'presumably' and matches the MARC 561 former owner note. The P921 'Kabbalah' subject is supported by 650 and 500 fields. The P136 'autograph' genre is supported by 655 and 500 fields mentioning autograph. The 'illuminated manuscript' claim has appropriate 'presumably' qualifier. The main issue is the name mismatch on the transcriber/scribe.

### 44. `990001286970205171` — partial

- **Label:** תרגום ערבי לתורה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `INSCRIPTION_EXTRACTION`, `DATE_EXTRACTION`, `SCRIBE_RECONCILIATION`
- **Primary fix layer:** RDF creator / property semantics
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Map current 710 ownership to P195/P127 as appropriate; do not use P127 for former owners, sellers, or censors. Represent historical ownership with provenance/event semantics and qualifiers, keeping the MARC role explicit.

**AI evidence and issue explanation:**

> The Hebrew label 'תרגום ערבי לתורה' matches the MARC 245 title field. The description correctly identifies this as a Hebrew manuscript from 1682 at NLI. The entity type 'manuscript' is appropriate. However, P1684 (inscription) contains 'נושא נוסף: כתב-יד. מכירה' which appears to be a catalog note about 'additional subject: manuscript, sale' rather than an actual inscription on the manuscript itself. The P3831 qualifier with Q372474 (object has role) is misapplied here—this is not a proper inscription statement. The P127 'owned by' with __LOCAL reference to 'mazal:987007451406105171' is supported by the authority evidence showing 'סלימן בן סאלם' as former owner, though the P1932 value 'סלימן בן סאלם' differs slightly from the authority's 'עלי בן סולימאן'—the MARC 561 shows 'סלימאן בן סאלם' which matches the P1932. The P11603 'transcribed by' correctly references the scribe per MARC 700 with role '(מעתיק)'. The P571 date 1682 is not directly visible in the provided MARC fields but may come from the full record. The main issue is the P1684 claim which misrepresents a catalog subject note as a manuscript inscription.

### 45. `990001340200205171` — partial

- **Label:** הגדה של פסח : מנהג אשכנז
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`, `PROVENANCE_MAPPING`, `INSCRIPTION_EXTRACTION`
- **Primary fix layer:** RDF creator / genre projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.

**AI evidence and issue explanation:**

> Labels and descriptions are accurate: Hebrew title matches MARC 245, description correctly identifies as Hebrew manuscript with date and holding institution. Entity type 'manuscript' is appropriate. However, P136 genre 'family register' (Q485228) is unsupported - the MARC 500 mentions 'רשימות משפחתיות' (family records) as a subject topic, not as a genre of the manuscript itself. The manuscript is a Passover Haggadah with family ownership history, not a family register as its genre. P127 owned by '__LOCAL:mazal:987007340200205171' has incorrect local reference ID - should be '987007304601505171' per the local_reference_targets. P1684 inscription value appears to be a garbled extraction from 500 field notes rather than an actual inscription text.

### 46. `990001343040205171` — partial

- **Label:** תכלאל
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `GENRE_MAPPING`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label 'תכלאל' matches the MARC 245 title field. The manuscript type (Q87167) is correct. However, P136 'illuminated manuscript' with 'presumably' qualifier is unsupported by the MARC 500$a content, which describes a Yemenite prayer book with Arabic instructions and marginal commentary but mentions no illumination. The P195 collection claim uses Q188915 without a visible label; while NLI holding is plausible, the MARC shows former owners (Sassoon, Valmadonna) and current owner NLI in 710/561 fields, but Q188915's identity is unverified. The P921 'Judaism' subject in description is generic but present in 650 fields. No authority evidence conflicts. The unsupported genre claim warrants partial role_ok.

### 47. `990001376840205171` — partial

- **Label:** שיר היחוד
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `GENRE_MAPPING`, `SCRIBE_RECONCILIATION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The manuscript item is well-structured with clean Hebrew label 'שיר היחוד' matching the MARC 500$a and title field. The type (manuscript Q87167) is correct. However, P921 'brit milah' is weakly supported: while 'ברית מילה' appears in the 500$a as context for blessings, the primary subject of this manuscript is the piyyut 'שיר היחוד' itself, not the circumcision ritual. The 650 subject heading 'Berit milah' is present but this is a generic topical heading, not the manuscript's main subject. P11603 uses a local reference '__LOCAL:mazal:987007262418105171' which is valid per local_reference_targets showing a scribe role in authority_evidence. The P136 genre 'piyyut' is correct per 655. The date 1663 appears in the description but lacks explicit MARC evidence in provided fields; however this is common for manuscripts and the description format suggests catalog-derived data. Suggested fix: remove or demote P921 brit milah as it overstates a secondary contextual element.

### 48. `990001379460205171` — partial

- **Label:** חזוק אמונה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `COMMENTATOR_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label 'חזוק אמונה' matches the MARC 500$a and title fields. The manuscript type (Q87167) is correct. The inception year 1602 is plausible for a manuscript. However, P9046 'commentary by' uses a local reference '__LOCAL:mazal:987007397668405171' which is valid per local_reference_targets, but the qualifier P1932 'object named as' contains 'קרמטיל, עקיבא בן אברהם' which appears to be an inverted name form. The authority evidence shows the preferred Latin name as 'קרמטיל, עקיבא בן אברהם' and Hebrew as 'דראך, עקיבא בן אברהם'. The P1932 value should likely be the natural-order form or match the authority preferred name more precisely. Additionally, the MARC 700 field shows this person as 'מעיר' (annotator/commentator), which supports the role, but the name form in P1932 could be improved. The description mentions 'Messiah; Religious disputations' which matches the 650 subjects. No validation issues are present. The item is mostly correct but the P1932 qualifier value should be reviewed for proper name form.

### 49. `990001400870205171` — partial

- **Label:** Meir Netiv in Latin
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`
- **Primary fix layer:** RDF creator / genre projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.

**AI evidence and issue explanation:**

> The label 'Meir Netiv in Latin' matches the MARC 245 title. The description 'Hebrew manuscript, 1627, National Library of Israel' is appropriate. However, P136=autograph (Q9026959) is unsupported: the MARC 655 says 'Autograph manuscripts' but the 500$a clarifies this is a 'Translation...by Nicholas Fuller' — a translation by a named translator, not an author's own manuscript. The 700 field shows Fuller as translator, not as author of an autograph. The genre claim should be removed or changed to reflect it's a translation, not an autograph.

### 50. `990001402000205171` — partial

- **Label:** עשרת הדברות וקטע מקריאת שמע
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `DATE_EXTRACTION`, `SOURCE_LINKS`
- **Primary fix layer:** RDF creator / date normalization
- **Primary difficulty/effect:** Medium / High
- **First implementation action:** Separate publication/copy dates from cataloging dates, normalize BCE Wikibase time syntax (for example `-0199-00-00T00:00:00Z`), retain precision, and suppress dates that are only inferred from a generic description.

**AI evidence and issue explanation:**

> The Hebrew label matches the MARC title field (245$a). The entity type 'manuscript' is appropriate for a papyrus fragment. However, P571 (inception) with value '+-199-00-00T00:00:00Z' appears to encode a BCE date (2nd century BCE for the Nash Papyrus) but uses invalid Wikidata syntax—the leading '+-' with year 199 is malformed; BCE dates should use '-0199-00-00T00:00:00Z'. The description's subject 'Miscellaneous prayers (Jewish liturgy)' is catalog bracket pollution from the 650 field and should be removed. No P921 should be asserted for generic 'Judaism' without stronger evidence. The P2888 and P973 URLs point to an external Wikibase (mhm-hmo.wikibase.cloud) which is acceptable as reference but the malformed date requires correction.

### 51. `990001406710205171` — partial

- **Label:** אוצרות חיים
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `DATE_EXTRACTION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label 'אוצרות חיים' matches the MARC 500$a and title fields. The manuscript type (Q87167) is correct. P571 (+1643) is reasonable for a Vital manuscript. P921 (Kabbalah) is supported by 650 'Cabala' and 500$a content. However, P127 uses '__LOCAL:mazal:987007305889305171' with P1932 'דה-פריס, יעקב בן יצחק' — the local reference target shows 'preferred_name_lat': 'דה-פריס, יעקב בן יצחק' but 'preferred_name_heb': 'פרידמן, יצחק בן יעקב' with birth/death years 1900-1968. The MARC 561$a suggests 'יעקב בן יצחק די פרימן' (possibly Friedman/de Vries) from Leverden, Netherlands, but the authority evidence shows a 20th-century person (1900-1968) while the manuscript is 1643. The P127 claim with 'presumably' qualifier is problematic: the entity type is 'person' but the dates don't align with a 1643 manuscript ownership chain. The role 'former owner' in authority_evidence is for a modern person, not plausible for a 17th-century manuscript. This needs verification or removal.

### 52. `990001499320205171` — partial

- **Label:** מחזור מנהג קרפנטרץ לראש השנה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and description. P31 (manuscript), P1476 (title), P571 (1648), P3959, P2888, and P973 are well-supported. However, P11603 'transcribed by' uses a __LOCAL reference to 'mazal:987007453092705171' which has authority_evidence showing preferred_name_lat 'דלפוגיט, אליהו' — but the P1932 qualifier shows 'דלפוגיט, אליהו' while the local target's Hebrew label is 'דליה אליהו-קאולי'. The MARC 700 field shows '"דלפוגיט, אליהו"' with role '"(מעתיק)"' (scribe/transcriber). The name mismatch between the Latin-script form in P1932 and the Hebrew label in the local target suggests the local target may represent a different person or the name parsing is inconsistent. The P1480 'presumably' qualifier is appropriate given the uncertainty. The role_ok is partial because the local reference target's identity is questionable given the name discrepancy, though the scribe role itself is supported by MARC 700.

### 53. `990001580110205171` — partial

- **Label:** אור הרופאים
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `GENRE_MAPPING`, `SCRIBE_RECONCILIATION`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The Hebrew label 'אור הרופאים' matches the 500$a and title fields. The manuscript type (Q87167) is correct. However, P136=autograph (Q9026959) is unsupported: the 500$a mentions 'בכתיבה מאוחרות יותר' (in a later hand) for the cover page, and the 655 genre is 'Autograph manuscripts' but this appears to be a catalog classification, not evidence that this specific manuscript is an autograph. The 500$a explicitly states the cover page is in a later hand, suggesting the main text may also not be autograph. P921=medicine is supported by 650$a 'Medicine' and the title/content. Missing: P195 (holding institution) should be National Library of Israel (implied by catalog source), and P11603 (scribe) should be considered for יעקב בן יצחק צהלון mentioned in 500$a as the author/commentator, not necessarily the scribe of this copy.

### 54. `990001792890205171` — partial

- **Label:** סדור מנהג קרפנטרץ לראש השנה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SCRIBE_RECONCILIATION`
- **Primary fix layer:** Authority reconciliation + RDF creator
- **Primary difficulty/effect:** Medium–High / Very high
- **First implementation action:** Attach P11603 only when the target identity matches the MARC 700/500 role and name. Require a resolved label for final export; retain `P1932` as the catalog name, but do not use it to mask a wrong target.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and description. P31 (manuscript), P1476 (title), P571 (1696 inception), and P3959/P973/P2888 (catalog links) are all well-supported. However, P11603 'transcribed by' uses a __LOCAL reference to a person with role 'scribe' in authority_evidence, but the MARC 700 field shows this person as '(מעתיק)' [transcriber] while another 700 entry for 'כרמי, אליהו בן משה' has role '(ממנו)' [from whom/former owner] which is not captured. The P11603 claim is acceptable but incomplete regarding the full contributor picture; the role modeling is mostly correct but the single transcribed-by claim doesn't fully represent the MARC contributor data.

### 55. `990001800310205171` — partial

- **Label:** פרקי רבי אליעזר
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label 'פרקי רבי אליעזר' matches the MARC 245 title field. The manuscript type (Q87167) is appropriate. The inception date 1634 is supported by the 561$a colophon date. However, the P127 'owned by' claim uses '__LOCAL:mazal:987007494055705171' which is acceptable as a local reference, but the P1932 qualifier 'חבני, גיילה יחיא בן מע[וצ']ה' appears to conflate two different names: the Hebrew preferred name from authority evidence is 'אלמוושד, מעוצ'ה בן יחיא' while the Latin form shows 'חבני, גיילה יחיא בן מע[וצ']ה'. The P1932 value seems to mix these forms incorrectly. The description mentions 'Midrash' as subject which is supported by 650$a and 500$a, though P921 is not explicitly stated in the sample. The item is mostly correct but the P1932 qualifier needs correction to match the actual name form from the authority evidence.

### 56. `990001801390205171` — partial

- **Label:** כתב יד עברי, ספרייה לאומית, 990001801390205171
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `WORK_FOLIO_MAPPING`
- **Primary fix layer:** RDF creator / work-candidate parser
- **Primary difficulty/effect:** High / Medium–High
- **First implementation action:** Parse 505 ranges with a monotonic/non-overlap check, preserve gaps explicitly, reject contradictory ranges, and attach P958 qualifiers only after validation. Keep accepted work evidence even when a folio qualifier is withheld.

**AI evidence and issue explanation:**

> The manuscript item has accurate labels and descriptions with proper Hebrew title 'קובץ' and English catalog-style label. The entity type 'manuscript' is correct. However, the P1574 'exemplar of' statements show a significant data quality issue: the folio ranges in the qualifiers contain overlapping and contradictory page ranges that appear to be incorrectly parsed from the 505 field. For example, 'כתובים' spans 1א-40ב, 45ב-65א, 65ב-78א (with gaps), while 'סדור מנהג אשכנז' spans 79ב-274ב which overlaps with 'תשב"ץ' at 79ב-123ב and 'ספר היראה' at 124א-133ב. More critically, 'משל הקדמוני' at 298ב-371א overlaps with 'ספר המעלות' at 298א-334א, and 'בן המלך והנזיר' at 372ב-418א overlaps with 'מנחת יהודה' at 382א-393א and 'סוד הסודות' at 418ב-433ב. The 505 field clearly shows these as separate sequential items, but the folio parsing has created physically impossible overlapping ranges. These overlapping ranges need correction before the item can be approved.

### 57. `990001827870205171` — partial

- **Label:** משנה תורה (ספר מדע, אהבה, זמנים)
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `INSCRIPTION_EXTRACTION`, `SCRIBE_RECONCILIATION`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / property semantics
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Map current 710 ownership to P195/P127 as appropriate; do not use P127 for former owners, sellers, or censors. Represent historical ownership with provenance/event semantics and qualifiers, keeping the MARC role explicit.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and English description. The entity type 'manuscript' is correct. However, there are significant role modeling issues: (1) P127 'owned by' is used for three former owners (דמרמרי/מוסי עיאל בן דאוד, גברא/יחיא בן דוד, אלפקעה/שלמה בן סעדיה) but the MARC 561 shows these as historical owners from a sales deed, not current owners; P127 should be P793 (significant event) with P580/P582 for ownership periods or use P793 with 'former ownership'. (2) The P11603 'transcribed by' claim for אלעדוי/יוסף בן עמרם בן עודד is supported by 700 field role '(מעתיק)' = copyist/transcriber, but the qualifier P1932 value 'אלעדוי, יוסף בן עמרם בן עודד' is Latin-script inverted form while the Hebrew name should be 'יוסף בן עמרם אלעדוי'. (3) The P1684 inscription contains catalog notes mixed with provenance, not a clean inscription text. (4) Missing: P195 for holding institution (National Library of Israel), and the scribe סעדיה בן שלמה טויל from authority evidence is not linked. The local references are valid and will resolve in two-pass upload.

### 58. `990001830400205171` — partial

- **Label:** מחזור מנהג קרפנטרץ (לשלאה) לראש השנה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SCRIBE_RECONCILIATION`
- **Primary fix layer:** Authority reconciliation + RDF creator
- **Primary difficulty/effect:** Medium–High / Very high
- **First implementation action:** Attach P11603 only when the target identity matches the MARC 700/500 role and name. Require a resolved label for final export; retain `P1932` as the catalog name, but do not use it to mask a wrong target.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and description with proper year 1688 from MARC. However, P11603 'transcribed by' points to Q118161349 with no value_label provided, and the MARC 700 field shows 'דדיניאה, יהודה בן יצחק' with role '(מעתיק)' [scribe/transcriber]. Without authority_evidence confirming this QID matches that name, the statement is unverified. The P921 'Judaism' subject in description is generic but acceptable; no P921 statement is present in the sample. The missing value_label for P11603 is a data quality issue that should be resolved before full approval.

### 59. `990001856120205171` — partial

- **Label:** סדור מנהג קרפנטרץ (לשלאה) לראש השנה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label matches the MARC title field. The entity type 'manuscript' is correct. However, P11603 'transcribed by' uses a __LOCAL reference to mazal:987007304034505171, but the MARC 700 field shows the scribe as 'בידרידוש, נתן יהושע בן שלמה' while the local target's authority evidence shows 'שלם, נתן' (Nathan Shalem, 1897-1959). These are different people: the manuscript scribe is Nathan Joshua ben Solomon Bedershi (17th century), not Nathan Shalem (20th century). The P1932 qualifier correctly shows the Bedershi name, but the item reference points to the wrong person. This is a significant role error that needs correction.

### 60. `990001858880205171` — partial

- **Label:** תקנות חברת בקור חולים בוירונה
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`, `SCRIBE_RECONCILIATION`
- **Primary fix layer:** RDF creator / genre projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.

**AI evidence and issue explanation:**

> Labels and descriptions are accurate: Hebrew title matches MARC 500$a and title field, description correctly notes 1687 date and NLI holding. Entity type 'manuscript' is appropriate. However, P11603 'transcribed by' points to Q118924043 with null value_label and no authority evidence confirming this person is the scribe. The MARC 700 field shows 'בסן, חזקיה מרדכי בן שמואל חיים' with role '(מעתיק)' [transcriber/scribe], which supports a transcriber claim, but the QID linkage is unverified in the provided context. Without confirmation that Q118924043 corresponds to this Hebrew name, the statement is unsupported. Additionally, P136 'genre' = 'Halakha' is plausible given 'Legislation (Jewish law)' in 655 and 'תקנות' [regulations] in the title, but the description's claim of 'Miscellaneous prayers' as primary subject is somewhat at tension with the Halakha genre assignment. The item needs verification of the transcriber QID before full approval.

### 61. `990001869850205171` — partial

- **Label:** מגן אהרן
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `SCRIBE_RECONCILIATION`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> Labels and descriptions are clean and accurate. The manuscript type (Q87167) is correct. However, P921 'Kabbalah' is weakly supported: the MARC 650 lists 'Cabala' as a subject, but the 500$a note shows this is primarily a polemical work defending the author's opposition to 'Kinnor David' by Joseph Jedidiah Carmi, with Kabbalah mentioned only as an additional topic ('נושא נוסף: קבלה'). P921 should reflect the primary subject, not a secondary topic. The scribe 'שושן, יצחק בן חת' and the mentioned author 'קרמי, יוסף ידידיה' from 700 fields are absent from statements, and P11603 for the scribe is missing. No P195 for holding institution (Montefiore Library/Halberstam collection). These omissions and the weak P921 claim make role_ok partial.

### 62. `990001875220205171` — partial

- **Label:** שלחן ערוך (ארח חיים)
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`, `PROVENANCE_MAPPING`, `INSCRIPTION_EXTRACTION`, `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> Labels and descriptions are accurate: Hebrew title matches MARC 500/561, description correctly identifies as Hebrew manuscript with date 1694 and NLI holding. Entity type 'manuscript' is appropriate. However, P127 'owned by' statements use __LOCAL references that appear in local_reference_targets with plausible person types, but the qualifier P1932 values show significant name mismatches against the authority evidence: 'אלחמדי, יוסף בן דוד' vs authority 'בן דוד, יוסף'; 'אלתאם, יהודה בן יחיא' vs authority 'בדיחי, יחיא בן יהודה'; 'אלחמדי, יחיא בן דוד' vs authority 'אל חמדי, דוד בן מסעוד'; 'סעיד בן סלם סעיד' vs authority 'סעיד בן מסלם'. These appear to be conflated or incorrect name strings that don't match the authority records. The P11603 transcribed by statement also has name mismatch: 'אלקיסי, סעדיה בן שלמה' vs authority 'טויל, סעדיה בן שלמה'. P1684 inscription contains catalog notes not actual manuscript inscription text. P136 'illuminated manuscript' is marked 'presumably' without direct MARC evidence for illumination. These issues require correction before full approval.

### 63. `990001878130205171` — partial

- **Label:** מדרש הגדול (שמות)
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `DATE_EXTRACTION`, `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and description. The P11603 'transcribed by' claim uses __LOCAL:mazal:987007415787305171 with object named as 'אלפתיחי, יוסף בן יונה', but the MARC 700 field shows the scribe as 'אלפתיחי, יוסף בן יונה' (מעתיק) while the local target's authority evidence shows 'פתיחי, משה בן יוסף' with role 'scribe'. The name string in P1932 ('אלפתיחי, יוסף בן יונה') does not match the authority evidence for that local target ('משה בן יוסף פתיחי'). The P127 ownership claims correctly reference local targets with appropriate name strings matching the MARC 700 fields. The P571 date 1607 and P1476 title are supported. The mismatch in the P11603 name string vs. the authority evidence for the referenced local target requires correction.

### 64. `990001882630205171` — partial

- **Label:** מלאכת שלמה (סדר זרעים)
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`, `SOURCE_LINKS`
- **Primary fix layer:** RDF creator / genre projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.

**AI evidence and issue explanation:**

> The Hebrew label 'מלאכת שלמה (סדר זרעים)' matches the MARC 500$a and title fields. The description correctly identifies this as a Hebrew manuscript from 1622 at NLI. P31=manuscript and P571=1622 are supported. However, P136=autograph (Q9026959) is problematic: the MARC 655 lists 'Autograph manuscripts' as a genre, but the 500$a describes this as a manuscript containing the author's own work with autobiographical details, not necessarily an autograph (holograph) in the strict sense. More critically, P2888 and P973 both point to the same Wikibase.cloud URL, which appears to be a circular self-reference to the item being created rather than an external authority source. The P2888 claim should likely be removed as it doesn't represent an exact match to an external canonical resource.

### 65. `990001901440205171` — partial

- **Label:** מדרש הגדול (דברים)
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / property semantics
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Map current 710 ownership to P195/P127 as appropriate; do not use P127 for former owners, sellers, or censors. Represent historical ownership with provenance/event semantics and qualifiers, keeping the MARC role explicit.

**AI evidence and issue explanation:**

> The Hebrew label 'מדרש הגדול (דברים)' matches the MARC 245 title field. The entity type 'manuscript' is appropriate. However, P127 'owned by' with value '__LOCAL:mazal:987007581989405171' uses a local reference that is properly declared in local_reference_targets, but the qualifier P1932 'object named as' with 'יוסף בן סעדיה' and P1480 'presumably' is problematic: the MARC 700 field shows this person as 'בעלים קודמים' (former owner), not current owner. P127 'owned by' should indicate current ownership, but the MARC 710 shows 'San Francisco State University Library' as 'current owner'. The local reference target is correctly typed as 'person' with matching Hebrew name, but the property used (P127) and the role mapping conflict with the MARC evidence. P195 'collection' or P127 with current owner would be more appropriate; the former owner should use a different property like P807 or be modeled as a provenance statement.

### 66. `990001948980205171` — partial

- **Label:** דרשות
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `GENRE_MAPPING`, `AUTHOR_CHAIN`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> Labels and descriptions are clean and accurate. The manuscript type (Q87167) is correct. However, P136=autograph (Q9026959) is unsupported: the MARC 500$a mentions 'אוטוגרף' (autograph) but this refers to עבדאללה בן סוכר being the author, not that the manuscript itself is an autograph manuscript. The 655 genre is 'Autograph manuscripts' but this appears to be a cataloging term for the genre of the collection, not a claim that this specific manuscript is an autograph. More critically, the manuscript lacks author attribution (P50) for עבדאללה בן סוכר who appears in 100 and 700 fields, and lacks P195 for the holding institution (National Library of Israel, implied by the Ktiv source). The P921=sermon is reasonable given the title 'דרשות' and 650 subject, but the genre claim needs removal or verification.

### 67. `990019020880205171` — fail

- **Label:** פנקס המדינה : או פנקס ועד הקהלות הראשיות במדינת ליטא; קובץ תקנות ופסקים משנת שפג עד שנת תקכא, נדפס מכתב-יד הנמצא בהורודנא, עם מלואים ושנויי נוסחאות על פי העתקות הפנקס בבריסק ובווילנא
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=no`
- **Validation codes:** `none`
- **Issue tags:** `TYPE_CLASSIFICATION`, `INSCRIPTION_EXTRACTION`, `DATE_EXTRACTION`, `HOLDING_INSTITUTION`
- **Primary fix layer:** RDF creator / entity-kind inference
- **Primary difficulty/effect:** Medium / Very high for affected items
- **First implementation action:** Detect catalog indicators such as דפוס צלום (photographic print/facsimile) before defaulting to manuscript; derive the date from publication/copy evidence, not a cataloging timestamp; add a regression fixture.

**AI evidence and issue explanation:**

> The entity type is incorrectly set to 'manuscript' when the MARC 500$a clearly states this is a 'דפוס צלום' (photographic print/facsimile edition) from 1925 (תרפ"ה), not an original manuscript. The description 'Hebrew manuscript, 1969' is wrong—1969 appears to be a cataloging date, not the work's origin. The P571 date of 1969 is unsupported; the actual publication is 1925. The P1684 inscription claim with 'Book suggested to Google; rejected' appears to be internal catalog noise, not an actual inscription. The P195 collection claim lacks value_label verification. The Hebrew label itself is accurate per the 245 title field, but the entity is mischaracterized.

### 68. `990025903200205171` — partial

- **Label:** מגלת אסתר
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `PROVENANCE_MAPPING`, `DATE_EXTRACTION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The Hebrew label 'מגלת אסתר' is clean and accurate per MARC 245/500. The manuscript type and illuminated manuscript genre are supported by 500$a describing illustrations ('ציורי פרחים וחיות בצבעים'). P571 year 1662 matches the Hebrew date התכ"ב (1662) in 561$a. However, P127 'owned by' uses __LOCAL:mazal:987007258460505171 with value_label null, but the local_reference_targets shows this person as 'Refael Ben Haim' / 'רפאל בן חיים' while the P1932 qualifier shows 'אלטינו, אלישע בן חיים רפאל' — these are different people. The MARC 561$a and 700 field clearly identify the former owner as 'אלטינו, אלישע בן חיים רפאל' (Elisha Altino), not Refael Ben Haim. The local reference target appears to be the wrong person (possibly a cataloging error linking to a different authority record). The P127 claim should use the correct person or remain as a string name until properly resolved.

### 69. `990035044030205171` — partial

- **Label:** ספר החרוזים
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `INSCRIPTION_EXTRACTION`, `DATE_EXTRACTION`, `SCRIBE_RECONCILIATION`, `AUTHORITY_RECONCILIATION`
- **Primary fix layer:** Authority enrichment / reconciliation
- **Primary difficulty/effect:** High / Very high
- **First implementation action:** Require normalized-name plus role/chronology agreement before attaching a local person/work target. If the MARC name and authority target disagree, keep a named string/evidence record and omit the item-valued claim until reconciled.

**AI evidence and issue explanation:**

> The manuscript item has accurate Hebrew label and appropriate description. The P11603 'transcribed by' claim uses a __LOCAL reference to mazal:987007261158205171, which exists in local_reference_targets with entity_type 'person' and authority_evidence showing role 'scribe'. However, the P1932 qualifier value 'מונדולפו, שמואל בן אברהם' does not match the local target's preferred name 'פירקוביץ, אברהם בן שמואל' (Abraham Firkowitsch). The MARC 500 field mentions 'שמואל יזיי"א בהמ' אברהם זלה"ה ממונדולפו' as the copyist, while the authority evidence shows Abraham Firkowitsch (1786-1874) with role 'scribe'. There is a mismatch between the transcribed-by person and the name string qualifier. The P1684 inscription claim has P3831 qualifier Q372474 without a clear role label visible, and the inscription text is supported by MARC 500 but the object has role qualifier needs verification. The date 1642 in P571 is supported by description but MARC does not explicitly confirm this year; the 500 field mentions 'מעתיק העותק בכתב-יד ניו-יורק' regarding a copyist but doesn't clearly date the manuscript itself to 1642.

### 70. `990038384110205171` — partial

- **Label:** כתב סמיכה לרבנות לר' יהודה בריאל
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `GENRE_MAPPING`
- **Primary fix layer:** RDF creator / genre projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Use exact semantic mappings only. Do not equate illustrated/decorated with illuminated, an autograph response with an autograph manuscript, a topic with a genre, or a rabbinic ordination certificate with a legal license. Drop uncertain inferred genres rather than publishing them.

**AI evidence and issue explanation:**

> The Hebrew label matches the MARC 500$a and title field. The entity type 'manuscript' is correct for a semikhah document. However, P136='license' (Q79719) is incorrect: this is a rabbinic ordination certificate (semikhah), not a legal license. The MARC 655 'Licenses' appears to be a cataloging term but semikhah is specifically a religious ordination document, not a general license. P136='document' is acceptable but the 'license' claim should be removed. No P921 for 'Rabbis' is present despite the 650 subject heading; this is conservative and acceptable since it's generic. The inception date 1677 is supported by the Hebrew date in 500$a.

### 71. `990038692590205171` — partial

- **Label:** משנה תורה (ספר הפלאה, זרעים, עבודה, קרבנות)
- **Entity type:** `manuscript`
- **Verdict dimensions:** `name_ok=yes, role_ok=partial, type_ok=yes`
- **Validation codes:** `none`
- **Issue tags:** `SUBJECT_MAPPING`, `PROVENANCE_MAPPING`, `SCRIBE_RECONCILIATION`
- **Primary fix layer:** RDF creator / subject projection
- **Primary difficulty/effect:** Medium / Very high
- **First implementation action:** Do not promote every MARC 650 topic or “additional subject” to P921. Emit P921 only for primary/canonical subjects; keep secondary topics in evidence or a less-assertive representation, and require an explicit primary flag where available.

**AI evidence and issue explanation:**

> The manuscript label and description are accurate. The entity type (manuscript) is correct. However, P921 'Rabbinic responsa' is unsupported: the MARC 650 shows 'Responsa' as a subject, but this is a Mishneh Torah manuscript (a legal code), not a collection of responsa. The responsa mentioned in 500$a are additional texts bound at the front, not the main work. P127 'owned by' uses a local reference to a person (987007496371605171) with role 'former owner' per authority_evidence, but P127 typically indicates current ownership; the MARC 561 describes a 17th-century pawn transaction, not current institutional ownership. P11603 'transcribed by' with 'presumably' qualifier is acceptable for the scribe role. The P921 claims need review: 'Halakha' is appropriate, but 'Rabbinic responsa' mischaracterizes the primary content.

## Definition of done for the next verification run

- No `type_ok=no` entities; facsimile/print records must be classified before item construction.
- No doubled/escaped ISBD quote wrappers in work labels; legitimate Hebrew abbreviation marks remain intact.
- No P921 claims derived solely from a secondary/additional MARC subject.
- No P136 claim without an exact genre/form mapping and supporting source text.
- No P127 statement for a known former owner, seller, or censor without correct provenance semantics.
- No P1684 statement for a catalog/workflow note; every inscription has a text-bearing source field and role.
- Every item-valued P11603/P127/P9046 target passes name/role/chronology reconciliation and has a resolved label/QID before public export.
- Every current holding institution is sourced from the record; NLI is not used as a default when the record names another institution.
- Every date has a source field, correct Wikibase syntax, and correct precision.
- Every anthology folio range is non-overlapping and traceable to the 505 evidence.
- Re-run AI verification on the rebuilt projection and compare counts; do not judge the old cached items.

## Source and limitations

The source JSON contains AI verdicts and generated item evidence, not a separately curated root-cause label. The classifications in this document are therefore an evidence-based engineering analysis of the verdict reasoning. Counts are intentionally overlapping, and authority conflicts that require human catalog review are marked as reconciliation work rather than silently “fixed” by inference.

For the complete raw item evidence, see [the non-passing report](</Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/reports/run-48ba6c13-115c-4763-bff1-c08b9031b518-wikidata-approved-1-non-passing.md>).
