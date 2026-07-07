---
name: docs-architecture-sync
description: >-
  Keeps docs/architecture (the per-block README/key-files/how-it-works/
  rules/skills/tests pages) synchronized with backend and frontend code
  changes in mhm-pipeline-web. Use before finishing any change that adds,
  renames, or removes a file, route, job kind, model field, or invariant —
  docs and code must land in the same change, never as a follow-up.
---

# Keep `docs/architecture` in sync with code changes

An out-of-date architecture page is a bug, not a nice-to-have. The next
agent reads a block's `README.md` + `rules.md` before touching it (see
`AGENTS.md`) — if the last change didn't write back, that agent starts from
a false model of the system.

## Workflow

1. Identify which block(s) under `docs/architecture/blocks/<block>/` the
   change touches. Unsure? Check
   [task-index.md](../../../docs/architecture/task-index.md) or the block
   table in [system-design.md](../../../docs/architecture/system-design.md).
   A change often touches more than one block (e.g. a new job kind touches
   both its owning feature block AND `job-service`, and often `eval-agent`
   if it's a verify job).
2. Update whichever of that block's pages the change affects:
   - `key-files.md` — new/renamed/removed file worth listing, or an
     existing file's one-line purpose changed.
   - `how-it-works.md` — changed flow/behavior; keep it narrative, cite
     `file.py:line` anchors like the existing prose does.
   - `rules.md` — new or changed invariant. Number it the next `RN` in that
     block's list and give it a one-line "*Why:*". Mirror it as an
     incident-annotated `Rule W-N` in root `CLAUDE.md` when the change is a
     hardening fix (bug found in production/review), not a new feature.
   - `AGENTS.md` — when agent entry guidance changes: bump the `W-1…W-N`
     pointer, add/retire a block in the index table, or document a new
     cross-cutting agent workflow. Keep it lean — details stay in CLAUDE.md
     and block `rules.md`.
   - `README.md` — operator quickstart / documentation table when the
     high-level project surface changes (new stage, new deploy note, rule
     range in the doc table).
   - `skills.md` — new step-by-step operational task (an endpoint sequence,
     a debug recipe, a curator workflow) a future agent will need to repeat.
   - `tests.md` — new test file/suite that pins the changed behavior.
   - `README.md` one-liner — only if the block's scope/summary itself
     changed (rare; most changes don't need this).
3. Cross-cutting changes (new global invariant, new stage, new trust
   boundary, a job kind or pattern that now appears in multiple blocks)
   also update [global-rules.md](../../../docs/architecture/global-rules.md)
   (G-series) and/or `system-design.md` / `data-flow.md`.
4. A genuinely new *recurring* task pattern (not a one-off) → add a row to
   [task-index.md](../../../docs/architecture/task-index.md).
5. Keep pages within the existing ≤100-line-per-page convention. Split into
   a new page under the block directory (and link it from `README.md`'s
   Contents list) rather than blowing past the limit.

## Common oversight

A shared file touches more than the block that "owns" it. Example: adding a
new background job kind that streams AI verdicts typically needs edits in
**three** blocks: the feature block (new endpoint/wiring), `job-service`
(new job-kind row in its kinds table), and `eval-agent` (new row/column in
its channels table, and any "N job-backed channels" count mentioned in
prose). Grep the other blocks' pages for the old count/list before
declaring the doc update done.

## Verifying you didn't miss a page

`git status --short` the docs you touched alongside the code diff — if the
code diff mentions a file, job kind, route, or rule number that doesn't
appear anywhere under `docs/architecture/`, go back and add it. Also grep
`AGENTS.md`, `README.md`, and `CLAUDE.md` for stale `W-1…W-N` pointers when
you add a new `Rule W-N`.
