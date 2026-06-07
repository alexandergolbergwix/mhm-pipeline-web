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
