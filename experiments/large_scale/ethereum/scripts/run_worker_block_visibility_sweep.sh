#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_worker_block_visibility_sweep.sh requires bash." >&2
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
DURATION_SECONDS="${DURATION_SECONDS:-36000}"
TX_RATE="${TX_RATE:-1}"
BLOCK_EVENT_BEACON_SAMPLE="${BLOCK_EVENT_BEACON_SAMPLE:-64}"
BLOCK_EVENT_MIN_READY_RATIO="${BLOCK_EVENT_MIN_READY_RATIO:-0.95}"
BLOCK_EVENT_MIN_COVERAGE_RATIO="${BLOCK_EVENT_MIN_COVERAGE_RATIO:-0.95}"
BLOCK_EVENT_WARMUP_SECONDS="${BLOCK_EVENT_WARMUP_SECONDS:-300}"
BLOCK_EVENT_TAIL_SECONDS="${BLOCK_EVENT_TAIL_SECONDS:-24}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-1}"
LIGHTHOUSE_OFFICIAL_IMAGE="${LIGHTHOUSE_OFFICIAL_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
LIGHTHOUSE_COMPONENT_IMAGE="${LIGHTHOUSE_COMPONENT_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-baseline-components}"
BUILD_FORK_IMAGE="${BUILD_FORK_IMAGE:-0}"
SWEEP_ID="${SWEEP_ID:-$(date +%Y%m%d-%H%M%S)-worker-block-visibility}"
SWEEP_ROOT="${SWEEP_ROOT:-$ETHEREUM_DIR/experiments/worker-block-visibility}"
SWEEP_DIR="$SWEEP_ROOT/$SWEEP_ID"
RUNS_DIR="$SWEEP_DIR/runs"
RUN_INDEX="$SWEEP_DIR/runs.csv"
SUMMARY_CSV="$SWEEP_DIR/summary.csv"
LOG_FILE="$SWEEP_DIR/sweep.log"
DRY_RUN=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Run serial high-resolution block-visibility experiments while changing only
the Lighthouse Beacon Processor worker cap.

Defaults:
  WORKER_CAPS="$WORKER_CAPS"
  BEACONS=$BEACONS
  DURATION_SECONDS=$DURATION_SECONDS
  BLOCK_EVENT_BEACON_SAMPLE=$BLOCK_EVENT_BEACON_SAMPLE
  TX_RATE=$TX_RATE

Options:
  --worker-caps LIST       Space-separated caps, for example "2" or "2 4 8".
  --beacons N              Beacon and Geth node count.
  --stub-as-count N        Stub AS count.
  --duration SEC           Sampling time per worker cap.
  --tx-rate RATE           Constant background transaction rate.
  --event-sample N         Beacon nodes observed through persistent event streams.
  --build-fork-image       Build the component-aware Lighthouse image once first.
  --continue-on-failure    Continue with the next cap after a failed run.
  --dry-run                Validate and print actions without starting containers.
  -h, --help               Show this help.
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
        --duration)
            DURATION_SECONDS="$2"
            shift 2
            ;;
        --tx-rate)
            TX_RATE="$2"
            shift 2
            ;;
        --event-sample)
            BLOCK_EVENT_BEACON_SAMPLE="$2"
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

for value_name in BEACONS STUB_AS_COUNT DURATION_SECONDS BLOCK_EVENT_BEACON_SAMPLE BLOCK_EVENT_WARMUP_SECONDS BLOCK_EVENT_TAIL_SECONDS STOP_ON_FAILURE BUILD_FORK_IMAGE; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done
for value_name in TX_RATE BLOCK_EVENT_MIN_READY_RATIO BLOCK_EVENT_MIN_COVERAGE_RATIO; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        echo "$value_name must be a non-negative number." >&2
        exit 2
    fi
done
if (( BLOCK_EVENT_BEACON_SAMPLE < 2 || BLOCK_EVENT_BEACON_SAMPLE > BEACONS )); then
    echo "BLOCK_EVENT_BEACON_SAMPLE must be between 2 and BEACONS." >&2
    exit 2
fi
if (( DURATION_SECONDS <= BLOCK_EVENT_WARMUP_SECONDS + BLOCK_EVENT_TAIL_SECONDS )); then
    echo "DURATION_SECONDS must exceed warmup plus tail time." >&2
    exit 2
fi
if ! "$PYTHON_BIN" -c \
    'import sys; value=float(sys.argv[1]); raise SystemExit(0 if 0 < value <= 1 else 1)' \
    "$BLOCK_EVENT_MIN_READY_RATIO"; then
    echo "BLOCK_EVENT_MIN_READY_RATIO must be in (0, 1]." >&2
    exit 2
fi
if ! "$PYTHON_BIN" -c \
    'import sys; value=float(sys.argv[1]); raise SystemExit(0 if 0.95 <= value <= 1 else 1)' \
    "$BLOCK_EVENT_MIN_COVERAGE_RATIO"; then
    echo "BLOCK_EVENT_MIN_COVERAGE_RATIO must be in [0.95, 1]." >&2
    exit 2
fi

read -r -a CAP_ARRAY <<< "$WORKER_CAPS"
if [[ "${#CAP_ARRAY[@]}" -eq 0 ]]; then
    echo "WORKER_CAPS is empty" >&2
    exit 2
fi
for worker_cap in "${CAP_ARRAY[@]}"; do
    if [[ ! "$worker_cap" =~ ^[0-9]+$ ]] || (( worker_cap < 1 )); then
        echo "invalid worker cap: $worker_cap" >&2
        exit 2
    fi
done

log() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '%s %s\n' "$(date '+%F %T')" "$*"
    else
        printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
    fi
}

if [[ "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$RUNS_DIR"
    if [[ ! -f "$RUN_INDEX" ]]; then
        printf 'worker_cap,status,exit_code,run_id,data_dir\n' > "$RUN_INDEX"
    fi
fi

log "worker block-visibility sweep started"
log "sweep_dir=$SWEEP_DIR caps=[$WORKER_CAPS] duration=${DURATION_SECONDS}s event_sample=$BLOCK_EVENT_BEACON_SAMPLE"

if [[ "$DRY_RUN" -eq 0 && "$BUILD_FORK_IMAGE" -eq 1 ]]; then
    log "building Lighthouse component image=$LIGHTHOUSE_COMPONENT_IMAGE"
    env \
        LIGHTHOUSE_BASE_IMAGE="$LIGHTHOUSE_OFFICIAL_IMAGE" \
        LIGHTHOUSE_FORK_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE" \
        "$SCRIPT_DIR/build_lighthouse_component_fork.sh"
fi

for worker_cap in "${CAP_ARRAY[@]}"; do
    run_id="b${BEACONS}-workers${worker_cap}-block-visibility"
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
        DURATION_SECONDS="$DURATION_SECONDS" \
        TX_RATE="$TX_RATE" \
        ETHER_VIEW=0 \
        CONSENSUS_INTERVAL=12 \
        CONSENSUS_BEACON_SAMPLE=32 \
        BLOCK_EVENT_ENABLED=1 \
        BLOCK_EVENT_BEACON_SAMPLE="$BLOCK_EVENT_BEACON_SAMPLE" \
        BLOCK_EVENT_MIN_READY_RATIO="$BLOCK_EVENT_MIN_READY_RATIO" \
        BLOCK_EVENT_MIN_COVERAGE_RATIO="$BLOCK_EVENT_MIN_COVERAGE_RATIO" \
        BLOCK_EVENT_WARMUP_SECONDS="$BLOCK_EVENT_WARMUP_SECONDS" \
        BLOCK_EVENT_TAIL_SECONDS="$BLOCK_EVENT_TAIL_SECONDS" \
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
    printf '%s,%s,%s,%s,%s\n' "$worker_cap" "$status" "$exit_code" "$run_id" "$data_dir" >> "$RUN_INDEX"

    per_run_summary="$data_dir/block-visibility/summary.csv"
    if [[ -s "$per_run_summary" ]]; then
        if [[ ! -s "$SUMMARY_CSV" ]]; then
            cp "$per_run_summary" "$SUMMARY_CSV"
        else
            tail -n 1 "$per_run_summary" >> "$SUMMARY_CSV"
        fi
    fi
    log "finished worker_cap=$worker_cap status=$status exit_code=$exit_code"
    if [[ "$exit_code" -ne 0 && "$STOP_ON_FAILURE" -eq 1 ]]; then
        log "stopping after first failure; use --continue-on-failure to continue"
        break
    fi
done

log "worker block-visibility sweep completed summary=$SUMMARY_CSV"
