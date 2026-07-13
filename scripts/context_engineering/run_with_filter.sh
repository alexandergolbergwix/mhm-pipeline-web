#!/usr/bin/env bash
# Run a command, capture exit code, pipe combined stdout/stderr through agent_output_filter.
# Usage: run_with_filter.sh --preset pytest -- <command...>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRESET="generic"
CONFIG=""
MAX_LINES=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preset)
      PRESET="${2:?}"
      shift 2
      ;;
    --config)
      CONFIG="${2:?}"
      shift 2
      ;;
    --max-lines)
      MAX_LINES="${2:?}"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ $# -lt 1 ]]; then
  echo "usage: run_with_filter.sh [--preset NAME] [--config PATH] [--max-lines N] -- <cmd...>" >&2
  exit 2
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

set +e
"$@" >"$TMP" 2>&1
CODE=$?
set -e

FILTER_ARGS=(--exit-code "$CODE")
if [[ -n "$CONFIG" ]]; then
  FILTER_ARGS+=(--config "$CONFIG")
else
  FILTER_ARGS+=(--preset "$PRESET")
fi
if [[ -n "$MAX_LINES" ]]; then
  FILTER_ARGS+=(--max-lines "$MAX_LINES")
fi

python3 "$SCRIPT_DIR/agent_output_filter.py" "${FILTER_ARGS[@]}" "$TMP"
exit 0
