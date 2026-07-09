# HMO Wikibase Studio

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

HMO Wikibase Studio publishes the project's own Hebrew Manuscripts Ontology (HMO)
graph — classes, properties, and per-run instances (manuscripts, persons, places,
works…) — as native entities on the self-hosted Wikibase Cloud instance
(`https://mhm-hmo.wikibase.cloud`). It is the Wikidata-Studio equivalent for our
own ontology, and a **separate trust boundary from wikidata.org**: Rule 25's
moratorium gate does not apply here (desktop Rule 45).

Pipeline stages, in order:

1. **Schema bootstrap** — create every ontology class/property as a live
   Wikibase Property/Item (global, not per-run). Datatype mapping is
   ontology-driven (`ontology_schema_reader`); optional AI verify judges the
   bootstrap report (~387 entries) before/after live writes.
2. **Item build** — resolve a run's RDF TTL against the live schema mapping into
   `ResolvedWikibaseEntity` drafts, with SHACL issues attached.
3. **Review** — single-page curator surface on `HmoStudio`: always-visible
   lifecycle bar (**Build items**, **Rebuild (skip cache)**, **Reupload (update
   existing)**) above an item table with a **Data status** column (`new` /
   `will update existing` / `updated`), per-item overrides, approval flags,
   AI audit/autofix verdicts, live-Wikibase compare, export/import, and a
   "Last upload" column showing the durable create/adopt/update/skip/failed
   outcome of the most recent live push (from the `wikibase_cloud_writes`
   audit log, not just the presence of a mapping row).
4. **Upload** — two-pass create-or-update job (~7800 sequential writes on a
   large corpus) with reconcile-before-create, per-item commit, cancellation,
   progress, a full audit trail, opt-in pre/post-upload AI verification, and
   a single-item push endpoint for applying one curator/AI fix live without
   a full re-upload.
5. **IIIF manifests + coverage** — per-manuscript IIIF Presentation 3.0 manifests
   uploaded as wiki pages, plus an HMO → Wikidata projection-coverage report.

## Contents

- [Key files](key-files.md) — every backend/frontend file in this block and its purpose.
- [How it works](how-it-works.md) — schema bootstrap, item build, review, upload,
  writer, credentials + audit, coverage + manifests.
- [Upload outcomes + verify](upload-outcomes-and-verify.md) — durable
  create/adopt/update/failed outcomes on the review table, single-item push,
  opt-in pre/post-upload AI verification.
- [Rules](rules.md) — the 23 invariants (R1–R23) this block enforces.
- [Skills](skills.md) — operator playbooks: bootstrap, upload, debug coverage,
  rotate credentials, adopt existing items.
- [Tests](tests.md) — the test suites pinning this block.

## Related blocks

- [rdf-graph](../rdf-graph/README.md) — produces the TTL this block builds from (and
  `ensure_ttl_on_disk` restore).
- [job-service](../job-service/README.md) — claim/heartbeat/dedup semantics of the
  bootstrap, upload, and coverage jobs (Rule W-38).
- [caching](../caching/README.md) — inference cache (AI verdicts) and the durable
  Postgres cache pattern.
- [wikidata-studio](../wikidata-studio/README.md) — the wikidata.org sibling; its
  build fingerprint hashes `hmo_instance_qids` so HMO uploads invalidate
  P2888/P973 cross-links.
- [platform-security](../platform-security/README.md) — auth context, RBAC, encrypted
  key handling used by AI verify.
- [frontend](../frontend/README.md) — glass components, job-attachment hooks, and
  render-stability rules the HMO panels follow.
