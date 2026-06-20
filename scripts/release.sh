#!/usr/bin/env bash
# Heroku release-phase: run pending migrations before any new dyno boots.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export EVAL_AGENT_ROOT="${EVAL_AGENT_ROOT:-$ROOT/eval-agent}"
echo "[release] checking eval-agent bundle…"
PY="python3"
if [ -x "$ROOT/backend/.venv/bin/python" ]; then
  PY="$ROOT/backend/.venv/bin/python"
fi
cd "$ROOT/backend" && "$PY" -c "from app.pipeline.agent_runner import locate_eval_agent; print('eval-agent:', locate_eval_agent())"
echo "[release] running Alembic migrations…"
cd "$ROOT/backend" && alembic upgrade head
