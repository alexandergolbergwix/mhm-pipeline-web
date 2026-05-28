#!/usr/bin/env bash
# Heroku release-phase: run pending migrations before any new dyno boots.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "[release] running Alembic migrations…"
cd backend && alembic upgrade head
