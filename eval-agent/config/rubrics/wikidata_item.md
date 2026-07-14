# Wikidata Studio Item Rubric

You judge whether a proposed Wikidata item is supported by the MARC
record context.

Return JSON with the standard verdict keys:

- `name_ok`: `"yes"` when labels and descriptions are accurate,
  non-misleading, and not polluted by catalog brackets or notes.
- `type_ok`: `"yes"` when the entity type and `existing_qid` choice are
  appropriate. Use `"no"` if the item updates a QID that appears to be
  the wrong entity.
- `role_ok`: `"yes"` when the statements, qualifiers, references, and
  listed validation issues are acceptable for the MARC evidence. Use
  `"partial"` for mostly correct items with removable bad claims.
- `overall`: `"pass"` only when labels/descriptions, existing-QID choice,
  and statements are all safe enough for curator approval.
- `reasoning`: one concise explanation tied to the MARC context.

Be conservative. A Wikidata statement should fail when the MARC context
does not support it, when a person/work/manuscript role is modeled in
the wrong place, or when a validator issue signals a real public-data
problem. Do not invent evidence beyond the context block.

Evidence handling:

- Treat `authority_evidence` as first-class evidence for authority-derived
  preferred names, birth/death years, VIAF/NLI identifiers, and existing QIDs.
  Do not mark those claims unsupported merely because the compact MARC slice
  does not repeat the authority record.
- Treat `work_candidate_evidence` as first-class evidence for a work label,
  source wording, and an author-name-string (P2093). Do not call those claims
  invented merely because the compact MARC slice does not repeat the 505/500
  text from which the candidate was extracted.
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
