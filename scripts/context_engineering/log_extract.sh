#!/usr/bin/env bash
# Deterministic log extraction — run BEFORE pasting logs into an LLM.
# Usage: log_extract.sh <preset> [file|-]   (default file: stdin)

set -euo pipefail

PRESET="${1:?usage: log_extract.sh <preset> [file|-]}"
INPUT="${2:--}"

read_input() {
  if [[ "$INPUT" == "-" ]]; then
    cat
  else
    if [[ "$INPUT" == *.gz ]]; then
      zcat "$INPUT"
    else
      cat "$INPUT"
    fi
  fi
}

SRC="$(read_input)"

case "$PRESET" in
  pytest-failures)
    printf '%s\n' "$SRC" | grep -E '^(FAILED|ERROR|=+ FAIL|=+ ERRORS|E[[:space:]]|short test summary)' | head -300
    ;;
  pytest-errors-only)
    printf '%s\n' "$SRC" | grep -E '^(E[[:space:]]|FAILED|ERROR)' | head -200
    ;;
  heroku-errors)
    printf '%s\n' "$SRC" \
      | grep -Ev ' (200|204|304) ' \
      | grep -Ei 'error|exception|traceback|H1[0-9]|R1[0-9]|timeout|worker|SIGKILL|Memory quota|crashed' \
      | tail -400
    ;;
  heroku-router-slow)
    printf '%s\n' "$SRC" | grep -E 'service=[0-9]{4,}ms' | tail -100
    ;;
  eval-trace)
    printf '%s\n' "$SRC" | grep -E '\[(TRACE|STATS|STEP)\]' | tail -500
    ;;
  eval-verdicts)
    printf '%s\n' "$SRC" | grep -E 'agent\.verdict|"overall"' | tail -300
    ;;
  backend-errors)
    printf '%s\n' "$SRC" | grep -Ei 'ERROR|CRITICAL|Traceback|Exception' | grep -v 'DEBUG' | tail -300
    ;;
  yarn-build-fail)
    printf '%s\n' "$SRC" | grep -Ei 'error TS|✘|failed|ELIFECYCLE|Cannot find|Module not found' | head -150
    ;;
  *)
    echo "unknown preset: $PRESET" >&2
    echo "presets: pytest-failures pytest-errors-only heroku-errors heroku-router-slow eval-trace eval-verdicts backend-errors yarn-build-fail" >&2
    exit 1
    ;;
esac
