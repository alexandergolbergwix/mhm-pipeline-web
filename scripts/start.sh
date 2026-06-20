#!/usr/bin/env bash
# Heroku web-dyno entrypoint. Boots the FastAPI app via uvicorn, binding
# to $PORT. The FastAPI app also serves the frontend's built static
# assets from frontend/dist (see backend/app/main.py).
#
# We cd into backend/ so uvicorn implicitly puts backend/ on sys.path —
# the in-source `from app.X import ...` pattern then resolves directly,
# matching how local dev works (uv run uvicorn app.main:app from the
# backend/ dir). The previous form `uvicorn backend.app.main:app` from
# the repo root crashed with `ModuleNotFoundError: No module named
# 'app'` because main.py imports cousin modules as `from app.X` rather
# than `from backend.app.X`. main.py's frontend-dist lookup uses a
# __file__-relative path (parents[2]) so it works regardless of cwd.
set -euo pipefail
ROOT="$(dirname "$0")/.."
export EVAL_AGENT_ROOT="${EVAL_AGENT_ROOT:-$ROOT/eval-agent}"
cd "$ROOT/backend"

PORT="${PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-1}"

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --workers "$WORKERS" \
    --proxy-headers \
    --forwarded-allow-ips='*'
