# Versioning, history & export — How it works

> Up: [Versioning, history & export](README.md)

### Event flow

```
Curator mutation (PATCH/POST/DELETE on a curator-mutable field)
        │
        ▼
apply_event(db, project_id, entity_type, entity_id, op, new_state, …)
        │  · validates entity_type ∈ ALL_ENTITY_TYPES, op ∈ ALL_OPS
        │  · rev_no = latest + 1;  parent_event_id = latest event
        │  · op=create/snapshot → stores full `state`
        │  · op=patch  → stores jsonpatch.make_patch(prev, new)  (RFC 6902)
        │  · op=revert → stores BOTH target state and inverse patch
        │  · every 50th rev auto-appends an op=snapshot event  (core.py:37,215)
        ▼
caller updates the read-model row, then commits BOTH in one transaction
```

`apply_event` never commits (`core.py:150-155`) — the handler owns the
transaction so event + read-model land atomically.

Entity types (`event.py:58`): `marc_record`, `extraction_entity`,
`authority_match`, `wikidata_override`, `wikibase_item`, and
`hmo_item_override` (added after Rule W-21 was written — code wins).
`wikidata_override` state includes curator diff fields plus `approved` and
(Rule W-99) `accept_foreign_modify` / `accepted_foreign_qid`.

State reads fold the log: find the latest `state`-bearing event
(create/snapshot/revert), then replay later patches (`core.py:108-135`).
Auto-snapshots every 50 revs bound the replay cost.

### Hot / cold tiers and snapshot cadence

| Tier | Store | Cadence | Retention |
|---|---|---|---|
| Hot | `project_events` | every mutation; auto-snapshot each 50th rev | rolling 1000 events per entity; **create + ALL snapshot events are never pruned** (`prune_events.py:85`) — pruned daily 03:05 UTC |
| Cold | `entity_snapshots` | 3 slots/day (00:05 / 08:05 / 16:05 UTC), only for entities touched in the just-finished 8-h window; idempotent upsert on `(entity_type, entity_id, bucket, slot)` | forever — never pruned |

### Revert semantics

`revert_to_rev` (`core.py:284`) computes the target state at `target_rev`,
builds `jsonpatch.make_patch(current, target)`, and appends it as a **fresh
`op=revert` event** — old events are never mutated, so a revert is itself
revertible. The history router then pushes the target state onto the
read-model projection (`history.py:458` `_apply_revert_to_read_model`), with
per-type field whitelists; `wikibase_item` has no read-model — the log itself
is the record of what was written out. Everything commits together.

### History API (`/api/projects/{id}/history/*`)

- `GET /history` — newest-first per-entity timeline (viewer). Content-hash
  extraction-entity ids are resolved to approval UUIDs (`history.py:391`).
- `GET /history/diff?from=&to=` — RFC 6902 patch + before/after states.
- `GET /history/at?rev=` — time-travel read.
- `POST /history/revert` — editor-only, appends revert + projects read-model.
- `GET /history/snapshots` — cold-archive timeline, `?since=YYYY-MM-DD`.
- All non-timeline routes call `_assert_entity_in_project` (`history.py:426`)
  so knowing a `(type, id)` pair from another project leaks nothing; the
  timeline route filters events by `project_id` post-query.

Legacy project-wide endpoints (`/events`, `/snapshots`, `/restore/{event_id}`)
still back the original approvals-restore UI on `ProjectSnapshot`.

### Export

`GET /projects/{id}/export` (full bundle, `?entity_types=` repeat filter),
`/export/snapshots` (`entity_type`, `since` filters), `/export/history`
(whole project or one entity). All: viewer-gated, `StreamingResponse` in
64 KB chunks, `Content-Disposition: attachment`, actor PII batch-decrypted —
plaintext, never ciphertext bytes (`export.py:86-102`). Shapes live in
`backend/app/schemas/export.py`. The frontend downloads by navigating a
hidden anchor (`export.ts`), never `blob()`.

Section-level export (`section_export.py`): one GET per pipeline section —
extraction/authority (json|csv, `approved_only`), rdf (ttl|nt), wikibase and
wikidata-studio (json|csv|ttl). The Studio export reuses
`compute_build_fingerprint` against `WikidataStudioCache` so a fresh cache hit
exports without rebuilding.

### Import

Section-level import (`section_import.py`): one POST per section
(multipart, 50 MB cap, editor-only). Pipeline: parse (JSON/CSV) → per-row
Pydantic validation (invalid rows accumulate in `errors`, valid rows proceed)
→ upsert by natural key **through `apply_event`** (`OP_PATCH` if changed,
`OP_CREATE` if new) → read-model update. Extraction/authority imports
invalidate `WikidataStudioCache`; the RDF import replaces the built graph
on disk.
