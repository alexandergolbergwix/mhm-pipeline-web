# Versioning, history & export - TTL Statement Export

> Up: [Versioning, history & export](README.md) | Rule R12

## Scope

`items_to_rdf_graph` (`backend/app/export/formatters.py`) builds the graph for
two TTL exports in `backend/app/routers/section_export.py`:

- `GET /runs/{run_id}/wikibase/export?format=ttl`
- `GET /runs/{run_id}/wikidata-studio/export?format=ttl`

The RDF section export (`rdf/export`) uses a different path.

## Output

For each claim, the graph contains:

- a statement node with `p:` and `ps:` values and `wikibase:rank`
  (`NormalRank`, `PreferredRank`, or `DeprecatedRank`);
- one `pq:` triple for each qualifier;
- one reference node for each reference block, linked with
  `prov:wasDerivedFrom`, with its snaks typed by `pr:` predicates.

`reference_blocks` take precedence over flat `references` when both exist and
the blocks are not empty.

A direct `wdt:` triple appears only for the best rank of a property. The best rank
is `preferred` when one exists, otherwise `normal`. A property with only deprecated
statements has no direct value.

## Fail-closed (422)

The graph builds before the response streams. `_rdf_graph_or_422` turns a
`ValueError` into HTTP 422, and the message becomes `detail`. No partial TTL
file streams. The export rejects:

- a property ID that is not `P<digits>` after an optional `wdt:` prefix;
- a rank that is not `normal`, `preferred`, or `deprecated`;
- a statement that is not an object;
- a qualifier or reference snak that is not an object, or that has no value.

A missing rank counts as `normal`.

## Tests

- `backend/tests/unit/test_export_formatters.py`
- `backend/tests/test_section_export_router.py`
