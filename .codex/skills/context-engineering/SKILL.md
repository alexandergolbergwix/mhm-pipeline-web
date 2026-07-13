---
name: context-engineering
description: >-
  Compresses logs and CLI output before LLM analysis using deterministic
  grep/awk presets and agent_output_filter.py (tokf-style). Use when debugging
  with large pytest logs, Heroku tails, eval-agent traces, yarn/tsc output,
  CI failures, or when context is high and the user mentions context rot,
  token budget, or log stuffing.
---

# Context engineering (MHM Pipeline Web)

**Never paste raw megabyte logs into the model.** Filter at the OS layer first,
then reason over the dense residue.

## When to invoke

- Investigating CI/pytest/Heroku/eval-agent failures with verbose output
- Context window is high; user asks to compact, grep, or pre-process logs
- Before `Read` on multi-MB log files, build artifacts, or `trace.jsonl`
- Running long test suites where only failures matter

Skip for: small single-file edits, reading one stack trace already in chat,
question-only replies.

## Decision tree

```
Large output to analyze?
├─ < 80 lines and already focused → read directly
├─ pytest / vitest / tsc → run_with_filter.sh or agent_output_filter --preset pytest|yarn
├─ heroku logs → log_extract.sh heroku-errors (or agent_output_filter --preset heroku)
├─ eval-agent trace → log_extract.sh eval-trace | eval-verdicts
└─ unknown format → log_extract.sh backend-errors OR --preset generic
```

## Core invariants

1. **Deterministic first, LLM second** — `grep`, `awk`, `agent_output_filter.py`
   before `Read` or summarization.
2. **Preserve failure locality** — keep tracebacks + N lines of context (`grep -C`
   in shell; pytest mode keeps `E` blocks).
3. **Strip ANSI** — color codes waste tokens and break tokenizers.
4. **Volatile data at the bottom** — when composing prompts manually, put
   extracted log snippets *after* stable system/instruction blocks (prompt-cache
   friendly).
5. **Budget** — aim for < 300 filtered lines / < ~8k tokens of log text per
   investigation hop; paginate if more signal is needed.

## Scripts (repo root)

| Tool | Path |
|---|---|
| TOML filter | [`scripts/context_engineering/agent_output_filter.py`](../../scripts/context_engineering/agent_output_filter.py) |
| grep presets | [`scripts/context_engineering/log_extract.sh`](../../scripts/context_engineering/log_extract.sh) |
| run + filter | [`scripts/context_engineering/run_with_filter.sh`](../../scripts/context_engineering/run_with_filter.sh) |
| README | [`scripts/context_engineering/README.md`](../../scripts/context_engineering/README.md) |

## MHM recipes

### Backend pytest (failures only)

```bash
scripts/context_engineering/run_with_filter.sh --preset pytest -- \
  bash -lc 'cd backend && .venv/bin/python -m pytest tests/test_foo.py -q --tb=short'
```

### Heroku production errors

```bash
heroku logs -n 3000 -a mhm-pipeline-web \
  | bash scripts/context_engineering/log_extract.sh heroku-errors - \
  | head -200
```

### eval-agent verify session

```bash
TRACE=/tmp/mhm-eval-agent-state/wikidata-verify-sessions/<run>/sessions/<id>/trace.jsonl
bash scripts/context_engineering/log_extract.sh eval-trace "$TRACE"
```

### Frontend typecheck

```bash
scripts/context_engineering/run_with_filter.sh --preset yarn -- \
  bash -lc 'cd frontend && yarn tsc --noEmit'
```

## Anti-patterns

- `Read` on entire `heroku logs` dumps or full `pytest -v` without filtering
- Asking the model to "find the error" in 50k lines of 200 OK router lines
- Stuffing `AGENTS.md` / `CLAUDE.md` excerpts into the user message when a
  path-scoped rule or skill link suffices (progressive disclosure)

## Progressive disclosure

- Preset TOML details: [`scripts/context_engineering/configs/`](../../scripts/context_engineering/configs/)
- Prompt-cache layer ordering + instruction budget: [reference.md](reference.md)
- Measurement-only verify (separate): [local-measure-verify](../local-measure-verify/SKILL.md)

## Tests

```bash
python3 scripts/context_engineering/test_agent_output_filter.py
```
