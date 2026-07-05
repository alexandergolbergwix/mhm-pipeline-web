# HMO Wikibase Item Autofix Rubric

Compare the built HMO Wikibase projection against the live Wikibase
entity snapshot. Propose only high-confidence `suggested_fixes` the
curator can apply in one click.

Each fix object must include:
- `target` (e.g. `label.en`, `description.he`, `statement.add`, `statement.remove`)
- `value`
- `confidence`: `"high"` only
- `reasoning`

Return the standard verdict keys plus `suggested_fixes` when fixes apply.
