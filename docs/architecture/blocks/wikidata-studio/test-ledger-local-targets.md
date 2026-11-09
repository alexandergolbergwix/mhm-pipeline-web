# Wikidata Studio - Test-Ledger Local Targets

> Up: [Wikidata Studio](README.md) | Related: [Reference blocks](reference-blocks.md)

## Markers in the publication document

A Publication document can reference a local item that already exists on the
test wiki. The runtime (`backend/app/publication/runtime.py`) writes one marker
per referenced local ID into `publication_local_targets`.

Each marker holds `site: "test.wikidata.org"` and the sealed test-ledger `qid`.
The runtime writes only the local IDs that the document references. For a test
ledger item, the runtime also sets `existing_qid` to the sealed QID.

## Registration before build

`WikidataPublicationGateway._register_test_target`
(`backend/app/publication/wikidata_gateway.py`) runs before any claim is built.
The write path and the read-back path both call it. The order is:

1. `_register_test_update_target` binds the target QID (update only).
2. `_register_test_local_targets` reads `publication_local_targets`.
3. A marker without the test site or without a string QID raises
   `WikidataPermanentError`. No write happens.
4. `WikidataUploader.register_test_local_target` binds the local ID to the QID.
   A different earlier proof for the same local ID raises
   `Conflicting test-target proof`.
5. Only then does the gateway remap claims for the test wiki.

`register_test_local_target` refuses any uploader that is not in test mode.
Live writes never use this proof.

## Limits

- A bare QID stays ambiguous. An unverified local target blocks the item
  (see [Reference blocks](reference-blocks.md)).
- The marker proves only the sealed test-ledger QID. It does not prove a live
  Wikidata entity.

## Tests

- `backend/tests/unit/test_wikidata_publication_gateway.py`
- `backend/tests/test_publication_source_projection.py`
