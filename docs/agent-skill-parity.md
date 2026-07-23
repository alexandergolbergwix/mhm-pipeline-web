# Claude/Codex skill parity

The shared pipeline command catalogs are currently identical (`.claude/commands/` and `.codex/commands/`). Global skills use different names in some cases; Codex should use these equivalents:

| Claude global skill | Codex equivalent |
|---|---|
| `analyze-logs`, `filter-logs` | `context-engineering` |
| `debug`, `investigate-module` | `diagnosing-bugs` |
| `review-code` | `code-review` |
| `refactor` | `codebase-design` |
| `test-gen` | `tdd` |
| `lookup-api` | `research` (and official docs skills when applicable) |
| `quick-commit` | repository AGENTS.md git workflow; no automatic commit shortcut |
| `pr-create` | `github-permissions` plus explicit user permission |
| `quiet-run` | `context-engineering` output filtering |
| `dev3*`, `hevy`, `wix-aws-access`, `fix-mac-ci-checkout` | same-named Codex skill |

Claude-only names are not omitted: they are routed to the Codex equivalent above. Project-specific `.codex/skills/` remain authoritative for pipeline runtime, eval-agent, documents, and presentation work.
