# Gila audit: builder completion plan

## Goal and accepted model

Apply the conversion rules to all 897 records in run
`47e03437-09f2-4d6b-aea7-89fe79d76198`.
Preserve each existing manuscript QID.
The user chose separate physical manuscript and written-work entities on
2026-11-05. Keep `Q47461344` on work items. Use physical classes on manuscripts.
Record this explicit model decision in the response to Gila.

## Verified causes and fixes

| Requirement | Cause and code path | Required change and acceptance check |
|---|---|---|
| Scribes without authority IDs | `person_projection._get_or_create_person` returns an empty stub; `person_linking._add_person_claims` then drops the source-backed role. | Preserve the explicit MARC role without a fabricated person item. Use an unknown-value role claim with the exact source name, or retain an explicit unresolved connection. Keep uncertain names uncertain. Test two distinct scribes and the final canonical output. |
| Known authors | `_find_work_author_match` requires an approved authority row. The production Cantarini row is unapproved, although its NLI ID matches `Q6076130`. `_ensure_work_author` does not consult audited identities without a match. | Keep approval and identity fidelity separate. Permit only verified, exact audited identities or approved exact matches. Test Cantarini, a rejected identity, a homonym, and a `$c` name extension. Never approve all authority rows to raise coverage. |
| Secondary-work authors | `content_projection._content_author` assigns the main author when `contents` contains one entry, including a secondary 500 entry. The NER path also falls back to the record author. | Restrict fallback to the main 245 work or an unambiguous 505 work. A 500/740 supplement needs its own responsibility evidence. The Etz Chayim selection must not inherit Bagi. |
| Opening sermon | `_extract_work_mentions` recognizes folio prefixes and narrow named-work introductions, but misses `בראשו שילב המעתיק דרשה על פרשת ויגש`. | Add a source-anchored content pattern. Preserve its descriptive title and source text. Do not infer a physical unit or a folio number. |
| Subject and genre scope | `_add_canonical_subjects` places resolved 650 topics on the manuscript without a part scope. A verified QID establishes identity, not scope. | Attach explicitly section-scoped data to the section work. Retain unresolved scope as review evidence. Do not classify the main Hizzuk Emunah work as a sermon because its manuscript contains one. |
| Codex evidence | `_determine_instance_type` treats 907 image counts and filenames as proof of codex form. | Remove image-only physical-form inference. Preserve the base manuscript class when form evidence is absent. Test scrolls, fragments, single sheets, and explicit multi-leaf extents. |
| Distinct role claims | `dedupe_statements` keys on property and value, without role or name qualifiers. `_merge_pair` uses the same shallow identity. | Preserve distinct roles, folio ranges, and named unknown values. Deduplicate only statements with the same qualified meaning. |
| References | `uploader._build_claim` combines flat native references into one block. On equal claims, `_build_wbi_item` replaces every existing reference when more than one exists. | Deduplicate exact references while preserving distinct sources. Test an owned update with duplicate references and a second valid source. Ensure the exists check does not skip the repair. |
| Colophon loss or missing role | `_merge_transcription_statements` combines inscriptions; `_merge_pair` prefers qualified text only for exact normalized matches. | Preserve colophon and title-page roles separately. Add a source comparison that distinguishes absent text from unqualified text. Preserve the full Hebrew date and `שמואל(?)`. |
| Record status | Ingest retains `record_status_notes`, but public projection correctly excludes a catalog note from inscriptions. | Carry the note through source evidence and the audit export. Do not use it to mark every manuscript claim uncertain. |
| Existing mixed work | The old `Q141175543` remains outside the corrected manuscript link. A new build cannot remove its old claims by omission. | Produce a separate source-specific repair proposal with current revision, claim IDs, source CNs, and proposed removals. Reuse the normal owned-item write path after review. Never merge works by title alone. |
| Corpus completion | The current nine-point documentation replaces point 9 with performance work. Job success also hides per-item failures. | Correct the checklist. Produce one row per manuscript/QID and separate the three review classes below. Include work repairs and both failed bulk items. |

## Review classes

1. `absent_in_source`: the source has no value. Do not invent one.
2. `present_not_transferred`: the source has a value but the projection loses it.
3. `identity_or_scope_uncertain`: the source names something but does not confirm
   its identity, role, work scope, or physical scope.

## Implementation sequence

- [ ] Add regression tests through the builder and canonical merge interfaces.
- [ ] Correct source-scoped author fallback and image-only codex inference.
- [ ] Preserve named unresolved scribes without new identifierless people.
- [ ] Preserve qualified statement identity and exact reference identity.
- [ ] Add the anchored sermon and its section-level genre evidence.
- [ ] Complete audited author reuse and retain unresolved authority cases.
- [ ] Complete colophon and catalog-status source evidence.
- [ ] Add the corpus audit export and the old-work repair proposal.
- [ ] Bump both legacy and canonical build schemas where behavior changes.
- [ ] Rebuild locally from read-only production inputs and compare all records.
- [ ] Update block rules, tests, and the original nine-point documentation.

## Release evidence

The first code review found seven further defects.
Use [the review follow-up](gila-builder-review-followup.md) for their reproductions
and the current verification state before the next code pass.

The final report must list checked QIDs, changed QIDs, changed fields, and
unresolved cases by review class. It must include failed item outcomes.
Passing tests do not establish that production received the changes.
The rebuild, targeted repairs, and upload require their own receipts and live
read-back before the audit can close.
