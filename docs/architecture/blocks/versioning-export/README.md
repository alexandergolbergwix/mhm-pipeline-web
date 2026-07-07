# Versioning, history & export

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

Every curator decision (approve, edit, override, revert, import) is recorded
in an append-only per-entity event log (`project_events`). The read-model
tables (`extraction_approvals`, `authority_matches`, `run_records`,
`wikidata_item_overrides`) are O(1) current-state caches; the event log is the
authoritative source of truth. On top of it sit: per-entity timelines, RFC
6902 diffs, time-travel reads, reverts, a two-tier snapshot store
(hot auto-snapshots inside the log + a cold `entity_snapshots` archive), and
JSON export/import surfaces at project level and per pipeline section.

## Contents

- [Key files](key-files.md) — every module, model, router, and job
- [How it works](how-it-works.md) — event flow, hot/cold snapshot tiers,
  revert semantics, history API, export, import
- [Rules](rules.md) — R1–R10 invariants
- [Skills](skills.md) — add a versioned entity type, extend the export
  bundle, add a section import, run the backfill, verify scheduler jobs
- [Tests pinning this block](tests.md)

## Related blocks

- [Caching stack](../caching/README.md) — event-logged mutations are exactly what flips the fingerprint-keyed build caches; section imports invalidate `WikidataStudioCache` explicitly
- [System Design](../../system-design.md)
