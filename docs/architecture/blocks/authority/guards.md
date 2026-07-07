# Authority Enrichment — How it works: guards

> Up: [Authority Enrichment](README.md)

**Guard/hardening stack** (`authority_hardening.py::apply_hardening_guards`).
Runs on every candidate before persistence, and again with sibling context in
`/authority/rebuild` and `finalize_authority_matches`. Flags and effects:

| Flag | Fires when | Effect |
|---|---|---|
| `placeholder_name` | Cataloguer placeholder (א״א, N.N., Anonymous, initials) | hard-reject: clear ALL ids + bio payload, conf→low |
| `non_person_heading` | Classical reference heading (אונקלוס, …) | hard-reject |
| `date_conflict` | Person dates impossible vs ms_year for the role (scribe dead 80+ yrs before MS) | hard-reject |
| `biographical_inconsistency` | death < birth etc. | hard-reject |
| `modern_person` | Born centuries after the MS | hard-reject |
| `mazal_entity_type_mismatch` | Mazal entity_type ≠ routed kind | hard-reject |
| `short_name_homonym` | ≤2-token/≤12-Hebrew-letter name on a rich cluster without tag-100 + (date overlap or single personality) corroboration | conf→low |
| `cluster_collapse` | Same VIAF cluster on two distinct names in one record | conf→low |
| `mazal_pair_collision` | Same VIAF cluster from two distinct (name, mazal_id) pairs | conf→low |
| `nli_strict_skip_viaf` | Mazal+VIAF resolved to pipeline-range QID ≥ Q138M | conf→medium |
| `corporate_viaf_drop`, `viaf_name_type_mismatch`, `viaf_person_on_non_person`, `viaf_untyped_no_anchor` | VIAF `nameType` conflicts with routed kind / unverified SRU without anchor | strip VIAF id + cluster ids, conf→low |
| `viaf_date_mismatch` | VIAF cluster years conflict with MARC $d | strip VIAF, conf→low |
| `cross_source_conflict` | Mazal tag-100 and VIAF disagree on dates | strip VIAF, keep Mazal, conf→low |
| `wikidata_orphan_label` | Label-search QID on work/corporate/topic without Mazal/VIAF anchor | strip QID, conf→low |
| `wikidata_label_on_place` | Any label-resolved QID on a place (KIMA/gazetteer only) | strip QID, conf→low |
| `wikidata_human_on_non_person` | Q5/Q15632617 on non-person row | strip QID, conf→low |
| `wikidata_crosscheck_fail` | VIAF cluster over-merged on Wikidata, or no Hebrew label within edit distance ≤2 (gated by `MHM_DISABLE_WIKIDATA_CROSSCHECK`) | strip QID + VIAF, conf→low/medium |
| `mazal_subject_not_personality` | Person author/contributor resolved via tag ≠ 100 (safety net when rematch found no tag-100 row) | conf→medium |
| `homonym_unresolved` | Mazal homonym abstain / ≥2 candidates without winner | conf→low, curator pick required |

`stage3_guards.authority_payload_blocked` (HARD_REJECT_GUARD_FLAGS) also nulls
`birth_year`/`death_year` after a hard reject. A candidate with no surviving
id AND no place coordinates is dropped entirely — unless it carries homonym
candidates (`authority.py:1581`).
