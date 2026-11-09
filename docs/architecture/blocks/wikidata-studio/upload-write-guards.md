# Wikidata Studio - Upload Write Guards

> Up: [Wikidata Studio](README.md) | Rules W-302, W-303

## Studio source recheck before each write

The bulk upload job (`backend/app/pipeline/wikidata_upload_job.py`) reads the
Studio cache fingerprint and summary when the job starts. It compares them with
the current cache before each live write.

- The job calls `_assert_source_current` before each live item write, in both
  upload passes.
- The sharded path calls `_assert_studio_source_unchanged` before each
  `upload_items` call.
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
