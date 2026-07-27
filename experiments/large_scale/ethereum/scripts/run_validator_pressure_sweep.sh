#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_validator_pressure_sweep.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

VALIDATOR_KEYS_PER_CLIENTS="${VALIDATOR_KEYS_PER_CLIENTS:-2 4 8}"
BEACONS="${BEACONS:-300}"
WORKER_CAP="${WORKER_CAP:-2}"
STUB_AS_COUNT="${STUB_AS_COUNT:-64}"
DURATION_SECONDS="${DURATION_SECONDS:-36000}"
TX_RATE="${TX_RATE:-1}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-1}"
SWEEP_ID="${SWEEP_ID:-$(date +%Y%m%d-%H%M%S)-validator-pressure}"
SWEEP_ROOT="${SWEEP_ROOT:-$ETHEREUM_DIR/experiments/validator-pressure}"
SWEEP_DIR="$SWEEP_ROOT/$SWEEP_ID"
RUNS_DIR="$SWEEP_DIR/runs"
SUMMARY_CSV="$SWEEP_DIR/summary.csv"
LOG_FILE="$SWEEP_DIR/sweep.log"
LIGHTHOUSE_OFFICIAL_IMAGE="${LIGHTHOUSE_OFFICIAL_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
LIGHTHOUSE_COMPONENT_IMAGE="${LIGHTHOUSE_COMPONENT_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-worker-cap}"
REUSE_BASELINE="${REUSE_BASELINE:-1}"
BASELINE_DATA_DIR="${BASELINE_DATA_DIR:-$ETHEREUM_DIR/experiments/worker-cap-sweep/worker-cap-20260714-175416/runs/b300-workers2/fork_processor_workers_only}"
DRY_RUN=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Run a serial validator-pressure sweep with a fixed number of Validator client
containers. Pressure increases by assigning more genesis keys to each client.

Defaults:
  VALIDATOR_KEYS_PER_CLIENTS="$VALIDATOR_KEYS_PER_CLIENTS"
  BEACONS=$BEACONS
  VALIDATOR_CLIENTS=3 * BEACONS
  WORKER_CAP=$WORKER_CAP
  DURATION_SECONDS=$DURATION_SECONDS
  TX_RATE=$TX_RATE

The existing 900-validator worker-cap=2 run is reused as the baseline. With
the default BEACONS=300, new runs cover 1800, 3600, and 7200 active validators
while retaining 900 Validator clients.

Options:
  --keys-per-client LIST     Space-separated levels, e.g. "1 2 4 8".
  --beacons N                Beacon count; Geth=N and Validator clients=3*N.
  --worker-cap N             Beacon processor worker cap. Default: $WORKER_CAP.
  --stub-as-count N          Stub AS count. Default: $STUB_AS_COUNT.
  --duration SEC             Sampling duration after health passes.
  --tx-rate RATE             Continuous transaction send rate.
  --continue-on-failure      Continue after a failed pressure level.
  --no-reuse-baseline        Do not add the existing 900-validator run to the summary.
  --dry-run                  Validate and print the experiment matrix only.
  -h, --help                 Show this help.

Important environment variables:
  VALIDATOR_KEYS_PER_CLIENTS, BEACONS, WORKER_CAP, STUB_AS_COUNT,
  DURATION_SECONDS, TX_RATE, STOP_ON_FAILURE, SWEEP_ID, SWEEP_ROOT,
  REUSE_BASELINE, BASELINE_DATA_DIR,
  PYTHON_BIN, HEALTH_TIMEOUT, HEALTH_INTERVAL, MEMORY_INTERVAL,
  CONSENSUS_BEACON_SAMPLE, VALIDATOR_LIVENESS_TIMEOUT,
  VALIDATOR_LIVENESS_BATCH_SIZE, UP_PRESET, BUILD_MODE,
  LIGHTHOUSE_OFFICIAL_IMAGE, LIGHTHOUSE_COMPONENT_IMAGE
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keys-per-client)
            VALIDATOR_KEYS_PER_CLIENTS="$2"
            shift 2
            ;;
        --beacons)
            BEACONS="$2"
            shift 2
            ;;
        --worker-cap)
            WORKER_CAP="$2"
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
        --continue-on-failure)
            STOP_ON_FAILURE=0
            shift
            ;;
        --no-reuse-baseline)
            REUSE_BASELINE=0
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

for value_name in BEACONS WORKER_CAP STUB_AS_COUNT DURATION_SECONDS STOP_ON_FAILURE REUSE_BASELINE; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done
for value_name in BEACONS WORKER_CAP STUB_AS_COUNT; do
    value="${!value_name}"
    if (( value < 1 )); then
        echo "$value_name must be positive." >&2
        exit 2
    fi
done
if [[ ! "$STOP_ON_FAILURE" =~ ^[01]$ ]]; then
    echo "STOP_ON_FAILURE must be 0 or 1." >&2
    exit 2
fi
if [[ ! "$REUSE_BASELINE" =~ ^[01]$ ]]; then
    echo "REUSE_BASELINE must be 0 or 1." >&2
    exit 2
fi

VALIDATOR_CLIENTS=$((BEACONS * 3))
read -r -a KEY_LEVELS <<< "$VALIDATOR_KEYS_PER_CLIENTS"
if [[ "${#KEY_LEVELS[@]}" -eq 0 ]]; then
    echo "VALIDATOR_KEYS_PER_CLIENTS is empty." >&2
    exit 2
fi
for keys_per_client in "${KEY_LEVELS[@]}"; do
    if [[ ! "$keys_per_client" =~ ^[0-9]+$ ]] || (( keys_per_client < 1 )); then
        echo "invalid keys-per-client level: $keys_per_client" >&2
        exit 2
    fi
    if [[ "$REUSE_BASELINE" -eq 1 && "$keys_per_client" -eq 1 ]]; then
        echo "keys-per-client=1 duplicates the reused baseline; remove 1 or set REUSE_BASELINE=0" >&2
        exit 2
    fi
done
if [[ "$REUSE_BASELINE" -eq 1 ]] && {
    [[ "$BEACONS" -ne 300 ]] || [[ "$WORKER_CAP" -ne 2 ]] ||
    [[ "$STUB_AS_COUNT" -ne 64 ]] || [[ "$DURATION_SECONDS" -ne 36000 ]] ||
    [[ "$TX_RATE" != "1" ]];
}; then
    echo "the reusable baseline requires BEACONS=300, WORKER_CAP=2, STUB_AS_COUNT=64," >&2
    echo "DURATION_SECONDS=36000, and TX_RATE=1; otherwise set REUSE_BASELINE=0" >&2
    exit 2
fi

log() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '%s %s\n' "$(date '+%F %T')" "$*"
    else
        printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
    fi
}

if [[ "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$RUNS_DIR"
    {
        echo "sweep_id=$SWEEP_ID"
        echo "beacons=$BEACONS"
        echo "geth=$BEACONS"
        echo "validator_clients=$VALIDATOR_CLIENTS"
        echo "validator_keys_per_client_levels=$VALIDATOR_KEYS_PER_CLIENTS"
        echo "worker_cap=$WORKER_CAP"
        echo "stub_as_count=$STUB_AS_COUNT"
        echo "duration_seconds=$DURATION_SECONDS"
        echo "tx_rate=$TX_RATE"
        echo "reuse_baseline=$REUSE_BASELINE"
        echo "baseline_data_dir=$BASELINE_DATA_DIR"
        echo "lighthouse_component_image=$LIGHTHOUSE_COMPONENT_IMAGE"
        echo "started_at=$(date -Is)"
    } > "$SWEEP_DIR/sweep.env"
fi

log "validator-pressure sweep started"
log "sweep_dir=$SWEEP_DIR"
log "keys_per_client=[$VALIDATOR_KEYS_PER_CLIENTS] clients=$VALIDATOR_CLIENTS worker_cap=$WORKER_CAP"

if [[ "$REUSE_BASELINE" -eq 1 ]]; then
    if [[ ! -f "$BASELINE_DATA_DIR/experiment.env" ]]; then
        echo "baseline data not found: $BASELINE_DATA_DIR" >&2
        echo "set REUSE_BASELINE=0, or provide BASELINE_DATA_DIR" >&2
        exit 1
    fi
    log "reusing existing baseline active_validators=$VALIDATOR_CLIENTS data_dir=$BASELINE_DATA_DIR"
    if [[ "$DRY_RUN" -eq 0 ]]; then
        "$PYTHON_BIN" "$SCRIPT_DIR/summarize_validator_pressure.py" \
            --data-dir "$BASELINE_DATA_DIR" \
            --output "$SUMMARY_CSV" \
            --validator-keys-per-client 1 \
            --validator-clients "$VALIDATOR_CLIENTS" \
            --beacons "$BEACONS" \
            --worker-cap "$WORKER_CAP" \
            --duration 36000 \
            --tx-rate 1 \
            --status reused-success \
            --exit-code 0 \
            --run-id existing-b300-workers2-v900
    fi
fi

for keys_per_client in "${KEY_LEVELS[@]}"; do
    active_validators=$((VALIDATOR_CLIENTS * keys_per_client))
    run_id="b${BEACONS}-vc${VALIDATOR_CLIENTS}-v${active_validators}-k${keys_per_client}"
    data_dir="$RUNS_DIR/$run_id/fork_processor_workers_only"
    log "starting keys_per_client=$keys_per_client active_validators=$active_validators run_id=$run_id"

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
        LIGHTHOUSE_PROCESSOR_WORKERS_CAP="$WORKER_CAP" \
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
    log "finished active_validators=$active_validators status=$status exit_code=$exit_code"

    "$PYTHON_BIN" "$SCRIPT_DIR/summarize_validator_pressure.py" \
        --data-dir "$data_dir" \
        --output "$SUMMARY_CSV" \
        --validator-keys-per-client "$keys_per_client" \
        --validator-clients "$VALIDATOR_CLIENTS" \
        --beacons "$BEACONS" \
        --worker-cap "$WORKER_CAP" \
        --duration "$DURATION_SECONDS" \
        --tx-rate "$TX_RATE" \
        --status "$status" \
        --exit-code "$exit_code" \
        --run-id "$run_id"

    if [[ "$exit_code" -ne 0 && "$STOP_ON_FAILURE" -eq 1 ]]; then
        log "stopping after first failed pressure level; use --continue-on-failure to continue"
        break
    fi
done

log "validator-pressure sweep completed summary=$SUMMARY_CSV"
