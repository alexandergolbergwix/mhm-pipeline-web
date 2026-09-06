# Wikidata Studio

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

Wikidata Studio turns a run's curated data (MARC records + *approved* authority
matches + *approved* NER entities + curator item overrides) into real Wikidata
items — manuscripts, persons, works — using the shared `WikidataItemBuilder`
compatibility facade, then lets a curator review, edit, approve, reconcile,
and export/upload them. Canonical-source builds adapt durable
`hmo_canonical_entities` through `hmo_wikidata_pq_mapper` (ontology / project
Wikibase → public P/Q; never ID identity), then **merge full MARC/authority
claims** from the legacy builder (Rule W-125) so research properties are not
lost. The web pipeline reshapes DB rows
into builder input. Projection code is split into focused modules, including
the web-side source-aware work-candidate boundary; shared fixes need an
explicit upstream port before the next desktop sync. Callers keep importing
the same facade.

The block exists in the shadow of two real 2026-04 incidents: a mass-merge
disaster (902+ wrong merges) and a mass-duplicate/non-notable-creation
bulk-deletion request (~5,948 items). Its whole write path is therefore
**fail-closed**: reconcile-before-create with hard errors on lookup failure, a
validator moat that blocks any ERROR-severity item, four modification guards
inside the uploader, smart existence + own-or-accept modify (Rule W-99), and a
production upload-target gate (default `dry_run`; curator may choose `test` or
`live` — Rule W-103). Legacy Studio upload supports dry-run and test only.
The sealed Publication path is the only path for a live wikidata.org write
(Rule W-212).

## Contents

- [Key files](key-files.md) — every backend/frontend file in this block and its purpose.
- [How it works: build and cache](build-and-cache.md) — build pipeline +
  fingerprint cache, item overrides/approval/statement exclude, validator moat.
- [How it works: guards and upload](guards-and-upload.md) —
  reconcile-before-create, existence/ownership, upload job + moratorium + QS,
  AI review + autofix.
- [Production publication](production-publication.md) — immutable Release,
  digest-bound Review, dry-run Plan, queued Execution, and recovery.
- [Rules](rules.md) — the invariants (R1–R123) this block enforces.
- [Upload readiness](upload-readiness.md) — person identity, review prerequisites, and explicit deferred connections.
- [Phase 1 projection quality](projection-quality.md) — evidence gates for labels, notes, subjects, genres, roles, and current institutions.
- [Skills](skills.md) — operator playbooks: P/Q constants, validator checks,
  dry-runs, force-rebuild, blocked items, local quality audit, AI autofixes,
  foreign-modify accept, HMO→Wikidata PQ mapping.
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

- [Publication credentials](publication-credentials.md) — saved credentials and encrypted worker grants (W-217).

- [Publication dry-run jobs](../wikidata-studio/publication-dry-run-job.md) — asynchronous checks, progress, cancellation, and receipt refresh (W-218).
