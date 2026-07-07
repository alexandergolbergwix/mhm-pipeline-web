# Versioning, history & export — Rules

> Up: [Versioning, history & export](README.md)

1. **R1 — Every curator mutation MUST route through `apply_event` BEFORE the
   read-model update, in the same transaction.** *Why:* the event log is the
   source of truth; a read-model write without an event is unrecoverable,
   invisible history (Rule W-21).
2. **R2 — NEVER mutate or delete an existing event to undo something; revert
   is always a new `op=revert` event.** *Why:* the log is append-only — that
   is what makes reverts auditable and themselves revertible (`core.py:284`).
3. **R3 — `apply_event` MUST NOT commit; the caller owns the transaction.**
   *Why:* event + read-model must land atomically or not at all.
4. **R4 — The prune job MUST NEVER delete `create` or `snapshot` events.**
   *Why:* they are the replay anchors — without them `state_at_rev` on old
   revisions breaks (`prune_events.py:5-11`; note: code preserves *all*
   snapshots, stronger than the "latest" wording in Rule W-21).
5. **R5 — `entity_snapshots` (cold tier) is NEVER pruned.** *Why:* it is the
   only history that survives the 1000-event window; `/export/snapshots`
   depends on it.
6. **R6 — New entity types MUST be added to the closed set
   `ALL_ENTITY_TYPES` in `event.py`;** `apply_event` raises on unknown types.
   *Why:* a free-form type string would fragment timelines and dodge the
   prune/snapshot/export machinery.
7. **R7 — History/export routes MUST gate on project membership AND
   re-assert the entity belongs to the project.** *Why:* the versioning core
   is project-agnostic; without `_assert_entity_in_project` a member of
   project A could enumerate project B's events (`history.py:426-437`).
8. **R8 — Exports MUST decrypt PII before serialisation and NEVER emit
   ciphertext bytes;** big exports MUST stream with
   `Content-Disposition: attachment`. *Why:* an export is the curator's
   usable copy of their data, and a 100 MB bundle must not pin RAM (Rule W-22).
9. **R9 — Imports MUST upsert through `apply_event` (never raw INSERT/UPDATE)
   and invalidate dependent build caches.** *Why:* imported rows are curator
   mutations like any other; skipping the log breaks R1, and a stale
   `WikidataStudioCache` would serve pre-import items.
10. **R10 — A revert MUST also project the target state onto the read-model
    table in the same commit.** *Why:* the UI reads the projection; an
    event-only revert would show nothing changed (`history.py:325-339`).
