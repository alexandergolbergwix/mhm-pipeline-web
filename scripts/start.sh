#!/usr/bin/env bash
# Heroku web-dyno entrypoint. Boots the FastAPI app via uvicorn, binding
# to $PORT. The FastAPI app also serves the frontend's built static
# assets from frontend/dist (see backend/app/main.py).
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-1}"

exec uvicorn backend.app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --workers "$WORKERS" \
    --proxy-headers \
    --forwarded-allow-ips='*'
