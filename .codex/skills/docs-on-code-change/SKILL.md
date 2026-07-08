---
name: docs-on-code-change
description: >-
  Mandatory docs sync gate before finishing any code change in mhm-pipeline-web.
  Use when you add, rename, or remove backend/frontend files, routes, job kinds,
  model fields, invariants, or user-visible behavior — read this skill and follow
  docs-architecture-sync before marking the task complete. Skip only for
  question-only replies with zero file edits.
---

# Docs on every code change (mandatory gate)

**A code change is not done until docs land in the same change.** Do not tell the
user the task is finished, offer to commit, or move on until this gate passes.

## When this skill applies

| Applies | Skip |
|---|---|
| Any edited/added/deleted source file under `backend/`, `frontend/`, `eval-agent/`, `modal/` | Pure Q&A — no files touched |
| New/renamed route, job kind, env var, DB column, UI surface | Typo fix in a comment only (no behavior/docs drift) |
| Production/review hardening fix → new `Rule W-N` | User explicitly said "code only, no docs" |

When unsure, **apply the skill**.

## Gate (run in order)

Copy this checklist and complete every item before finishing:

```
Docs gate:
- [ ] 1. Read .codex/skills/docs-architecture-sync/SKILL.md
- [ ] 2. Identify affected block(s) under docs/architecture/blocks/
- [ ] 3. Update block pages (key-files, how-it-works, rules, skills, tests — as needed)
- [ ] 4. Cross-block grep: job-service / eval-agent / frontend if shared plumbing changed
- [ ] 5. Incident/hardening fix → Rule W-N in CLAUDE.md + bump W-1…W-N in AGENTS.md + README.md
- [ ] 6. Agent entry change only → AGENTS.md (keep lean; details in CLAUDE.md / block rules)
- [ ] 7. git diff: every new route/kind/file/invariant in code appears in docs or CLAUDE.md
```

## Quick block picker

Unsure which block? Start at
[docs/architecture/task-index.md](../../../docs/architecture/task-index.md) or
[system-design.md](../../../docs/architecture/system-design.md).

Common multi-block edits:

| You changed | Also update |
|---|---|
| New `run_jobs` kind | Owning feature block + `job-service` + often `eval-agent` |
| Verify stream / eval-agent | `eval-agent` + owning channel block + `frontend` if modal/hook |
| Curator UI table/modal | `frontend` + owning feature block |
| RDF / SHACL / graph builder | `rdf-graph` (+ sync note if desktop mirror involved) |

## Definition of done

The task is **incomplete** if:

- Code diff mentions a file, route, job kind, or invariant absent from `docs/architecture/`
- You added `Rule W-N` in `CLAUDE.md` but `AGENTS.md` / `README.md` still say `W-1…W-(N-1)`
- You removed/renamed a file still listed in a block's `key-files.md`

Tell the user what doc files you updated in the completion summary (one short line).

## Full workflow

All page-level detail lives in
[docs-architecture-sync/SKILL.md](../docs-architecture-sync/SKILL.md) —
this skill is the **when + gate**; that skill is the **how**.
