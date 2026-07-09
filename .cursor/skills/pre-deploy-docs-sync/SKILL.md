---
name: pre-deploy-docs-sync
description: >-
  Mandatory gate before Heroku deploy or git push to GitHub in mhm-pipeline-web.
  Use when the user asks to deploy, push, release, or ship — after code is done
  but BEFORE any git push, heroku push, or gh command. Runs the architecture
  docs mapping (task-index) to confirm every code change in the branch has
  matching docs/architecture updates, CLAUDE.md/AGENTS.md pointers, and tests.
  Never skip because "docs were done earlier" without re-verifying the diff.
---

# Pre-deploy / pre-push docs sync (mandatory gate)

**Do not deploy or push to GitHub until this gate passes.** Permission to push
is separate (user must say "push" each time); this skill is what you run *before*
asking or executing that push.

Applies to:

- `git push` (any remote)
- `gh pr create`, `gh release`, workflow dispatch
- `git push heroku main` / Heroku deploy
- `modal deploy` when `modal/` or shared converter paths changed

Does **not** replace [docs-on-code-change](../docs-on-code-change/SKILL.md) during
implementation — that skill runs while coding; this skill is the **final audit**
immediately before ship.

## Gate checklist (run in order)

Copy and complete every item:

```
Pre-deploy docs gate:
- [ ] 1. git status + git diff (staged + unstaged) — list every changed path
- [ ] 2. Read docs/architecture/task-index.md — map each changed path to block(s)
- [ ] 3. Read .codex/skills/docs-architecture-sync/SKILL.md — apply its workflow
- [ ] 4. For each touched block under docs/architecture/blocks/<block>/, verify:
         key-files.md, how-it-works.md, rules.md, skills.md, tests.md (as needed)
- [ ] 5. Cross-block grep: job-service, eval-agent, frontend, deployment if shared
         plumbing (job kinds, verify channels, env vars) changed
- [ ] 6. New Rule W-N in CLAUDE.md → AGENTS.md + README.md + global-rules.md
         pointers bumped to W-1…W-N
- [ ] 7. git diff: no route/kind/file/invariant in code without a doc mention
- [ ] 8. Tell the user which doc files were updated (or "docs already current for diff")
```

## Block picker (start here)

[docs/architecture/task-index.md](../../../docs/architecture/task-index.md) —
the mapping tree. When the diff spans multiple areas, update **every** block
touched, not only the "main" feature.

| Diff touches | Minimum doc pages to check |
|---|---|
| `backend/app/routers/`, `backend/app/pipeline/` | Owning block + often `job-service` |
| `eval-agent/`, verify streams | `eval-agent` + owning channel block |
| `frontend/src/` | `frontend` + owning feature block |
| `backend/converter/`, `graph_builder` | `rdf-graph` and/or `hmo-wikibase-studio` |
| `modal/` | `extraction` + `deployment` |
| `scripts/release.sh`, `Procfile`, env | `deployment` (`env-vars.md` if new var) |

## Definition of ready to deploy/push

**Blocked** if any of:

- Code mentions a new file, route, job kind, model field, or invariant absent from
  `docs/architecture/` and not recorded as `Rule W-N` in `CLAUDE.md`
- `AGENTS.md` or `README.md` still shows `W-1…W-(N-1)` after adding `Rule W-N`
- A block's `key-files.md` lists a deleted/renamed file
- You have uncommitted doc fixes still needed for the current diff

**Ready** when:

- Docs and code are in the same commit set (or you explicitly list doc files the
  user should commit alongside code)
- Completion message includes: `Docs: <paths updated>` or `Docs: verified current`

## Relationship to other skills

| Skill | When |
|---|---|
| [docs-on-code-change](../docs-on-code-change/SKILL.md) | After each code edit session, before "task done" |
| [docs-architecture-sync](../docs-architecture-sync/SKILL.md) | Detailed how-to for block page updates |
| **pre-deploy-docs-sync** (this file) | Immediately before deploy or git push |

## After the gate

1. Summarize deploy/push scope for the user (commits, Heroku vs GitHub).
2. Ask for explicit permission before `git push` / `gh` / Heroku (per user rules).
3. Do not amend commits to sneak docs in — add a docs commit or one combined commit
   per user preference.
