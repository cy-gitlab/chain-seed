#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_api_worker_contention_sweep.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

WORKER_CAPS="${WORKER_CAPS:-2 4 8}"
BEACONS="${BEACONS:-300}"
STUB_AS_COUNT="${STUB_AS_COUNT:-64}"
TX_RATE="${TX_RATE:-1}"
API_LOAD_PHASES="${API_LOAD_PHASES:-baseline:0:1800,load_1x:0.2:1800,recovery_1x:0:600,load_5x:1:1800,recovery_5x:0:600,load_10x:2:1800,recovery_10x:0:600,load_20x:4:1800}"
API_TARGET_COUNT="${API_TARGET_COUNT:-30}"
API_CONTROL_COUNT="${API_CONTROL_COUNT:-30}"
API_MONITOR_PER_ROLE="${API_MONITOR_PER_ROLE:-8}"
API_CONCURRENCY_PER_NODE="${API_CONCURRENCY_PER_NODE:-4}"
API_REQUEST_TIMEOUT="${API_REQUEST_TIMEOUT:-5}"
PROCESSOR_METRICS_INTERVAL="${PROCESSOR_METRICS_INTERVAL:-2}"
DURATION_PADDING="${DURATION_PADDING:-60}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-1}"
LIGHTHOUSE_OFFICIAL_IMAGE="${LIGHTHOUSE_OFFICIAL_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
LIGHTHOUSE_COMPONENT_IMAGE="${LIGHTHOUSE_COMPONENT_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-baseline-components}"
BUILD_FORK_IMAGE="${BUILD_FORK_IMAGE:-0}"
SWEEP_ID="${SWEEP_ID:-$(date +%Y%m%d-%H%M%S)-api-worker-contention}"
SWEEP_ROOT="${SWEEP_ROOT:-$ETHEREUM_DIR/experiments/api-worker-contention}"
SWEEP_DIR="$SWEEP_ROOT/$SWEEP_ID"
RUNS_DIR="$SWEEP_DIR/runs"
SUMMARY_CSV="$SWEEP_DIR/runs.csv"
LOG_FILE="$SWEEP_DIR/sweep.log"
DRY_RUN=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Run serial API-contention experiments while changing only the Lighthouse worker cap.

Defaults:
  WORKER_CAPS="$WORKER_CAPS"
  BEACONS=$BEACONS
  API_TARGET_COUNT=$API_TARGET_COUNT
  API_MONITOR_PER_ROLE=$API_MONITOR_PER_ROLE
  API_LOAD_PHASES=$API_LOAD_PHASES

Options:
  --worker-caps LIST       Space-separated caps, e.g. "2" or "2 4 8".
  --beacons N              Beacon and Geth node count.
  --stub-as-count N        Stub AS count.
  --tx-rate RATE           Constant background transaction rate.
  --phases SCHEDULE        Comma-separated NAME:RATE_PER_NODE:DURATION phases.
  --target-count N         API-loaded Beacon count.
  --control-count N        Unloaded paired control Beacon count.
  --monitor-per-role N     Target and control nodes scraped for internal metrics.
  --build-fork-image       Build the component-aware Lighthouse image once first.
  --continue-on-failure    Continue with the next cap after a failed run.
  --dry-run                Validate arguments and print actions only.
  -h, --help               Show this help.

The default schedule lasts 10,800 seconds per worker cap, plus a 60-second drain window.
EtherView is disabled so that the controlled load generator is the only external observer load.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --worker-caps)
            WORKER_CAPS="$2"
            shift 2
            ;;
        --beacons)
            BEACONS="$2"
            shift 2
            ;;
        --stub-as-count)
            STUB_AS_COUNT="$2"
            shift 2
            ;;
        --tx-rate)
            TX_RATE="$2"
            shift 2
            ;;
        --phases)
            API_LOAD_PHASES="$2"
            shift 2
            ;;
        --target-count)
            API_TARGET_COUNT="$2"
            shift 2
            ;;
        --control-count)
            API_CONTROL_COUNT="$2"
            shift 2
            ;;
        --monitor-per-role)
            API_MONITOR_PER_ROLE="$2"
            shift 2
            ;;
        --build-fork-image)
            BUILD_FORK_IMAGE=1
            shift
            ;;
        --continue-on-failure)
            STOP_ON_FAILURE=0
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for value_name in BEACONS STUB_AS_COUNT API_TARGET_COUNT API_CONTROL_COUNT API_MONITOR_PER_ROLE API_CONCURRENCY_PER_NODE DURATION_PADDING STOP_ON_FAILURE BUILD_FORK_IMAGE; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done
if [[ "$API_TARGET_COUNT" -ne "$API_CONTROL_COUNT" ]]; then
    echo "paired design requires API_TARGET_COUNT == API_CONTROL_COUNT" >&2
    exit 2
fi
if (( API_TARGET_COUNT * 2 > BEACONS )); then
    echo "target and control cohorts require at least $((API_TARGET_COUNT * 2)) Beacon nodes" >&2
    exit 2
fi

phase_duration=0
IFS=',' read -r -a PHASE_ARRAY <<< "$API_LOAD_PHASES"
for phase in "${PHASE_ARRAY[@]}"; do
    IFS=':' read -r phase_name phase_rate phase_seconds extra <<< "$phase"
    if [[ -n "${extra:-}" || -z "$phase_name" || ! "$phase_rate" =~ ^[0-9]+([.][0-9]+)?$ || ! "$phase_seconds" =~ ^[0-9]+$ || "$phase_seconds" -lt 1 ]]; then
        echo "invalid API phase: $phase" >&2
        exit 2
    fi
    phase_duration=$((phase_duration + phase_seconds))
done
DURATION_SECONDS=$((phase_duration + DURATION_PADDING))

log() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '%s %s\n' "$(date '+%F %T')" "$*"
    else
        printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
    fi
}

if [[ "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$RUNS_DIR"
    if [[ ! -f "$SUMMARY_CSV" ]]; then
        printf 'worker_cap,status,exit_code,run_id,data_dir\n' > "$SUMMARY_CSV"
    fi
fi

read -r -a CAP_ARRAY <<< "$WORKER_CAPS"
if [[ "${#CAP_ARRAY[@]}" -eq 0 ]]; then
    echo "WORKER_CAPS is empty" >&2
    exit 2
fi

log "API worker contention sweep started"
log "sweep_dir=$SWEEP_DIR caps=[$WORKER_CAPS] phase_duration=${phase_duration}s total_duration=${DURATION_SECONDS}s"

if [[ "$DRY_RUN" -eq 0 && "$BUILD_FORK_IMAGE" -eq 1 ]]; then
    log "building Lighthouse component image=$LIGHTHOUSE_COMPONENT_IMAGE"
    env \
        LIGHTHOUSE_BASE_IMAGE="$LIGHTHOUSE_OFFICIAL_IMAGE" \
        LIGHTHOUSE_FORK_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE" \
        "$SCRIPT_DIR/build_lighthouse_component_fork.sh"
fi

for worker_cap in "${CAP_ARRAY[@]}"; do
    if [[ ! "$worker_cap" =~ ^[0-9]+$ ]] || (( worker_cap < 1 )); then
        echo "invalid worker cap: $worker_cap" >&2
        exit 2
    fi
    run_id="b${BEACONS}-workers${worker_cap}-api"
    data_dir="$RUNS_DIR/$run_id/fork_processor_workers_only"
    log "starting worker_cap=$worker_cap run_id=$run_id"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        continue
    fi
    if [[ -d "$data_dir" ]]; then
        echo "run data already exists: $data_dir" >&2
        echo "choose a new SWEEP_ID or remove the incomplete run explicitly" >&2
        exit 1
    fi

    set +e
    env \
        RUN_ROOT="$RUNS_DIR" \
        RUN_ID="$run_id" \
        BEACONS="$BEACONS" \
        STUB_AS_COUNT="$STUB_AS_COUNT" \
        VARIANTS="fork_processor_workers_only" \
        LIGHTHOUSE_OFFICIAL_IMAGE="$LIGHTHOUSE_OFFICIAL_IMAGE" \
        LIGHTHOUSE_COMPONENT_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE" \
        LIGHTHOUSE_PROCESSOR_WORKERS_CAP="$worker_cap" \
        LIGHTHOUSE_METRICS_ENABLED=1 \
        API_LOAD_ENABLED=1 \
        API_LOAD_PHASES="$API_LOAD_PHASES" \
        API_TARGET_COUNT="$API_TARGET_COUNT" \
        API_CONTROL_COUNT="$API_CONTROL_COUNT" \
        API_MONITOR_PER_ROLE="$API_MONITOR_PER_ROLE" \
        API_CONCURRENCY_PER_NODE="$API_CONCURRENCY_PER_NODE" \
        API_REQUEST_TIMEOUT="$API_REQUEST_TIMEOUT" \
        PROCESSOR_METRICS_INTERVAL="$PROCESSOR_METRICS_INTERVAL" \
        DURATION_SECONDS="$DURATION_SECONDS" \
        TX_RATE="$TX_RATE" \
        ETHER_VIEW=0 \
        PYTHON_BIN="$PYTHON_BIN" \
        BUILD_FORK_IMAGE=0 \
        KEEP_LAST=0 \
        "$SCRIPT_DIR/run_300_fork_component_benchmark.sh"
    exit_code=$?
    set -e

    status="failed"
    if [[ "$exit_code" -eq 0 ]]; then
        status="success"
    fi
    printf '%s,%s,%s,%s,%s\n' "$worker_cap" "$status" "$exit_code" "$run_id" "$data_dir" >> "$SUMMARY_CSV"
    log "finished worker_cap=$worker_cap status=$status exit_code=$exit_code"
    if [[ "$exit_code" -ne 0 && "$STOP_ON_FAILURE" -eq 1 ]]; then
        log "stopping after first failure; use --continue-on-failure to continue"
        break
    fi
done

log "API worker contention sweep completed summary=$SUMMARY_CSV"
