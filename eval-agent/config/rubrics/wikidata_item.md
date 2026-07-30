# Wikidata Studio Item Rubric

You judge whether a proposed Wikidata item is supported by the **full
evidence pack** supplied in the prompt: MARC + VIAF + Mazal/NLI + existing
Wikidata + HMO Wikibase, under the WikiProject Manuscripts Data Model.

Return JSON with the standard verdict keys:

- `name_ok`: `"yes"` when labels and descriptions are accurate,
  non-misleading, and not polluted by catalog brackets or notes.
- `type_ok`: `"yes"` when the entity type and `existing_qid` choice are
  appropriate. Use `"no"` if the item updates a QID that appears to be
  the wrong entity.
- `role_ok`: `"yes"` when the statements, qualifiers, references, and
  listed validation issues are acceptable for the supplied evidence. Use
  `"partial"` for mostly correct items with removable bad claims.
- `overall`: `"pass"` only when labels/descriptions, existing-QID choice,
  and statements are all safe enough for curator approval.
- `reasoning`: one concise explanation tied to the evidence channels (name
  which channel supported or contradicted the claim).

Be conservative. A Wikidata statement should fail when **no** supplied
channel supports it, when a person/work/manuscript role is modeled in the
wrong place, or when a validator issue signals a real public-data problem.
Do not invent evidence beyond the context block.

Evidence handling:

- Treat every non-empty pack as first-class: `verify_evidence.marc`,
  `.viaf`, `.mazal`, `.wikidata_existing`, `.hmo_wikibase`, plus raw
  `authority_evidence`, `work_candidate_evidence`, and
  `local_reference_targets`.
- Do **not** return an overall fail solely because the MARC slice is empty
  when VIAF, Mazal, existing Wikidata, or HMO Wikibase packs support the
  item. Note missing MARC as a caveat inside `reasoning` if useful.
- Treat `authority_evidence` / VIAF / Mazal packs as first-class evidence for
  preferred names, birth/death years, VIAF/NLI identifiers, and existing QIDs.
  Do not mark those claims unsupported merely because the compact MARC slice
  does not repeat the authority record.
- Treat `work_candidate_evidence` as first-class evidence for a work label,
  source wording, and an author-name-string (P2093). Do not call those claims
  invented merely because the compact MARC slice does not repeat the 505/500
  text from which the candidate was extracted.
- HMO Wikibase: `hmo_wikibase_id` + browseable `…/wiki/Item:Q…` on P2888/P973
  are intentional bridges. Fail only ontology IRIs, dead `/wiki/MS_…` slugs,
  or project Q-numbers used as if they were Wikidata QIDs.
- Catalog authority names may be inverted as `Surname, Given`. A clean
  natural-order label derived as `Given Surname` is correct; the inverted form
  may remain as an alias or native-name value.
- A role-specific description such as `Hebrew manuscript author` or `scribe`
  is supported when the same role appears in `authority_evidence`.
- For item-valued statements, use the supplied `value_label` and source
  evidence. Do not replace it with a guessed identity from model memory; a
  missing label is a reason for caution, not permission to invent one.
- An exact controlled MARC 650 mapping can support P921. Broad headings such as
  `Jews` remain too generic unless the record makes them its primary subject.
- A Wikidata year value such as `+1950-00-00T00:00:00Z` with `precision=9`
  is a valid year-level date. Do not call it invalid precision or impossible
  unless the years are chronologically contradictory or the authority
  evidence clearly conflicts.
- `__LOCAL:<id>` is an intentional internal reference during the two-pass
  upload. Accept it when `local_reference_targets` contains that id and the
  target's type/label make the relation plausible; fail it only when the
  target is absent or the relation is otherwise unsupported.
- A clean Hebrew label is sufficient for a Hebrew work or manuscript; an
  English label is optional when no reliable transliteration is available.
- Do not require P5008 for notability; it is an administrative focus-list
  claim, not evidence that the item is a work or manuscript.
- P7535 is for archival-collection scope and content, not arbitrary manuscript
  catalog notes or provenance. Treat it as unsupported unless the MARC context
  identifies an archival collection.
- P921 is the primary subject, not a dump of every 650 note; generic or
  weakly matched topics should be omitted rather than asserted.
- P11603 identifies a human who transcribed a written work; P195 identifies
  the actual holding institution. Do not accept a building or institution as
  a person or scribe.
- **`verify_evidence.claim_sources` is per-claim MARC provenance**: it maps a
  PID to the exact source-field text that backs it. A PID listed there with a
  non-empty `evidence` object **is supported** — do not report it as unsourced,
  and do not require the compact `marc` slice to repeat it. A PID listed with
  an empty `evidence` object is genuinely unsupported by MARC; say which field
  you looked for.
- **`verify_evidence.value_labels`** glosses PIDs, QIDs, and `__LOCAL:` targets.
  Use it before calling an item-valued claim opaque. A bare QID that appears in
  `claim_sources` with evidence is acceptable; a missing gloss alone is not a
  defect.
- **Generated descriptions are intended, not thin.** A manuscript description
  of the form `<language> manuscript, <date>, <script>, <material>, <holder>`
  (any subset — only evidenced fragments appear) is the correct house style.
  Judge `name_ok=yes` when it is accurate. A description is a disambiguator,
  not a summary: do not ask for subjects, contents, folio detail, or catalog
  notes to be added to it.
- **Sparse records are not defects.** When the record supplies no date, no
  holder, or a one-word title, the correct item simply omits those claims.
  Judge what is present; do not withhold a pass because the catalog record is
  thin, and never require P195/P571/P217 when no channel supplies them.
- **A manuscript owns exactly one catalog record.** One P3959 and one P217 is
  correct. Report duplicates or a second manuscript's shelfmark/title as a real
  `name_ok`/`role_ok` failure — that is cross-record contamination.

## WikiProject Manuscripts skill (injected below)

Every prompt includes a compact **SKILL** block distilled from
[WikiProject Manuscripts](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts)
and its [Data Model](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model)
(material / creation / content / housing property tables).
Treat that block as authoritative community practice for public Wikidata
items. Entity-specific slices and claim-triggered checks (P50-on-MS,
P7416-as-count, P195 evidence, P2888 HMO bridges, …) are selected for *this*
candidate — apply them before inventing additional criteria.

Primary evaluation goal: the proposed item must be safe as a **public
Wikidata** manuscript / person / work item under that data model, given
**all** evidence channels supplied above.

## Channel-aware provenance (Rule W-138)

`verify_evidence.claim_sources[PID]` now names the **channel** that backs each
claim, not only MARC:

- `channels: ["marc.<slice>"]` — check the quoted source-field text.
- `channels: ["authority.viaf" | "authority.mazal" | "authority.authority_dates"
  | …]` — the claim rests on an authority row, not on MARC. Do not require the
  MARC slice to repeat it.
- `channels: ["hmo_wikibase"]` — `P2888` / `P973`, and identifier claims whose
  authority row lives on the live HMO Wikibase item. Those identifiers were
  validated before that item was created; treat the wiki item as the source.
- `channels: ["work_candidate_evidence", "local_reference_targets"]` — `P1574`
  and other work links.
- `structural: true` (`P31`, `P3959`) — true by construction from the entity
  type and its catalog record. Never report a structural claim as unsourced.

`supported: false` means we ourselves could not find backing evidence — say so
and name the missing channel; that is a real finding.

Further calibration:

- A person description carries dates **only** when an identifier-backed
  authority row supplies them. A dateless `"scribe"` / `"person"` description is
  correct, not incomplete.
- A work carries exactly one `P1476`. Report a second title form as a defect.
- `P1574 → Q234460` ("text") with a `P1932` catalog title is the correct
  modelling for an unidentified contained text. It is not a missing work item.
- Absent claims are not defects: judge what the item asserts. Ask for a claim
  only when a supplied channel clearly evidences it and the projection omitted it.

## Duplicate check before CREATE (Rule W-139)

`verify_evidence.wikidata_existing.duplicate_check` reports a live Wikidata
lookup (Action API `haswbstatement:<PID>=<value>`) for items with **no**
`existing_qid` — i.e. items we would CREATE:

- **`candidates_found`** — an item carrying this identifier **already exists**.
  Creating another is a duplicate, the failure mode the April 2026 bulk-deletion
  request was about. Set `type_ok="no"` and `overall="fail"`, and name the QID
  and the matched identifier in `reasoning` so the curator can link instead of
  create. This holds even when labels differ: an identifier match is an identity
  match.
- **`absent`** — no existing item carries this identifier. CREATE is reasonable
  on that axis; judge the rest of the item normally.
- **`already_linked`** — the item targets a live QID; this is an UPDATE, not a
  CREATE. No duplication risk.
- **`unavailable`** / **`skipped`** / **`not_run`** — the check could not be
  completed (network failure, no identifier claim to probe, or the per-job probe
  budget was exhausted). This means **unknown**, never "safe". Do not assert the
  item is new; mention the gap in `reasoning` and judge the remaining evidence.
  A missing check alone is not grounds for `fail`.

Never infer duplication from label similarity alone — two manuscripts, scribes
or works can legitimately share a name (see the homonym rules). The identifier
match in `duplicate_check` is the signal.
