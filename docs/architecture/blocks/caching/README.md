# Caching stack

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

Every expensive computation in the backend — external inference calls (Modal
NER, VIAF, Mazal, KIMA, Wikidata SPARQL, Gemini AI verdicts) and per-run build
results (Wikidata Studio items, HMO Wikibase items, HMO coverage reports, RDF
TTL) — is served through a layered cache so the first call across the whole
team pays the cost and everyone else gets an instant answer that survives
Heroku dyno restarts.

Three distinct cache families exist, and they must not be confused:

1. **Global inference cache** (`inference_cache` table + Redis L1) — keyed by
   `(kind, sha256(canonical_json(query_summary)))`, shared across users and runs.
2. **Scoped read-model cache** (Redis/in-memory only, Tier 2) — keyed by
   run / project / user scope; for curator-facing GET responses that depend
   on context (e.g. the entity-table poll).
3. **Durable build caches** (one Postgres table per build kind) — keyed by a
   SHA-256 *input fingerprint*; invalidation is purely "the inputs changed".

## Contents

- [Key files](key-files.md) — every module, model, and job in this block
- [How it works](how-it-works.md) — tier diagram, TTL table per kind, Redis
  client, Tier-2 scoped cache, fingerprint-keyed durable build caches
- [Rules](rules.md) — R1–R9 invariants
- [Skills](skills.md) — add a cached kind / durable build cache / scoped
  cache; change a TTL
- [Tests pinning this block](tests.md)

## Related blocks

- [Versioning & Export](../versioning-export/README.md) — event-log mutations are the inputs that flip build-cache fingerprints (section imports explicitly rely on this)
- [System Design](../../system-design.md)
