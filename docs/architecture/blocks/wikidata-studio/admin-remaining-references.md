# Remaining reference scope

The **Remaining references** tab uses `GET /api/admin/wikidata-reference-repairs?scope=remaining`.
The start request includes `scope: "remaining"` and its own exact scope digest.
The original queue retains its checkpoint and scope. The two scopes share the run lock.

`gila_remaining_reference_scope.json` records the 27 items and 472 repeated references from the 20 November audit.
The eligible repair scope contains nine items with 13 redundant blocks.
The other 18 items have different creators. The queue lists them for review without an edit action.
The latest Gila audit can update the observed reference count next to each dated item.

`reviewed_reference_scope.py` checks each reviewed claim digest and citation group.
This scope uses the existing citations as exact reference evidence.
The worker preserves each distinct citation group and every non-reference field.
A changed claim requires a new review. The source-control check for the original queue remains in place.
The existing Publication worker rechecks the current role, source proof, creator UID, and revision before a write.
It retains its normal lag, retry, and unknown-outcome controls.
`test_reviewed_reference_scope.py` checks evidence changes and the creator boundary.
