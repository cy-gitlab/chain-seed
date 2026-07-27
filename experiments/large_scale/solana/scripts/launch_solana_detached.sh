#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "launch_solana_detached.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOLANA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_SCRIPT="$SCRIPT_DIR/run_solana_large_experiment.sh"

VALIDATORS="${VALIDATORS:-300}"
if [[ -z "${RUN_PREFIX:-}" ]]; then
    RUN_PREFIX="$(date +%Y%m%d-%H%M%S)-solana-v${VALIDATORS}"
fi
export RUN_PREFIX

RUN_DIR="$SOLANA_DIR/experiments/$RUN_PREFIX"
mkdir -p "$RUN_DIR"

LAUNCH_LOG="$RUN_DIR/launcher.log"
LAUNCH_ENV="$RUN_DIR/launcher.env"
PID_FILE="$RUN_DIR/launcher.pid"

if [[ -f "$PID_FILE" ]]; then
    old_pid="$(tr -dc '0-9' < "$PID_FILE" || true)"
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
        echo "launcher already appears to be running: pid=$old_pid run_dir=$RUN_DIR" >&2
        exit 3
    fi
fi

{
    echo "run_prefix=$RUN_PREFIX"
    echo "run_dir=$RUN_DIR"
    echo "started_at=$(date -Is)"
    echo "command=$RUN_SCRIPT $*"
    env | sort
} > "$LAUNCH_ENV"

cd "$SOLANA_DIR"
nohup setsid "$RUN_SCRIPT" "$@" > "$LAUNCH_LOG" 2>&1 < /dev/null &
pid="$!"
echo "$pid" > "$PID_FILE"
disown "$pid" 2>/dev/null || true

echo "launched pid=$pid"
echo "run_dir=$RUN_DIR"
echo "launcher_log=$LAUNCH_LOG"
echo "run_log=$RUN_DIR/run.log"
