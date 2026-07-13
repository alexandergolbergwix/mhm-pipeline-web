# Context engineering — reference

Condensed from production context-engineering practice. Read only when tuning
filters, prompt layout, or agent memory — not every session.

## Why pre-filter

- Transformer prefill scales ~O(n²); long inputs increase latency and cost linearly.
- **Lost in the middle**: facts buried mid-prompt are recalled 15–30% less reliably.
- **Instruction budget**: ~150–200 reliable instructions; bloated logs compete with
  system rules and schema constraints.

## Prompt layer order (cache-friendly)

| Layer | Stability | Examples | Position |
|---|---|---|---|
| 1 | Absolute | Tool schemas, safety policy | Top |
| 2 | High | Persona, invariant few-shot | |
| 3 | Medium | RAG / architecture excerpts | |
| 4 | Growing | Conversation history | |
| 5 | Volatile | User query, **filtered log excerpt**, tool output | Bottom |

Put `agent_output_filter` output in layer 5. Never inject timestamps or
session IDs above the cache breakpoint.

## Filter design

| Mechanism | Use |
|---|---|
| `grep -v DEBUG` | Drop known-noise lines |
| `grep -E 'error\|FAIL'` | Keep triage candidates |
| `grep -C 5 'pattern'` | Localize failure without whole file |
| `keep_patterns` in TOML | Whitelist mode for noisy sources (Heroku) |
| `mode = pytest` | Semantic failure blocks |

Target **60–90% line drop** while keeping all stack traces and summary lines.

## Hierarchical agent config (this repo)

| Layer | Location | Load when |
|---|---|---|
| Enterprise / global | `~/AGENTS.md`, upstream `pipeline/AGENTS.md` | Every session |
| Project | `mhm-pipeline-web/AGENTS.md`, `CLAUDE.md` | Every session |
| Block rules | `docs/architecture/blocks/*/rules.md` | Touching that block |
| Skills | `.codex/skills/*/SKILL.md` | Task matches description |
| Path rules | `.cursor/rules/*.mdc` | Glob match |

Do not duplicate block rules into `CLAUDE.md` prose — link instead.

## When to suggest `/compact`

- Finished one debug/investigation arc before starting another
- Context high and next step is multi-file implementation
- Repeating questions or contradicting earlier decisions

Not mid-stack-trace analysis — keep raw error text in the active turn.
