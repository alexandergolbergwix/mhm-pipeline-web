# Versioning, history & export — Key files

> Up: [Versioning, history & export](README.md)

| File | Purpose |
|---|---|
| `backend/app/versioning/core.py` | `apply_event`, `current_state`, `state_at_rev`, `diff_revs`, `revert_to_rev`, `event_timeline`; auto-snapshot every 50 revs |
| `backend/app/models/event.py` | `ProjectEvent` row (entity_type/entity_id/rev_no/op/patch/state), closed sets `ALL_ENTITY_TYPES` / `ALL_OPS`; legacy `ProjectSnapshot` |
| `backend/app/models/entity_snapshot.py` | `EntitySnapshot` cold archive — full state per `(entity_type, entity_id, bucket, slot)` |
| `backend/app/routers/history.py` | `/projects/{id}/history` timeline / `diff` / `at` / `revert` / `snapshots` + legacy project-event endpoints; project-scope guards; read-model projection on revert |
| `backend/app/routers/export.py` | `GET /projects/{id}/export` (+ `/snapshots`, `/history`) — streaming JSON attachments |
| `backend/app/routers/section_export.py` | Per-section `GET /runs/{id}/{section}/export` (json/csv/ttl/nt) |
| `backend/app/routers/section_import.py` | Per-section `POST /runs/{id}/{section}/import` — parse → validate → upsert via `apply_event` |
| `backend/app/schemas/export.py` | Pydantic shapes for the project-export bundle (`ExportEntityType` closed set) |
| `backend/app/jobs/snapshot_entities.py` | 3x/day slot snapshotter (`snapshot_touched_entities`) |
| `backend/app/jobs/prune_events.py` | Daily 1000-event rolling-window prune (`HARD_CAP = 1000`) |
| `backend/scripts/run_snapshot.py` | Heroku Scheduler entrypoint — 00:05 / 08:05 / 16:05 UTC |
| `backend/scripts/run_prune_events.py` | Heroku Scheduler entrypoint — 03:05 UTC |
| `backend/scripts/backfill_versioning.py` | One-shot, idempotent: `op=create` event for every pre-existing read-model row |
| `frontend/src/api/export.ts` | Typed client — hidden-anchor download (no JS-heap buffering) |
| `frontend/src/components/export/ExportButton.tsx` / `ExportProjectDialog.tsx` | Project-header "Export…" entry point + entity-type picker |
