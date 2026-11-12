# Wikidata Studio - Upload Write Guards

> Up: [Wikidata Studio](README.md) | Rules W-302, W-303

## Studio source recheck once per write batch

The bulk upload job (`backend/app/pipeline/wikidata_upload_job.py`) reads the
Studio cache fingerprint and summary when the job starts. It compares them with
the current cache before the first live write of each batch.

- `_BatchFreshnessGate` runs the check when the write count is a multiple of
  `WRITE_FRESHNESS_BATCH_SIZE` (50). One check covers up to 50 writes.
- A failed check blocks the write. The next write checks again, because the
  count does not advance after a failure.
- The job uses one gate in each upload pass, and the sharded path uses its own gate.
- A source change during a batch is detected at the next batch boundary, not
  before each write. A full check takes about 74 seconds on production data.
- A dry run skips the check, because a dry run writes nothing.

If the cache row is missing, or the fingerprint or summary changed, the job raises
`StudioSourceChangedError`. The job makes no further writes. The job message tells
the curator to rebuild the Studio source and upload again. The check fails closed.
A stale plan never writes.

## Owned-item rule

Only items created by the current account may be modified. An existing item
that the account did not create is not a write target.

- `WikidataUploader._assert_modifiable` (`backend/converter/wikidata/uploader.py`)
  raises `UnauthorisedModificationError` for any QID that is not owned.
- `_is_our_item` treats the authenticated user as the owner only when the user
  is the first-revision author. Three independent checks must agree. See the
  method docstring for the channels.
- The one exception is a live curator accept of the exact QID
  (`accepted_foreign_qid`, Rule W-302). The accept covers only that QID.
  The test wiki never uses this exception.
- `WikidataPublicationGateway._assert_publication_modifiable`
  (`backend/app/publication/wikidata_gateway.py`) applies the same rule before a
  Publication write. The mutation must have creator ownership or
  `allow_foreign_update`.
- Identity evidence never grants ownership. P3959 is identity evidence, not an
  ownership channel (Rule W-302).

## Tests

- `backend/tests/unit/test_wikidata_upload_source_recheck.py`
- `backend/tests/unit/test_wikidata_upload_guards.py`
- `backend/tests/unit/test_wikidata_uploader_ownership_policy.py`
- `backend/tests/unit/test_wikidata_publication_gateway.py`

See [source repair safeguards](source-repair-safeguards.md) for W-311–W-314, the focused tests, and the source-only build procedure.
