# Background Run-Job Service

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

The run-job service is the single mechanism for anything a curator triggers that
cannot finish inside Heroku's 30-second HTTP router timeout: NER extraction over a
whole run, authority re-enrichment, RDF builds, AI-verify sessions (NER / authority /
Wikidata / HMO item), Wikidata Studio builds and uploads, HMO coverage reports, HMO
Wikibase schema bootstrap, and HMO item uploads. A router POST creates a `run_jobs` row and
returns immediately; an in-process `asyncio` task does the work; the frontend polls
(and receives WebSocket pushes) until the row reaches a terminal status.

It is deliberately **not** a queue broker. Postgres rows are the queue, the ledger,
and the coordination primitive. Multi-dyno safety comes from row *ownership*
(`claimed_by = WORKER_ID`), a 60-second maintenance loop that heartbeats owned rows,
reaps stale ones, and respawns orphans, and a partial unique index that makes
"one active job per (run, kind)" a database-level guarantee (CLAUDE.md Rule W-38).

The service also handles secrets: user-scoped credentials (HuggingFace token, Gemini
API key, Wikidata token) are unwrapped at job-start time in the request context
(where the user's KEK is available) and stored in `params` under underscore-prefixed
keys that the serialiser strips before anything reaches the API or frontend.

Progress is a free-form JSONB dict (conventionally `{phase, processed, total,
message, ...}`) written via `update_job_progress`, which also pushes a
`run_job_update` payload through the existing Postgres NOTIFY → WebSocket bridge so
open clients see updates without waiting for the next poll tick.

## Contents

- [Key files](key-files.md) — every module, model, migration, hook, and test that makes up the service
- [How it works](how-it-works.md) — lifecycle, job kinds and owners, frontend attachment
- [Rules](rules.md) — R1–R16 invariants (claiming, staleness, secrets, cancellation, UI)
- [Skills & tests](skills-and-tests.md) — add a job kind, debug a stuck job, attach UI; tests pinning this block

## Related blocks

- [eval-agent](../eval-agent/README.md) — verify jobs stream from the eval-agent subprocess
- [extraction](../extraction/README.md) — `extraction` + `ner_verify` job surfaces
- [authority](../authority/README.md) — `authority_re_enrich` + `authority_verify`
- [rdf-graph](../rdf-graph/README.md) — `rdf_build` and the `RdfArtifact` write-through
- [hmo-wikibase-studio](../hmo-wikibase-studio/README.md) — `hmo_coverage`, `hmo_schema_bootstrap`, `hmo_item_upload`, `hmo_item_verify`
- [wikidata-studio](../wikidata-studio/README.md) — `wikidata_studio_build`, `wikidata_upload`, `wikidata_verify`
- [caching](../caching/README.md) — durable Postgres counterparts for job outputs (Rules W-26/W-39)
- [frontend](../frontend/README.md) — `runJobs` store, attachment hooks, render-stability rules
- [deployment](../deployment/README.md) — dyno restarts, `WORKER_ID`, Heroku 30 s router timeout
- [platform-security](../platform-security/README.md) — KEK-wrapped secrets injected into job params
- [versioning-export](../versioning-export/README.md) · [research](../research/README.md)
