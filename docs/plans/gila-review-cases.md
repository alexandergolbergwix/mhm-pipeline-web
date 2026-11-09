# Gila audit — manual-review case triage (2026-11-05)

> Up: [Wikidata Studio block](../architecture/blocks/wikidata-studio/gila-audit-completion.md) · Rule W-300 · reply letter: [gila-response-letter-he.md](gila-response-letter-he.md)

Source: `backend/state/gila-builder-audit-47e03437.json` (run
`47e03437-09f2-4d6b-aea7-89fe79d76198`, 897 records, 897 checked QIDs).
This triage groups the audit's 1,310 review cases so a curator can work
them group by group. The three classes are Gila's own distinctions:

1. `absent_in_source` — the MARC source has no such value. Do not invent one.
2. `present_not_transferred` — the source has the value but the projection
   lost it. (0 cases in this report; three builder regressions found in the
   code review were fixed under Rule W-300 and need a re-run to re-count.)
3. `identity_or_scope_uncertain` — the source names something but does not
   confirm its identity, role, work scope, or physical scope.

## absent_in_source — 147 cases (nothing to transfer)

| Point | Field | Reason | Count | Example QIDs |
|---|---|---|---|---|
| 7 | `300$a` | `no_prepared_physical_extent` | 104 | Q141746842, Q141746846, Q141746847 |
| 1 | `942$z` | `no_prepared_current_942_shelfmark` | 43 | Q141746893, Q141746964, Q141746967 |

Action: none. These document that the source lacks the field.

## identity_or_scope_uncertain — 1,163 cases in 17 groups

| Point | Field | Reason | Count | Example QIDs | Manual check |
|---|---|---|---|---|---|
| 5 | 700 | `named_scribe_identity_unresolved` | 412 | Q141746831, Q141746842, Q141746847 | Match the named scribe to an authority file (NLI/VIAF); the claim is kept as `P11603 somevalue` + `P1932` name until then |
| 5 | 100 | `author_identity_not_linked` | 391 | Q141746841, Q141746844, Q141746845 | Link the author to an identity file; P2093 stays until an exact verified match exists |
| 7 | 751/260 | `production_place_not_resolved` | 107 | Q141175735, Q141746832, Q141746844 | Resolve the place name through KIMA/a catalog; roles stay separated (writing vs related) |
| 6 | RELATED_WORKS | `unsupported_source` | 100 | Q141746848, Q141746857, Q141746858 | The related-work mention lacks a traceable source; confirm against the scan or drop |
| 2 | 907$n | `image_name_does_not_prove_form` | 97 | Q141746842, Q141746846, Q141746847 | Informational: physical form is unproven; the base manuscript class stands |
| 6 | 500 | `catalogue_prose` | 17 | Q141746887, Q141746967, Q141746968 | The 500 text is catalog prose, not a work; keep out unless the scan shows a real title |
| 6 | 100/245 | `bibliographic_fragment` | 10 | Q141746859, Q141746882, Q141746935 | The title/author pair looks bibliographic; confirm the work identity |
| 7 | 041/500 | `verify_language_scope_against_scan` | 8 | Q141747534, Q141748151, Q141748280 | Language vs script scope (e.g. Italian in Latin letters); check the scan |
| 6 | 500 | `bibliographic_fragment` | 7 | Q141746858, Q141746887, Q141746891 | Same as the 100/245 group, from 500 notes |
| 6 | RELATED_WORKS | `latin_title_requires_authority` | 6 | Q141747240, Q141747277, Q141747534 | Latin-script titles need authority/QID evidence before linking (Rule W-68) |
| 6 | 740 | `descriptive_note` | 2 | Q141746974, Q141747058 | The 740 is descriptive, not an analytical title |
| 3 | 500 | `descriptive_work_identity_is_source_scoped` | 1 | Q141747170 | The anchored opening sermon of Hizzuk Emunah stays a source-scoped work |
| 6 | 505 | `latin_title_requires_authority` | 1 | Q141175880 | Same as above, from 505 |
| 6 | 500 | `person_name_not_work` | 1 | Q141748075 | A person-name string was rejected as a work title (Rule W-264) |
| 6 | 500 | `folio_range_title` | 1 | Q141748413 | A folio-range string was rejected as a work title (Rule W-264) |
| 8 | 957/590 | `catalog_prose_requires_review` | 1 | Q141748419 | A colophon-field note is catalog prose; do not project as inscription |
| 5 | 700 | `verify_scribe_target_identity` | 1 | Q141748424 | A scribe target matched an identity but needs a source check (Troki case) |

## Live upload state (full-corpus receipt `24802798-dd95-4973-8ec8-32138f459e8f`)

1,996 items processed: 1,558 adopted, 407 blocked, 19 skipped, 1 created,
11 failed. The 10 owned-manuscript failures are Hebrew label/description
collisions (fix ready in the local uploader: keep the existing label, add
the proposed title as an alias; targeted retry pending). The remaining
failure `work:מדרש_איכה` needs manual payload review. The 407 blocked and
19 skipped items stay out of the retry; they need work-identity review and
uncertain-duplicate confirmation. Per-item outcomes live in the upload
ledger; the audit JSON's `upload_outcomes` (latest per local id over the
last ten succeeded jobs) shows 345 updated, 241 adopted, 4 created,
1,771 exists, 54 skipped, 12 blocked, 2 failed.

## Work repair proposal (source review)

`Q141175543` holds claims from two source records: `990001915930205171`
(קנטריני, יצחק חיים) and `990001123390205171` (אברהם הלוי, ממרוקו).
`Q141755239` is the Cantarini "דרושים" work tied to `990001915930205171`.
The source evidence separates two different works behind the generic title
"דרושים". Planned repair after curator confirmation: manuscript
`Q141175895` links to `Q141755239`; `Q141175543` keeps only the Abraham
claims from `990001123390205171`.

## Remaining checks before the audit can close

`verify_dates_places_languages_and_physical_scope`,
`confirm_work_identities_and_authority_links`,
`review_unqualified_or_truncated_transcriptions`,
`inspect_per_item_upload_outcomes_and_live_reference_blocks`,
`read_back_live_claims_before_marking_qids_corrected`.
The Rule W-300 fixes (colophon date survival, sole-work author fallback)
change some projections — re-run `--gila-audit` after the targeted retry and
keep `corrected_qids` empty until live read-back confirms corrections.
