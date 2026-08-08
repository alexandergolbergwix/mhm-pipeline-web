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
- `overall`: `"full"` only when labels/descriptions, existing-QID choice,
  and statements are all safe enough for curator approval. Legacy `"pass"`
  is accepted as a synonym of `"full"` when reading older caches.
- `reasoning`: one concise explanation tied to the evidence channels (name
  which channel supported or contradicted the claim).

Be conservative. A Wikidata statement should fail when **no** supplied
channel supports it, when a person/work/manuscript role is modeled in the
wrong place, or when a validator issue signals a real public-data problem.
Do not invent evidence beyond the context block.

## Universal Mode-β hygiene (Rule W-171 / W-172)

These rules apply to **every** manuscript / person / work — never special-case
a control number or shelfmark:

1. **Absent claims are never defects.** Do not set `role_ok`/`overall` to
   `partial` solely because P11603 / P195 / P1574 / P217 “should exist”. Judge
   what is present. Sparse catalogues omit claims.
2. **WPM manuscript labels** of the form `{holder}, {shelfmark}` or a Hebrew
   designation built from holder+shelfmark are **`name_ok=yes`** when they match
   MARC 710/090 evidence. Do not demand a literary work title as the MS label.
3. **P1476 may equal a contained work title** when P1574 links that work (or a
   local work target). Do not demand shelfmark substitution for the title claim.
4. **Trust `value_label` / `verify_evidence.value_labels`.** Never invent an
   institution name from QID shape or model memory (especially never invent NLI
   for an unrelated QID).
5. **Quantities:** when a statement carries `unit` (`mm`, leaf QID `Q107256474`,
   page QID `Q1069725`), that field **is** the unit. Do **not** demand a
   separate unit *qualifier*, and do **not** set `role_ok`/`overall` to
   `partial` for “missing unit” when `unit` is already present. Compare after
   unit conversion; do not treat `95` with unit `mm` as contradicting a MARC
   centimetre figure, and do not treat a leaf count as “pages” when the unit
   is leaf.
6. **Facsimile:** when P31 is book (`Q571`) and `semantic_type` / notes indicate
   a printed facsimile, `type_ok=yes` even if `entity_type` is still
   `manuscript` (stable upload type). Work descriptions for facsimile source
   records must not claim the work is “preserved in a Hebrew manuscript”.
7. **Existing QID type:** do **not** invent that `existing_qid` is a
   disambiguation page when `verify_evidence.wikidata_existing` does not show
   `P31=Q4167410`. A human (`Q5`) target with matching labels is not a type
   error solely because the name is shared by several historical figures.
8. **Holding QIDs:** never invent that `Q1028334` (Cambridge University
   Library) or another audited holder QID is the National Library of Israel.
   Prefer `value_label`, then the English manuscript label’s holder fragment.
9. **Keep hard fails** for true identity contamination (conflicting P8189),
   wrong public QID links, and unsupported bad claims that are present.

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
- `channels: ["authority.person_link"]` — a role-derived person edge
  (`P3342` significant person, `P1891` signatory, `P127` owner, …). The evidence
  is the MARC relator that produced the edge, quoted in `evidence.person_link`,
  not a MARC field that repeats the value.
- `structural: true` (`P31`, `P3959`, `P5008`) — true by construction from the
  entity type, its catalog record, or our own WikiProject membership. Never
  report a structural claim as unsourced.

**Read `support_status`, not `supported`.** The boolean conflated two very
different facts; the status names them:

- `supported` — evidence is quoted. Check the claim against it.
- `structural` — needs no separate source (see above).
- `channel_empty` — the channel is correct and **this record's field is empty**.
  Catalogue sparsity, not a defect: do not report it, and do not let it move any
  axis. A thin MARC record is still a valid one.
- `no_channel_mapped` — **our build defect.** No channel table names this PID, so
  nobody can trace the claim. Report it as a projection bug that needs a channel
  row, naming the PID — not as a claim the item failed to evidence. Until
  August 2026 `P3342` and `P1891` shipped this way on 21 rows, and it read as if
  the data were unsupported when the mapping was simply missing.

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
- **`candidates_found` with `adoption.adopted: true`** — the pipeline has already
  adopted that QID, so `existing_qid` is set and this is an UPDATE. The duplicate
  axis is SETTLED: do not fail the item for duplication, and do not ask the curator
  to link it. Judge the claims themselves. (Adoption only happens on an identifier
  or verified-composite match with exactly one candidate; it does not authorise the
  write, which still passes an ownership check.)
- **`unavailable`** / **`skipped`** / **`not_run`** — the check could not be
  completed (network failure, no identifier claim to probe, or the per-job probe
  budget was exhausted; `reason: "capped"` means the key was computed but the
  budget deferred it). This means **unknown**, never "safe". Do not assert the
  item is new; mention the gap in `reasoning` and judge the remaining evidence.
  A missing check alone is not grounds for `fail`.

**Name the status token you saw.** Write `duplicate_check.status=absent` (or
whichever it is) rather than prose like "the duplicate check was empty" or "not
available". Until August 2026 the fixture stripped this channel out entirely, so
every verdict rendered `{}` and hedged about it — 28 of 29 `partial` verdicts on
run `48ba6c13` spent their reasoning on a probe that had in fact answered
`absent`. Quoting the token makes that class of bug visible in one grep instead
of invisible for three months.

**An inconclusive check may not move any axis.** It is not evidence against
`name_ok`, `type_ok` or `role_ok`, and it must not by itself downgrade `overall`
from `full` to `partial`. Mention it and move on.

Never infer duplication from label similarity alone — two manuscripts, scribes
or works can legitimately share a name (see the homonym rules). The identifier
match in `duplicate_check` is the signal.

## LLM extraction proposals are candidates, not evidence (Rule W-140)

`verify_evidence.llm_proposals` holds structured values a tier-1 model mined
from the MARC provenance prose (500 / 541 / 561 / 563 / 583) — the narrative
Hebrew that names owners, places and writing supports no parser reaches. Each
proposal quotes the verbatim `span` it came from and is dropped before it
reaches you when that span is not literally present in the record.

They are **review candidates for the curator**, and nothing in this block has
been projected onto the item:

- **Never treat a proposal as evidence for a claim.** Generation is not an
  evidence channel (Rules W-72 / W-138). If a statement is supported only by a
  proposal, it is unsupported.
- **Never fault an item for not carrying a proposed value.** Absent claims are
  not defects, and a proposal is explicitly not an instruction to project.
- Do not fault the item when `status` is `unavailable`, `no_source`,
  `disabled` or `not_run` — those describe the extractor, not the item.
- You may mention in `reasoning` that a proposal looks worth a curator's
  attention, but it must not change `name_ok`, `type_ok`, `role_ok` or
  `overall`.
