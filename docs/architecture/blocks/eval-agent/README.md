# Eval-Agent AI Verification System

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

Runs the vendored **eval-agent** (a per-candidate agentic judge backed by
Gemini or other tier-1 models from `tier1_models.yaml`) over curator-scoped
rows and streams live verdicts back to the browser. Five verify
channels share one runner core: authority matches, NER/extraction entities,
Wikidata Studio items, HMO Wikibase items, and HMO Wikibase schema entries. Each
channel builds a filtered JSON fixture on disk, spawns `eval-agent run` as a
**subprocess** (never a Python import), translates its stdout line protocol into
`AgentEvent`s, streams them over SSE, persists the trace for replay, writes
verdict summaries back onto the owning DB row, and write-throughs the shared
`inference_cache` (kind `ai_verdict`) so future sessions warm-hit without Gemini.

## Contents

- [Key files](key-files.md) — runner core, per-channel routers/streams, vendored CLI, rubrics, frontend modals
- [Session pipeline & channels](session-pipeline.md) — the shared SSE session pipeline and the five channels' quirks
- [State, jobs & caching](state-and-caching.md) — background jobs + session snapshots, state-dir layout, verdict summary shape, cache tiers, frontend
- [Rules](rules.md) — R1–R39 invariants (trust boundary, state dirs, cache keys, SSE, secrets, tier-1 registry, HMO item MARC scope, schema prompt context)
- [Skills & tests](skills-and-tests.md) — add a channel, debug/replay a session, inspect the cache, analyze non-passing exports with Codex; tests pinning this block

## Related blocks

- [job-service](../job-service/README.md) — claim/heartbeat lifecycle of the verify job kinds
- [extraction](../extraction/README.md) — `ExtractionApproval` rows and the entity review table the NER channel serves
- [authority](../authority/README.md) — `AuthorityMatch` rows, guards, and the payload the authority evaluator judges
- [wikidata-studio](../wikidata-studio/README.md) — Studio item build the wikidata channel verifies
- [hmo-wikibase-studio](../hmo-wikibase-studio/README.md) — HMO items + schema bootstrap the two HMO channels verify
- [caching](../caching/README.md) — `inference_cache` + Redis L1 (Rule W-25)
- [frontend](../frontend/README.md) — modal components, SSE client pattern, `useApprovalStore` polling
- [deployment](../deployment/README.md) — Heroku slug read-only FS, `start.sh` env exports, `release.sh` fail-fast (Rule W-33)
