#!/usr/bin/env bash
# Heroku worker-dyno entrypoint (job-service R27). Executes the memory-
# heavy run_jobs kinds (graph builds, bulk verifies, uploads, publication)
# off the web dyno so a job's memory spike can never kill the UI.
#
# The worker runs the same maintenance loop as the web app: startup
# recovery, heartbeats, stale reaping, orphan respawn, and queued-job
# admission — a shorter interval keeps pickup latency low. Claim
# arbitration in run_job_service makes double-spawn (web + worker) a
# harmless no-op for the loser.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export EVAL_AGENT_ROOT="${EVAL_AGENT_ROOT:-$ROOT/eval-agent}"
export EVAL_AGENT_STATE_DIR="${EVAL_AGENT_STATE_DIR:-/tmp/mhm-eval-agent-state}"
export RUN_JOB_ROLE="${RUN_JOB_ROLE:-worker}"
export RUN_JOB_MAINTENANCE_INTERVAL="${RUN_JOB_MAINTENANCE_INTERVAL:-10}"
cd "$ROOT/backend"

exec python -m app.jobs_worker
