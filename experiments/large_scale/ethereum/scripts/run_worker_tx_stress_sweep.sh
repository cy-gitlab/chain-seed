#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_worker_tx_stress_sweep.sh requires bash." >&2
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
TX_ACCOUNT_COUNT="${TX_ACCOUNT_COUNT:-200}"
TX_MIX_PROFILE="${TX_MIX_PROFILE:-$ETHEREUM_DIR/configs/tx_mix_worker_v1.json}"
TX_LOAD_PHASES="${TX_LOAD_PHASES:-baseline:1:7200,load25:25:2700,recovery25:1:1800,load50:50:2700,recovery50:1:1800,load75:75:2700,recovery75:1:1800,load100:100:2700,recovery100:1:12600}"
TX_RANDOM_SEED="${TX_RANDOM_SEED:-20260725}"
TX_RECEIPT_INTERVAL="${TX_RECEIPT_INTERVAL:-2}"
TX_DRAIN_TIMEOUT="${TX_DRAIN_TIMEOUT:-600}"
TX_DRAIN_INTERVAL="${TX_DRAIN_INTERVAL:-6}"
BLOCK_EVENT_BEACON_SAMPLE="${BLOCK_EVENT_BEACON_SAMPLE:-64}"
PROCESSOR_METRICS_BEACON_SAMPLE="${PROCESSOR_METRICS_BEACON_SAMPLE:-32}"
PROCESSOR_METRICS_INTERVAL="${PROCESSOR_METRICS_INTERVAL:-2}"
GETH_CACHE_MB="${GETH_CACHE_MB:-128}"
GETH_GO_MEM_LIMIT_MB="${GETH_GO_MEM_LIMIT_MB:-256}"
GETH_MEMORY_MAX_MB="${GETH_MEMORY_MAX_MB:-512}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-1}"
LIGHTHOUSE_OFFICIAL_IMAGE="${LIGHTHOUSE_OFFICIAL_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
LIGHTHOUSE_COMPONENT_IMAGE="${LIGHTHOUSE_COMPONENT_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-baseline-components}"
BUILD_FORK_IMAGE="${BUILD_FORK_IMAGE:-0}"
SWEEP_ID="${SWEEP_ID:-$(date +%Y%m%d-%H%M%S)-worker-tx-stress-300}"
SWEEP_ROOT="${SWEEP_ROOT:-$ETHEREUM_DIR/experiments/worker-tx-stress}"
SWEEP_DIR="$SWEEP_ROOT/$SWEEP_ID"
RUNS_DIR="$SWEEP_DIR/runs"
RUNS_CSV="$SWEEP_DIR/runs.csv"
SUMMARY_CSV="$SWEEP_DIR/tx_stress_phase_summary.csv"
TYPE_SUMMARY_CSV="$SWEEP_DIR/tx_type_phase_summary.csv"
PROCESSOR_SUMMARY_CSV="$SWEEP_DIR/processor_tx_phase_summary.csv"
LOG_FILE="$SWEEP_DIR/sweep.log"
DRY_RUN=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Run phased mixed-transaction stress experiments serially across worker caps.

Defaults:
  worker caps: "$WORKER_CAPS"
  Beacon/Geth nodes: $BEACONS/$BEACONS
  Validator clients: $((BEACONS * 3))
  Geth cache: ${GETH_CACHE_MB} MiB per node
  Geth Go heap target: ${GETH_GO_MEM_LIMIT_MB} MiB per node
  Geth cgroup memory limit: ${GETH_MEMORY_MAX_MB} MiB per node
  transaction accounts: $TX_ACCOUNT_COUNT
  phases: $TX_LOAD_PHASES

Options:
  --worker-caps LIST       Space-separated caps, for example "2" or "2 4 8".
  --beacons N              Beacon and Geth node count.
  --stub-as-count N        Stub AS count.
  --accounts N             Prefunded sender account count.
  --geth-cache-mb N        Geth cache per node in MiB; 0 keeps the Geth default.
  --geth-go-mem-limit-mb N Go heap target per node; 0 disables GOMEMLIMIT.
  --geth-memory-max-mb N   Geth cgroup memory/swap limit; 0 means unlimited.
  --profile PATH           Mixed transaction profile JSON.
  --phases SCHEDULE        Comma-separated NAME:TPS:DURATION_SEC phases.
  --build-fork-image       Build the reusable Lighthouse component image once.
  --continue-on-failure    Continue to the next worker cap after failure.
  --dry-run                Validate and print the experiment without running Docker.
  -h, --help               Show this help.

The default phase schedule is exactly 36,000 seconds (10 hours) per worker cap.
Every cap is a fresh, serial run and containers are removed after each run.
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
        --accounts)
            TX_ACCOUNT_COUNT="$2"
            shift 2
            ;;
        --geth-cache-mb)
            GETH_CACHE_MB="$2"
            shift 2
            ;;
        --geth-go-mem-limit-mb)
            GETH_GO_MEM_LIMIT_MB="$2"
            shift 2
            ;;
        --geth-memory-max-mb)
            GETH_MEMORY_MAX_MB="$2"
            shift 2
            ;;
        --profile)
            TX_MIX_PROFILE="$2"
            shift 2
            ;;
        --phases)
            TX_LOAD_PHASES="$2"
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

for value_name in BEACONS STUB_AS_COUNT TX_ACCOUNT_COUNT TX_RANDOM_SEED TX_RECEIPT_INTERVAL TX_DRAIN_TIMEOUT TX_DRAIN_INTERVAL BLOCK_EVENT_BEACON_SAMPLE PROCESSOR_METRICS_BEACON_SAMPLE PROCESSOR_METRICS_INTERVAL GETH_CACHE_MB GETH_GO_MEM_LIMIT_MB GETH_MEMORY_MAX_MB STOP_ON_FAILURE BUILD_FORK_IMAGE; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done
if (( GETH_CACHE_MB != 0 && GETH_CACHE_MB < 32 )); then
    echo "GETH_CACHE_MB must be 0 or at least 32." >&2
    exit 2
fi
if (( GETH_GO_MEM_LIMIT_MB != 0 && GETH_GO_MEM_LIMIT_MB < 64 )); then
    echo "GETH_GO_MEM_LIMIT_MB must be 0 or at least 64." >&2
    exit 2
fi
if (( GETH_MEMORY_MAX_MB != 0 && GETH_MEMORY_MAX_MB < 128 )); then
    echo "GETH_MEMORY_MAX_MB must be 0 or at least 128." >&2
    exit 2
fi
if (( GETH_GO_MEM_LIMIT_MB != 0 && GETH_MEMORY_MAX_MB != 0 \
    && GETH_GO_MEM_LIMIT_MB >= GETH_MEMORY_MAX_MB )); then
    echo "GETH_GO_MEM_LIMIT_MB must be smaller than GETH_MEMORY_MAX_MB." >&2
    exit 2
fi
if (( BEACONS < 2 || STUB_AS_COUNT < 1 || TX_ACCOUNT_COUNT < 2 )); then
    echo "BEACONS must be at least 2, STUB_AS_COUNT positive, and accounts at least 2." >&2
    exit 2
fi
if (( BLOCK_EVENT_BEACON_SAMPLE > BEACONS || PROCESSOR_METRICS_BEACON_SAMPLE > BEACONS )); then
    echo "event and processor metric samples cannot exceed BEACONS." >&2
    exit 2
fi
if [[ ! -f "$TX_MIX_PROFILE" ]]; then
    echo "missing transaction profile: $TX_MIX_PROFILE" >&2
    exit 2
fi

phase_duration=0
IFS=',' read -r -a PHASE_ARRAY <<< "$TX_LOAD_PHASES"
declare -A PHASE_NAMES=()
for phase in "${PHASE_ARRAY[@]}"; do
    IFS=':' read -r phase_name phase_rate phase_seconds extra <<< "$phase"
    if [[ -n "${extra:-}" || ! "$phase_name" =~ ^[A-Za-z0-9_.-]+$ \
        || ! "$phase_rate" =~ ^[0-9]+([.][0-9]+)?$ \
        || ! "$phase_seconds" =~ ^[0-9]+$ || "$phase_seconds" -lt 1 ]]; then
        echo "invalid transaction phase: $phase" >&2
        exit 2
    fi
    if [[ -n "${PHASE_NAMES[$phase_name]:-}" ]]; then
        echo "duplicate transaction phase name: $phase_name" >&2
        exit 2
    fi
    PHASE_NAMES[$phase_name]=1
    phase_duration=$((phase_duration + phase_seconds))
done
DURATION_SECONDS="$phase_duration"

"$PYTHON_BIN" "$SCRIPT_DIR/generate_mixed_tx_load.py" \
    --compose-file "$ETHEREUM_DIR/output/docker-compose.yml" \
    --accounts "$TX_ACCOUNT_COUNT" \
    --profile "$TX_MIX_PROFILE" \
    --phases "$TX_LOAD_PHASES" \
    --setup-file /tmp/seedemu-worker-tx-stress-setup.json \
    --seed "$TX_RANDOM_SEED" \
    --dry-run \
    > /dev/null

log() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '%s %s\n' "$(date '+%F %T')" "$*"
    else
        printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
    fi
}

refresh_combined_csv() {
    local output="$1"
    local name="$2"
    local first=1
    local file
    : > "$output"
    while IFS= read -r file; do
        if [[ "$first" -eq 1 ]]; then
            cat "$file" >> "$output"
            first=0
        else
            tail -n +2 "$file" >> "$output"
        fi
    done < <(find "$RUNS_DIR" -path "*/fork_processor_workers_only/$name" -type f | sort)
}

if [[ "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$RUNS_DIR"
    if [[ ! -f "$RUNS_CSV" ]]; then
        printf 'worker_cap,geth_cache_mb,geth_go_mem_limit_mb,geth_memory_max_mb,status,exit_code,run_id,data_dir\n' > "$RUNS_CSV"
    fi
fi

read -r -a CAP_ARRAY <<< "$WORKER_CAPS"
if [[ "${#CAP_ARRAY[@]}" -eq 0 ]]; then
    echo "WORKER_CAPS is empty." >&2
    exit 2
fi

log "worker tx-stress sweep started"
log "sweep_dir=$SWEEP_DIR caps=[$WORKER_CAPS] duration=${DURATION_SECONDS}s accounts=$TX_ACCOUNT_COUNT geth_cache_mb=$GETH_CACHE_MB geth_go_mem_limit_mb=$GETH_GO_MEM_LIMIT_MB geth_memory_max_mb=$GETH_MEMORY_MAX_MB"
log "phases=$TX_LOAD_PHASES"

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
    run_id="b${BEACONS}-workers${worker_cap}-gethcache${GETH_CACHE_MB}-gethgomem${GETH_GO_MEM_LIMIT_MB}-gethmax${GETH_MEMORY_MAX_MB}-tx-stress"
    data_dir="$RUNS_DIR/$run_id/fork_processor_workers_only"
    log "starting worker_cap=$worker_cap run_id=$run_id"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        continue
    fi
    if [[ -d "$data_dir" ]]; then
        echo "run data already exists: $data_dir" >&2
        echo "reuse SWEEP_ID only for worker caps that have not been run." >&2
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
        GETH_CACHE_MB="$GETH_CACHE_MB" \
        GETH_GO_MEM_LIMIT_MB="$GETH_GO_MEM_LIMIT_MB" \
        GETH_MEMORY_MAX_MB="$GETH_MEMORY_MAX_MB" \
        LIGHTHOUSE_METRICS_ENABLED=1 \
        PROCESSOR_METRICS_ENABLED=1 \
        PROCESSOR_METRICS_BEACON_SAMPLE="$PROCESSOR_METRICS_BEACON_SAMPLE" \
        PROCESSOR_METRICS_INTERVAL="$PROCESSOR_METRICS_INTERVAL" \
        API_LOAD_ENABLED=0 \
        BLOCK_EVENT_ENABLED=1 \
        BLOCK_EVENT_BEACON_SAMPLE="$BLOCK_EVENT_BEACON_SAMPLE" \
        TX_LOAD_MODE=mixed \
        TX_MIX_PROFILE="$TX_MIX_PROFILE" \
        TX_LOAD_PHASES="$TX_LOAD_PHASES" \
        TX_ACCOUNT_COUNT="$TX_ACCOUNT_COUNT" \
        TX_RANDOM_SEED="$TX_RANDOM_SEED" \
        TX_RATE=1 \
        TX_MAX_CONSECUTIVE_FAILURES=0 \
        TX_RECEIPT_MODE=blocks \
        TX_RECEIPT_INTERVAL="$TX_RECEIPT_INTERVAL" \
        TX_DRAIN_TIMEOUT="$TX_DRAIN_TIMEOUT" \
        TX_DRAIN_INTERVAL="$TX_DRAIN_INTERVAL" \
        DURATION_SECONDS="$DURATION_SECONDS" \
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
    printf '%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "$worker_cap" "$GETH_CACHE_MB" "$GETH_GO_MEM_LIMIT_MB" \
        "$GETH_MEMORY_MAX_MB" "$status" "$exit_code" "$run_id" "$data_dir" \
        >> "$RUNS_CSV"
    refresh_combined_csv "$SUMMARY_CSV" "tx_stress_phase_summary.csv"
    refresh_combined_csv "$TYPE_SUMMARY_CSV" "tx_type_phase_summary.csv"
    refresh_combined_csv "$PROCESSOR_SUMMARY_CSV" "processor_tx_phase_summary.csv"
    log "finished worker_cap=$worker_cap status=$status exit_code=$exit_code"
    if [[ "$exit_code" -ne 0 && "$STOP_ON_FAILURE" -eq 1 ]]; then
        log "stopping after first failure; use --continue-on-failure to continue"
        break
    fi
done

log "worker tx-stress sweep completed summary=$SUMMARY_CSV"
