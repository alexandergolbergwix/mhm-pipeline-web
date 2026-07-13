# Context engineering toolkit

Deterministic pre-processing for agent workflows: compress CLI output and logs
**before** they enter the LLM context window. Targets 60–90% token reduction on
verbose test/build/log streams without losing failure signal.

## Scripts

| Script | Role |
|---|---|
| `agent_output_filter.py` | TOML-driven filter (ANSI strip, skip/keep regex, pytest failure blocks) |
| `log_extract.sh` | Fast `grep`/`zgrep` presets for common MHM sources |
| `run_with_filter.sh` | Run any command; emit filtered output + `[exit_code=N]` |

## Quick recipes

```bash
# Backend pytest — failures only
cd backend && .venv/bin/python -m pytest tests/unit/test_foo.py -q 2>&1 \
  | python3 ../scripts/context_engineering/agent_output_filter.py --preset pytest -

# One-liner with exit code preserved
scripts/context_engineering/run_with_filter.sh --preset pytest -- \
  bash -lc 'cd backend && .venv/bin/python -m pytest tests/ -q --tb=short'

# Heroku tail (last 2000 lines → errors)
heroku logs -n 2000 -a mhm-pipeline-web \
  | bash scripts/context_engineering/log_extract.sh heroku-errors -

# eval-agent trace from a verify session dir
bash scripts/context_engineering/log_extract.sh eval-trace /tmp/mhm-eval-agent-state/.../trace.jsonl

# Frontend typecheck noise reduction
cd frontend && yarn tsc --noEmit 2>&1 \
  | python3 ../scripts/context_engineering/agent_output_filter.py --preset yarn -
```

## Presets (`configs/*.toml`)

- `generic` — drop DEBUG / pip noise
- `pytest` — failure blocks + short summary
- `heroku` — 4xx/5xx, H12/R14, tracebacks, slow router lines
- `eval` — `[TRACE]` / `[STATS]` / verdict lines
- `yarn` — TS errors, vitest failures, ELIFECYCLE

Custom config: copy a TOML, edit `skip_patterns` / `keep_patterns`, pass
`--config path/to.toml`.

## Agent skill

See [`.codex/skills/context-engineering/SKILL.md`](../.codex/skills/context-engineering/SKILL.md).

## Tests

```bash
python3 scripts/context_engineering/test_agent_output_filter.py
```
