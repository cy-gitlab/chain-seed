#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_solana_limit_sweep.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOLANA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_SCRIPT="$SCRIPT_DIR/run_solana_large_experiment.sh"
SUMMARY_SCRIPT="$SCRIPT_DIR/summarize_solana_experiment.py"

PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

LEVELS="${LEVELS:-225 250 275 300}"
CURRENT_RUN_DIR="${CURRENT_RUN_DIR:-}"
WAIT_INTERVAL="${WAIT_INTERVAL:-300}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-50400}"
RUN_PREFIX_BASE="${RUN_PREFIX_BASE:-solana-limit-$(date +%Y%m%d-%H%M%S)}"
SUMMARY_CSV="${SUMMARY_CSV:-$SOLANA_DIR/experiments/${RUN_PREFIX_BASE}-summary.csv}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-1}"

usage() {
    cat <<EOF
Usage: $(basename "$0")

Environment overrides:
  CURRENT_RUN_DIR="$CURRENT_RUN_DIR"
      Optional existing run dir to wait for and validate before starting LEVELS.
  LEVELS="$LEVELS"
      Space-separated validator counts to run after CURRENT_RUN_DIR passes.
  RUN_PREFIX_BASE="$RUN_PREFIX_BASE"
  SUMMARY_CSV="$SUMMARY_CSV"
  WAIT_INTERVAL=$WAIT_INTERVAL
  MAX_WAIT_SECONDS=$MAX_WAIT_SECONDS
  STOP_ON_FAILURE=$STOP_ON_FAILURE

This script runs one experiment at a time. Defaults match the current
low-memory 200-validator profile and keep every validator active.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

for value_name in WAIT_INTERVAL MAX_WAIT_SECONDS STOP_ON_FAILURE; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done

mkdir -p "$(dirname "$SUMMARY_CSV")"

log() {
    printf '%s %s\n' "$(date '+%F %T')" "$*"
}

data_dir_for_run_dir() {
    local run_dir="$1"
    if [[ "$(basename "$run_dir")" == "solana-large" ]]; then
        printf '%s\n' "$run_dir"
    else
        printf '%s/solana-large\n' "$run_dir"
    fi
}

run_dir_still_running() {
    local run_dir="$1"
    local pid_file pid
    if [[ "$(basename "$run_dir")" == "solana-large" ]]; then
        pid_file="$(dirname "$run_dir")/launcher.pid"
    else
        pid_file="$run_dir/launcher.pid"
    fi
    if [[ -f "$pid_file" ]]; then
        pid="$(tr -d '[:space:]' < "$pid_file")"
        if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        return 1
    fi
    pgrep -af "$RUN_SCRIPT" | grep -qF -- "$(basename "$run_dir")"
}

summary_value() {
    local summary_file="$1"
    local key="$2"
    awk -F= -v k="$key" '$1 == k {print $2; exit}' "$summary_file" 2>/dev/null || true
}

append_summary_row() {
    local run_prefix="$1"
    local validators="$2"
    local status_code="$3"
    local data_dir="$4"
    local summary_file="$data_dir/experiment_summary.txt"

    if [[ ! -s "$SUMMARY_CSV" ]]; then
        echo "run_prefix,validators,status_code,status,active_min,active_last,slot_delta,tx_count_delta,duration_max_elapsed,duration_required_elapsed,summary_file,data_dir" > "$SUMMARY_CSV"
    fi

    local status active_line active_min active_last slot_line slot_delta tx_count_delta duration_line duration_max_elapsed duration_required_elapsed
    status="$(summary_value "$summary_file" status)"
    active_line="$(grep -E '^active_samples=' "$summary_file" 2>/dev/null || true)"
    active_min="$(sed -n 's/.*active_min=\([^ ]*\).*/\1/p' <<< "$active_line")"
    active_last="$(sed -n 's/.*active_last=\([^ ]*\).*/\1/p' <<< "$active_line")"
    slot_line="$(grep -E '^slot_start=' "$summary_file" 2>/dev/null || true)"
    slot_delta="$(sed -n 's/.*slot_delta=\([^ ]*\).*/\1/p' <<< "$slot_line")"
    tx_count_delta="$(sed -n 's/.*tx_count_delta=\([^ ]*\).*/\1/p' <<< "$slot_line")"
    duration_line="$(grep -E '^duration_target=' "$summary_file" 2>/dev/null || true)"
    duration_max_elapsed="$(sed -n 's/.*duration_max_elapsed=\([^ ]*\).*/\1/p' <<< "$duration_line")"
    duration_required_elapsed="$(sed -n 's/.*duration_required_elapsed=\([^ ]*\).*/\1/p' <<< "$duration_line")"

    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "$run_prefix" "$validators" "$status_code" "$status" \
        "$active_min" "$active_last" "$slot_delta" "$tx_count_delta" \
        "$duration_max_elapsed" "$duration_required_elapsed" \
        "$summary_file" "$data_dir" >> "$SUMMARY_CSV"
}

summarize_and_validate() {
    local run_prefix="$1"
    local validators="$2"
    local data_dir="$3"

    "$PYTHON_BIN" "$SUMMARY_SCRIPT" "$data_dir" \
        --expected-validators "$validators" \
        --min-active "$validators" \
        --output-json "$data_dir/experiment_summary.json" \
        --output-text "$data_dir/experiment_summary.txt" \
        --fail-on-fail
}

wait_for_existing_run() {
    local run_dir="$1"
    local data_dir
    data_dir="$(data_dir_for_run_dir "$run_dir")"
    local run_log="$run_dir/run.log"
    if [[ "$(basename "$run_dir")" == "solana-large" ]]; then
        run_log="$(dirname "$run_dir")/run.log"
    fi

    local elapsed=0
    log "waiting for existing run: $run_dir"
    while true; do
        if [[ -f "$run_log" ]] && grep -qE 'large Solana experiment (completed|failed)' "$run_log"; then
            log "existing run reached terminal state: $run_log"
            return 0
        fi
        if [[ -d "$data_dir" ]] && find "$data_dir" -maxdepth 1 -type f \( -name '*failed*' -o -name 'stall-detected.log' -o -name 'active-stall-detected.log' -o -name 'host-memory-watchdog-triggered.*' \) | grep -q .; then
            if ! run_dir_still_running "$run_dir"; then
                log "existing run failure marker detected and runner exited: $data_dir"
                return 0
            fi
        fi
        if [[ -f "$data_dir/experiment_summary.txt" ]]; then
            log "existing run summary detected; waiting for compose cleanup and terminal run.log marker"
        fi
        if (( elapsed >= MAX_WAIT_SECONDS )); then
            echo "timed out waiting for existing run: $run_dir" >&2
            return 1
        fi
        sleep "$WAIT_INTERVAL"
        elapsed=$((elapsed + WAIT_INTERVAL))
        log "still waiting for existing run elapsed=${elapsed}s"
    done
}

export PYTHON_BIN

# Tuned defaults used by the successful 200-validator smoke and current 10h run.
export STUB_AS_COUNT="${STUB_AS_COUNT:-64}"
export INTERNET_MAP="${INTERNET_MAP:-0}"
export SOLANA_GENESIS_STAGED_STARTUP="${SOLANA_GENESIS_STAGED_STARTUP:-1}"
export SOLANA_GENESIS_CORE_VALIDATORS="${SOLANA_GENESIS_CORE_VALIDATORS:-1}"
export SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS="${SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS:-100000000000}"
export SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS="${SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS:-1000000000000}"
export SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS="${SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS:-50000000}"
export STRICT_VALIDATOR_STATUS="${STRICT_VALIDATOR_STATUS:-0}"
export ACTIVE_COUNT_MODE="${ACTIVE_COUNT_MODE:-process-scan}"
export ACTIVE_INTERVAL="${ACTIVE_INTERVAL:-30}"
export ACTIVE_WAIT="${ACTIVE_WAIT:-7200}"
export HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-7200}"
export CORE_HEALTH_TIMEOUT="${CORE_HEALTH_TIMEOUT:-1800}"
export CORE_ACTIVE_WAIT="${CORE_ACTIVE_WAIT:-900}"
export CORE_SLOT_RETRIES="${CORE_SLOT_RETRIES:-10}"
export CORE_STABILIZE_SECONDS="${CORE_STABILIZE_SECONDS:-120}"
export CORE_STABILIZE_CHECK_INTERVAL="${CORE_STABILIZE_CHECK_INTERVAL:-15}"
export CORE_REQUIRE_TX_HEALTH="${CORE_REQUIRE_TX_HEALTH:-0}"
export REQUIRE_TX_HEALTH="${REQUIRE_TX_HEALTH:-0}"
export TX_CHECK_TIMEOUT="${TX_CHECK_TIMEOUT:-600}"
export TX_SUBMIT_TIMEOUT="${TX_SUBMIT_TIMEOUT:-90}"
export SOLANA_BEST_EFFORT_TIMEOUT="${SOLANA_BEST_EFFORT_TIMEOUT:-30}"
export STRICT_ACTIVE_SAMPLING="${STRICT_ACTIVE_SAMPLING:-1}"
export POST_HEALTH_STABILIZE_SECONDS="${POST_HEALTH_STABILIZE_SECONDS:-2400}"
export SLOT_RETRIES="${SLOT_RETRIES:-10}"
export SLOT_STALL_TIMEOUT="${SLOT_STALL_TIMEOUT:-600}"
export SLOT_STALL_GRACE="${SLOT_STALL_GRACE:-300}"
export SLOT_STALL_CHECK_INTERVAL="${SLOT_STALL_CHECK_INTERVAL:-60}"
export DURATION_SECONDS="${DURATION_SECONDS:-36000}"
export TX_RATE="${TX_RATE:-0.2}"
export SOLANA_METRICS_RPC_TIMEOUT="${SOLANA_METRICS_RPC_TIMEOUT:-180}"
export SOLANA_SLOTS_PER_EPOCH="${SOLANA_SLOTS_PER_EPOCH:-262144}"
export SOLANA_TARGET_TICK_DURATION_MS="${SOLANA_TARGET_TICK_DURATION_MS:-500000}"
export SOLANA_TICKS_PER_SLOT="${SOLANA_TICKS_PER_SLOT:-10}"
export SOLANA_DISABLE_WARMUP_EPOCHS="${SOLANA_DISABLE_WARMUP_EPOCHS:-1}"
export SOLANA_TPU_DISABLE_QUIC="${SOLANA_TPU_DISABLE_QUIC:-1}"
export SOLANA_TPU_ENABLE_UDP="${SOLANA_TPU_ENABLE_UDP:-1}"
export SOLANA_AGAVE_LOW_MEMORY_PROFILE="${SOLANA_AGAVE_LOW_MEMORY_PROFILE:-1}"
export SOLANA_AGAVE_LOW_MEMORY_LEVEL="${SOLANA_AGAVE_LOW_MEMORY_LEVEL:-ultra}"
export SOLANA_OBSERVER_BATCH_SIZE="${SOLANA_OBSERVER_BATCH_SIZE:-10}"
export SOLANA_OBSERVER_JOBS="${SOLANA_OBSERVER_JOBS:-1}"
export SOLANA_OBSERVER_BATCH_DELAY="${SOLANA_OBSERVER_BATCH_DELAY:-20}"
export SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT="${SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT:-2}"
export UP_PHASE_DELAY="${UP_PHASE_DELAY:-5}"
export WAIT_FOR_LOAD_BELOW="${WAIT_FOR_LOAD_BELOW:-200}"
export LOAD_WAIT_TIMEOUT="${LOAD_WAIT_TIMEOUT:-3600}"
export LOAD_WAIT_INTERVAL="${LOAD_WAIT_INTERVAL:-30}"
export HOST_MEMORY_MIN_AVAILABLE_GIB="${HOST_MEMORY_MIN_AVAILABLE_GIB:-50}"
export HOST_MEMORY_CHECK_INTERVAL="${HOST_MEMORY_CHECK_INTERVAL:-15}"
export CAPTURE_FINAL_DIAGNOSTICS="${CAPTURE_FINAL_DIAGNOSTICS:-0}"
export SKIP_BASE_BUILD="${SKIP_BASE_BUILD:-1}"
export BUILD_MODE="${BUILD_MODE:-nobuild-mount}"
export UP_PRESET="${UP_PRESET:-fast}"

if [[ -n "$CURRENT_RUN_DIR" ]]; then
    wait_for_existing_run "$CURRENT_RUN_DIR"
    current_data_dir="$(data_dir_for_run_dir "$CURRENT_RUN_DIR")"
    current_validators="$(summary_value "$current_data_dir/experiment_summary.txt" expected_validators)"
    if [[ -z "$current_validators" ]]; then
        current_validators="$(grep -E '^validators=' "$current_data_dir/experiment.env" 2>/dev/null | cut -d= -f2 | head -n 1 || true)"
    fi
    current_validators="${current_validators:-0}"
    if ! summarize_and_validate "$(basename "$CURRENT_RUN_DIR")" "$current_validators" "$current_data_dir"; then
        append_summary_row "$(basename "$CURRENT_RUN_DIR")" "$current_validators" 1 "$current_data_dir"
        echo "existing run did not pass strict summary checks: $CURRENT_RUN_DIR" >&2
        exit 1
    fi
    append_summary_row "$(basename "$CURRENT_RUN_DIR")" "$current_validators" 0 "$current_data_dir"
fi

read -r -a validator_levels <<< "$LEVELS"
for validators in "${validator_levels[@]}"; do
    if [[ ! "$validators" =~ ^[0-9]+$ ]] || (( validators < 1 )); then
        echo "invalid validator count in LEVELS: $validators" >&2
        exit 2
    fi

    run_prefix="${RUN_PREFIX_BASE}-v${validators}"
    data_dir="$SOLANA_DIR/experiments/$run_prefix/solana-large"
    log "starting validators=$validators duration=${DURATION_SECONDS}s run_prefix=$run_prefix"

    status=0
    env \
        RUN_PREFIX="$run_prefix" \
        VALIDATORS="$validators" \
        MIN_ACTIVE_SOLANA="$validators" \
        "$RUN_SCRIPT" --validators "$validators" --duration "$DURATION_SECONDS" --tx-rate "$TX_RATE" \
        || status=$?

    if [[ -d "$data_dir" ]]; then
        if ! summarize_and_validate "$run_prefix" "$validators" "$data_dir"; then
            status=1
        fi
        append_summary_row "$run_prefix" "$validators" "$status" "$data_dir"
    else
        status=1
        append_summary_row "$run_prefix" "$validators" "$status" "$data_dir"
    fi

    log "finished validators=$validators status=$status summary=$SUMMARY_CSV"
    if (( status != 0 && STOP_ON_FAILURE == 1 )); then
        echo "stopping limit sweep after failed level: validators=$validators status=$status" >&2
        exit "$status"
    fi
done

log "limit sweep completed summary=$SUMMARY_CSV"
