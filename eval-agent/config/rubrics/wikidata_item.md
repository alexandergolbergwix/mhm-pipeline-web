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
