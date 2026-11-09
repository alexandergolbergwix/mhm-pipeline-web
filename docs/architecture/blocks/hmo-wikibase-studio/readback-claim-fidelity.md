# HMO Wikibase Studio - Read-Back Claim Fidelity

> Up: [HMO Wikibase Studio](README.md) | Related: [How it works](how-it-works.md)

## What the read-back keeps

`canonical_snapshot_from_wikibase` (`backend/app/pipeline/hmo_canonical.py`)
turns a live Wikibase item into a canonical claim. Each claim keeps its
evidence from the live statement:

- `rank`, when it is not `normal`. The allowed ranks are `normal`,
  `preferred`, and `deprecated`.
- `qualifiers`, as a flat list of snaks. Each snak keeps its property and value.
- `reference_blocks`, as one list of snaks for each Wikibase reference.

The read-back keeps the reference boundaries. It does not merge two references
into one block.

## Fail-closed rules

The conversion raises `ValueError` and returns no snapshot for the entity when:

- a rank is not one of the three allowed ranks;
- a qualifier or reference snak is not a value snak (`snaktype` must be `value`);
- a snak has no property or no `datavalue`;
- a snak property does not match its group key;
- a snak uses a datatype other than `string`, `external-id`, `url`, or `time`;
- a reference has no snaks, or the snak groups are not a mapping or a list.

Only `string`, `external-id`, `url`, and `time` snaks are supported. A snak
with another datatype blocks the entity. The read-back never drops such a snak
silently.

## Why

A read-back that drops the rank, a qualifier, or a reference boundary produces a
claim that differs from the live item. The conversion therefore fails closed.
It raises an error and produces no partial claim.

## Tests

- `backend/tests/unit/test_hmo_canonical_readback.py`
