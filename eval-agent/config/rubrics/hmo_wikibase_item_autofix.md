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

Prefer fixes that keep HMO items **Wikidata-projection-ready** under the
injected WikiProject Manuscripts skill (no manuscript P50 semantics,
correct scribe/holder roles, no project-QID-as-Wikidata confusion).
