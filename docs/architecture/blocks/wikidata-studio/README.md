# Wikidata Studio

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

Wikidata Studio turns a run's curated data (MARC records + *approved* authority
matches + *approved* NER entities + curator item overrides) into real Wikidata
items — manuscripts, persons, works — using the **desktop pipeline's builder
verbatim** (`converter.wikidata.item_builder.WikidataItemBuilder`), then lets a
curator review, edit, approve, reconcile, and finally export/upload them. The
web layer (`backend/app/pipeline/wikidata_studio.py`) is thin glue: it reshapes
DB rows into the desktop's input format and never re-implements builder logic,
so every desktop safety fix arrives by file sync. The public builder is a compatibility facade over focused projection modules; callers continue to import `WikidataItemBuilder` unchanged.

The block exists in the shadow of two real 2026-04 incidents: a mass-merge
disaster (902+ wrong merges) and a mass-duplicate/non-notable-creation
bulk-deletion request (~5,948 items). Its whole write path is therefore
**fail-closed**: reconcile-before-create with hard errors on lookup failure, a
validator moat that blocks any ERROR-severity item, four modification guards
inside the uploader, and a production moratorium gate (`MORATORIUM_LIFTED`)
that refuses live wikidata.org writes by default.

## Contents

- [Key files](key-files.md) — every backend/frontend file in this block and its purpose.
- [How it works: build and cache](build-and-cache.md) — build pipeline +
  fingerprint cache, item overrides/approval/statement exclude, validator moat.
- [How it works: guards and upload](guards-and-upload.md) —
  reconcile-before-create, upload job + moratorium + QS download, AI review +
  autofix.
- [Rules](rules.md) — the 21 invariants (R1–R21) this block enforces.
- [Skills](skills.md) — operator playbooks: P/Q constants, validator checks,
  dry-runs, force-rebuild, blocked items, local quality audit, AI autofixes.
- [Local quality audit](quality-audit.md) — read-only measurement, count
  interpretation, and safe remediation order.
- [Tests](tests.md) — the test suites pinning this block.

## Related blocks

- [authority](../authority/README.md) — approved `AuthorityMatch` rows (+ payload cluster_ids, preferred names) are the builder's identifier source.
- [hmo-wikibase-studio](../hmo-wikibase-studio/README.md) — `wikibase_entity_mappings` feed `hmo_instance_qids_for_run` (P2888/P973 targets).
- [job-service](../job-service/README.md) — `wikidata_studio_build` and `wikidata_upload` job kinds; 409-attach protocol; token unwrapping into job params.
- [caching](../caching/README.md) — `WikidataStudioCache` fingerprint tier; `wikidata.label` inference-cache kind (Rule W-25/W-39 family).
- [eval-agent](../eval-agent/README.md) — `audit_wikidata_item` / `autofix_from_wikidata` actions and the verify SSE session layout.
- [frontend](../frontend/README.md) — `WikidataStudio.tsx`, glass components, run-job attachment hooks (Rule W-36).
