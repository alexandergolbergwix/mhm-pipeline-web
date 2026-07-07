# Versioning, history & export — Skills

> Up: [Versioning, history & export](README.md)

### Skill: add a versioned entity type

1. Add `ENTITY_TYPE_<NAME>` to `backend/app/models/event.py` and include it
   in `ALL_ENTITY_TYPES`.
2. Route every mutation of the new read-model through `apply_event`
   (create on insert, patch on update) before the projection write.
3. Add a branch to `_apply_revert_to_read_model` in `history.py` with an
   explicit field whitelist (or document that there is no projection, like
   `wikibase_item`).
4. If it should appear in project exports: extend `ExportEntityType` in
   `backend/app/schemas/export.py`, the export router's section loop, and
   `EXPORT_ENTITY_TYPES` / `EXPORT_ENTITY_LABELS` in
   `frontend/src/api/export.ts`.
5. Add a `test_*_emits_*_event` case to
   `backend/tests/test_versioning_integration.py` and a
   `test_export_*_filter` case to `test_export_router.py` (Rules W-21/W-22).

### Skill: add an entity type to the export bundle

Extend `ExportEntityType` (schemas), the per-type query section in
`export.py`, the frontend closed set in `export.ts`, and the checkbox list in
`ExportProjectDialog.tsx`; then add the matching `test_export_*_filter` case.

### Skill: add a section import

Copy an existing block in `section_import.py`: define an `ImportRow` model,
pick a natural key, upsert via `apply_event` (`OP_CREATE`/`OP_PATCH`),
update the read-model, invalidate affected build caches, and extend
`backend/tests/test_section_import_router.py`.

### Skill: run the versioning backfill

One-shot, idempotent (skips entities that already have events):

```bash
heroku run -- bash -lc "cd backend && python -m scripts.backfill_versioning"
# locally: cd backend && .venv/bin/python -m scripts.backfill_versioning
```

Writes an `op=create` / `rev_no=1` event capturing the current state of every
MARC record, extraction approval, authority match, and Wikidata override.

### Skill: verify the scheduler jobs

Heroku Scheduler must carry: `scripts.run_snapshot` at 00:05/08:05/16:05 UTC,
`scripts.run_prune_events` at 03:05 UTC (and `run_prune_inference_cache` at
02:05 — staggered so retention jobs don't contend for the pool). Both jobs
are idempotent: the snapshotter upserts on `(entity_type, entity_id, bucket,
slot)`, so retries are safe.
