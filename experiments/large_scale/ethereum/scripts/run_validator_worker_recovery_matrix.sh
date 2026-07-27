#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_validator_worker_recovery_matrix.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

# Each point is VALIDATOR_KEYS_PER_CLIENT:BEACON_PROCESSOR_WORKER_CAP.
MATRIX="${MATRIX:-1:2 4:4 4:8 8:4 8:8}"
BEACONS="${BEACONS:-300}"
STUB_AS_COUNT="${STUB_AS_COUNT:-64}"
DURATION_SECONDS="${DURATION_SECONDS:-36000}"
TX_RATE="${TX_RATE:-1}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-1}"
INCLUDE_CAP2_REFERENCES="${INCLUDE_CAP2_REFERENCES:-1}"
SWEEP_ID="${SWEEP_ID:-validator-worker-recovery-$(date +%Y%m%d-%H%M%S)}"
SWEEP_ROOT="${SWEEP_ROOT:-$ETHEREUM_DIR/experiments/validator-worker-recovery}"
SWEEP_DIR="$SWEEP_ROOT/$SWEEP_ID"
RUNS_DIR="$SWEEP_DIR/runs"
SUMMARY_CSV="$SWEEP_DIR/summary.csv"
LOG_FILE="$SWEEP_DIR/sweep.log"
REFERENCE_ROOT="${REFERENCE_ROOT:-$ETHEREUM_DIR/experiments/validator-pressure/validator-pressure-20260717-120130/runs}"
LIGHTHOUSE_OFFICIAL_IMAGE="${LIGHTHOUSE_OFFICIAL_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
LIGHTHOUSE_COMPONENT_IMAGE="${LIGHTHOUSE_COMPONENT_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-worker-cap}"
BUILD_FORK_IMAGE="${BUILD_FORK_IMAGE:-0}"
DRY_RUN=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Run the serial validator-load/worker-cap recovery matrix. A matrix point uses
KEYS_PER_VALIDATOR_CLIENT:WORKER_CAP syntax.

Defaults:
  MATRIX="$MATRIX"
  BEACONS=$BEACONS
  GETH=$BEACONS
  VALIDATOR_CLIENTS=3 * BEACONS
  DURATION_SECONDS=$DURATION_SECONDS
  TX_RATE=$TX_RATE

The default matrix reruns 900 validators with worker cap 2, then tests worker
caps 4 and 8 at 3,600 and 7,200 active validators. Existing cap-2 results for
3,600 and 7,200 validators are added to summary.csv as read-only references.

Options:
  --matrix LIST              Space-separated KEYS:WORKERS points.
  --beacons N                Beacon count; Geth=N, Validator clients=3*N.
  --stub-as-count N          Stub AS count.
  --duration SEC             Sampling duration after health passes.
  --tx-rate RATE             Continuous transaction send rate.
  --build-fork-image         Build the configurable Lighthouse image once.
  --no-cap2-references       Do not include existing cap-2 reference rows.
  --continue-on-failure      Continue after an operationally failed run.
  --dry-run                  Validate and print the matrix only.
  -h, --help                 Show this help.

Important environment variables:
  MATRIX, BEACONS, STUB_AS_COUNT, DURATION_SECONDS, TX_RATE,
  STOP_ON_FAILURE, INCLUDE_CAP2_REFERENCES, REFERENCE_ROOT,
  SWEEP_ID, SWEEP_ROOT, PYTHON_BIN, HEALTH_TIMEOUT, HEALTH_INTERVAL,
  MEMORY_INTERVAL, CONSENSUS_BEACON_SAMPLE, VALIDATOR_LIVENESS_TIMEOUT,
  VALIDATOR_LIVENESS_BATCH_SIZE, UP_PRESET, BUILD_MODE,
  LIGHTHOUSE_OFFICIAL_IMAGE, LIGHTHOUSE_COMPONENT_IMAGE, BUILD_FORK_IMAGE
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --matrix)
            MATRIX="$2"
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
        --build-fork-image)
            BUILD_FORK_IMAGE=1
            shift
            ;;
        --no-cap2-references)
            INCLUDE_CAP2_REFERENCES=0
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
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for value_name in BEACONS STUB_AS_COUNT DURATION_SECONDS STOP_ON_FAILURE INCLUDE_CAP2_REFERENCES BUILD_FORK_IMAGE; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done
for value_name in BEACONS STUB_AS_COUNT DURATION_SECONDS; do
    value="${!value_name}"
    if (( value < 1 )); then
        echo "$value_name must be positive." >&2
        exit 2
    fi
done
if [[ ! "$TX_RATE" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "TX_RATE must be a non-negative number." >&2
    exit 2
fi
for value_name in STOP_ON_FAILURE INCLUDE_CAP2_REFERENCES BUILD_FORK_IMAGE; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[01]$ ]]; then
        echo "$value_name must be 0 or 1." >&2
        exit 2
    fi
done
if [[ "$INCLUDE_CAP2_REFERENCES" -eq 1 ]] && {
    [[ "$BEACONS" -ne 300 ]] || [[ "$STUB_AS_COUNT" -ne 64 ]] ||
    [[ "$DURATION_SECONDS" -ne 36000 ]] || [[ ! "$TX_RATE" =~ ^1([.]0+)?$ ]];
}; then
    echo "cap-2 references require BEACONS=300, STUB_AS_COUNT=64," >&2
    echo "DURATION_SECONDS=36000, and TX_RATE=1; use --no-cap2-references otherwise" >&2
    exit 2
fi

VALIDATOR_CLIENTS=$((BEACONS * 3))
read -r -a MATRIX_POINTS <<< "$MATRIX"
if [[ "${#MATRIX_POINTS[@]}" -eq 0 ]]; then
    echo "MATRIX is empty." >&2
    exit 2
fi

declare -A SEEN_POINTS=()
for point in "${MATRIX_POINTS[@]}"; do
    IFS=: read -r keys_per_client worker_cap extra <<< "$point"
    if [[ -n "${extra:-}" || ! "$keys_per_client" =~ ^[0-9]+$ || ! "$worker_cap" =~ ^[0-9]+$ ]]; then
        echo "invalid matrix point '$point'; expected positive KEYS:WORKERS" >&2
        exit 2
    fi
    if (( keys_per_client < 1 || worker_cap < 1 )); then
        echo "invalid matrix point '$point'; values must be positive" >&2
        exit 2
    fi
    if [[ -n "${SEEN_POINTS[$point]:-}" ]]; then
        echo "duplicate matrix point: $point" >&2
        exit 2
    fi
    SEEN_POINTS[$point]=1
done

log() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '%s %s\n' "$(date '+%F %T')" "$*"
    else
        printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
    fi
}

append_summary() {
    local data_dir="$1"
    local keys_per_client="$2"
    local worker_cap="$3"
    local status="$4"
    local exit_code="$5"
    local run_id="$6"

    "$PYTHON_BIN" "$SCRIPT_DIR/summarize_validator_pressure.py" \
        --data-dir "$data_dir" \
        --output "$SUMMARY_CSV" \
        --validator-keys-per-client "$keys_per_client" \
        --validator-clients "$VALIDATOR_CLIENTS" \
        --beacons "$BEACONS" \
        --worker-cap "$worker_cap" \
        --duration "$DURATION_SECONDS" \
        --tx-rate "$TX_RATE" \
        --status "$status" \
        --exit-code "$exit_code" \
        --run-id "$run_id"
}

if [[ "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$RUNS_DIR"
    {
        echo "sweep_id=$SWEEP_ID"
        echo "matrix=$MATRIX"
        echo "beacons=$BEACONS"
        echo "geth=$BEACONS"
        echo "validator_clients=$VALIDATOR_CLIENTS"
        echo "stub_as_count=$STUB_AS_COUNT"
        echo "duration_seconds=$DURATION_SECONDS"
        echo "tx_rate=$TX_RATE"
        echo "include_cap2_references=$INCLUDE_CAP2_REFERENCES"
        echo "reference_root=$REFERENCE_ROOT"
        echo "lighthouse_component_image=$LIGHTHOUSE_COMPONENT_IMAGE"
        echo "started_at=$(date -Is)"
    } > "$SWEEP_DIR/sweep.env"
fi

log "validator-worker recovery matrix started"
log "sweep_dir=$SWEEP_DIR"
log "matrix=[$MATRIX] clients=$VALIDATOR_CLIENTS duration=${DURATION_SECONDS}s tx_rate=$TX_RATE"

if [[ "$INCLUDE_CAP2_REFERENCES" -eq 1 ]]; then
    for keys_per_client in 4 8; do
        active_validators=$((VALIDATOR_CLIENTS * keys_per_client))
        reference_dir="$REFERENCE_ROOT/b300-vc900-v${active_validators}-k${keys_per_client}/fork_processor_workers_only"
        reference_id="reference-b300-vc900-v${active_validators}-k${keys_per_client}-workers2"
        log "cap-2 reference active_validators=$active_validators data_dir=$reference_dir"
        if [[ "$DRY_RUN" -eq 0 ]]; then
            if [[ ! -f "$reference_dir/experiment.env" ]]; then
                echo "missing cap-2 reference data: $reference_dir" >&2
                exit 1
            fi
            append_summary "$reference_dir" "$keys_per_client" 2 reference-existing 0 "$reference_id"
        fi
    done
fi

if [[ "$DRY_RUN" -eq 0 && "$BUILD_FORK_IMAGE" -eq 1 ]]; then
    log "building configurable Lighthouse image=$LIGHTHOUSE_COMPONENT_IMAGE"
    env \
        LIGHTHOUSE_BASE_IMAGE="$LIGHTHOUSE_OFFICIAL_IMAGE" \
        LIGHTHOUSE_FORK_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE" \
        "$SCRIPT_DIR/build_lighthouse_component_fork.sh"
fi

for point in "${MATRIX_POINTS[@]}"; do
    IFS=: read -r keys_per_client worker_cap <<< "$point"
    active_validators=$((VALIDATOR_CLIENTS * keys_per_client))
    run_id="b${BEACONS}-vc${VALIDATOR_CLIENTS}-v${active_validators}-k${keys_per_client}-workers${worker_cap}"
    data_dir="$RUNS_DIR/$run_id/fork_processor_workers_only"
    log "starting point=$point active_validators=$active_validators run_id=$run_id"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        continue
    fi

    set +e
    env \
        RUN_ROOT="$RUNS_DIR" \
        RUN_ID="$run_id" \
        BEACONS="$BEACONS" \
        VALIDATOR_KEYS_PER_CLIENT="$keys_per_client" \
        STUB_AS_COUNT="$STUB_AS_COUNT" \
        VARIANTS="fork_processor_workers_only" \
        LIGHTHOUSE_OFFICIAL_IMAGE="$LIGHTHOUSE_OFFICIAL_IMAGE" \
        LIGHTHOUSE_COMPONENT_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE" \
        LIGHTHOUSE_PROCESSOR_WORKERS_CAP="$worker_cap" \
        DURATION_SECONDS="$DURATION_SECONDS" \
        TX_RATE="$TX_RATE" \
        PYTHON_BIN="$PYTHON_BIN" \
        BUILD_FORK_IMAGE=0 \
        KEEP_LAST=0 \
        "$SCRIPT_DIR/run_300_fork_component_benchmark.sh"
    exit_code=$?
    set -e

    if [[ "$exit_code" -eq 0 ]]; then
        status="success"
    else
        status="failed"
    fi
    log "finished point=$point active_validators=$active_validators status=$status exit_code=$exit_code"
    append_summary "$data_dir" "$keys_per_client" "$worker_cap" "$status" "$exit_code" "$run_id"

    if [[ "$exit_code" -ne 0 && "$STOP_ON_FAILURE" -eq 1 ]]; then
        log "stopping after first operational failure; use --continue-on-failure to continue"
        break
    fi
done

log "validator-worker recovery matrix completed summary=$SUMMARY_CSV"
