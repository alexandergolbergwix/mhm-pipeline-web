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
bulk upload-target gate (default `dry_run`; API clients may choose `test` or
`live` — Rules W-103 / W-291). A live bulk job requires the curator's live
Settings credential but does not require a sealed Publication Release, Receipt,
or Execution. Publication is a separate sealed workflow (Rule W-212). The
single-item push route accepts `test` but returns `410 Gone` for `live`.
After a Publication prepare request returns 404, 405, or 410, the compatibility
panel lists all three bulk targets but enables submission only for `dry_run`.

## Contents

- [Key files](key-files.md) — every backend/frontend file in this block and its purpose.
- [How it works: build and cache](build-and-cache.md) — build pipeline +
  fingerprint cache, item overrides/approval/statement exclude, validator moat.
- [How it works: guards and upload](guards-and-upload.md) —
  reconcile-before-create, existence/ownership, upload job + moratorium + QS,
  AI review + autofix.
- [Production publication](production-publication.md) — immutable Release,
  digest-bound Review, dry-run Plan, queued Execution, and recovery.
- [Rules](rules.md) — the invariants (R1–R158) and known gaps.
- [Reference blocks](reference-blocks.md) — source grouping, old-data gates, deferred links, test wiki, and export limits (W-304).
- [Studio cache freshness](studio-cache-freshness.md) — build fingerprint, override application, and the Rebuild required badge.
- [Upload write guards](upload-write-guards.md) — Studio source recheck before each bulk write, and the owned-item rule.
- [Test-ledger local targets](test-ledger-local-targets.md) — sealed test QIDs and `publication_local_targets` registered before build.
- [Owner-only upload](owner-only-upload.md) — exact manuscript targets, live credential, preview, and limits (W-303).
- [Owner-only claim safety](owner-only-claim-safety.md) — replacement guards and snapshot/audit limits (W-303).
- [Upload readiness](upload-readiness.md) — person identity, review prerequisites, and explicit deferred connections.
- [Phase 1 projection quality](projection-quality.md) — evidence gates for labels, notes, subjects, genres, roles, and current institutions.
- [Skills](skills.md) — operator playbooks: P/Q constants, validator checks,
  dry-runs, force-rebuild, blocked items, local quality audit, AI autofixes,
  foreign-modify accept, HMO→Wikidata PQ mapping.
- [Local quality audit](quality-audit.md) — read-only measurement, count
  interpretation, and safe remediation order.
- [Gila audit completion](gila-audit-completion.md) — source-qualified claims,
  the accepted model, and the full-run review report (W-296).
- [Gila audit risks](gila-audit-risks.md) — open work-identity, folio, and historical reference-data gaps.
- [Historical repairs](historical-repairs.md) — authorized exact writes and durable receipts (W-307).
- [Gila source fidelity](gila-source-fidelity.md) — occurrence identity, source scope, per-fact comparisons, and guarded historical proposals.
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

- [AI review of blocked items](publication-ai-review.md) — report, cache, and explicit approval.

- [Existing items without updates](reference-only.md) — explicit identity choices and write exclusion.

- [Automatic resolution](automatic-resolution.md) — source checks, reuse, subset, and honest deferral.

- [Foreign-block resolution](foreign-block-resolution.md) — identity evidence, reconcile-method stamping, the blocked-item sweep, and the accept drawer (W-270/W-302).
