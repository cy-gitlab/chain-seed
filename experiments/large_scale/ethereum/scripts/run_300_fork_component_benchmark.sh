#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_300_fork_component_benchmark.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_DIR="$ETHEREUM_DIR/src"
OUTPUT_DIR="$ETHEREUM_DIR/output"
COMPOSE_FILE="$OUTPUT_DIR/docker-compose.yml"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

BEACONS="${BEACONS:-300}"
VALIDATOR_KEYS_PER_CLIENT="${VALIDATOR_KEYS_PER_CLIENT:-1}"
STUB_AS_COUNT="${STUB_AS_COUNT:-64}"
PLATFORM="${PLATFORM:-amd}"
ETHER_VIEW="${ETHER_VIEW:-1}"
INTERNET_MAP="${INTERNET_MAP:-1}"
INCLUDE_TOOLS="${INCLUDE_TOOLS:-0}"
VARIANTS="${VARIANTS:-baseline fork_cache_only fork_light_client_only fork_processor_only fork_rayon_only fork_quic_only fork_all}"
DURATION_SECONDS="${DURATION_SECONDS:-36000}"
MEMORY_INTERVAL="${MEMORY_INTERVAL:-60}"
BLOCK_INTERVAL="${BLOCK_INTERVAL:-12}"
BLOCK_MAX_CONSECUTIVE_ERRORS="${BLOCK_MAX_CONSECUTIVE_ERRORS:-5}"
CONSENSUS_INTERVAL="${CONSENSUS_INTERVAL:-2}"
CONSENSUS_BEACON_SAMPLE="${CONSENSUS_BEACON_SAMPLE:-32}"
VALIDATOR_LIVENESS_TIMEOUT="${VALIDATOR_LIVENESS_TIMEOUT:-10}"
VALIDATOR_LIVENESS_BATCH_SIZE="${VALIDATOR_LIVENESS_BATCH_SIZE:-1024}"
REORG_DEPTH="${REORG_DEPTH:-64}"
REORG_SCAN_INTERVAL="${REORG_SCAN_INTERVAL:-12}"
TX_RATE="${TX_RATE:-1}"
TX_LOAD_MODE="${TX_LOAD_MODE:-simple}"
TX_MIX_PROFILE="${TX_MIX_PROFILE:-$ETHEREUM_DIR/configs/tx_mix_worker_v1.json}"
TX_LOAD_PHASES="${TX_LOAD_PHASES:-baseline:1:3600}"
TX_ACCOUNT_COUNT="${TX_ACCOUNT_COUNT:-20}"
TX_RANDOM_SEED="${TX_RANDOM_SEED:-20260725}"
TX_SCHEDULE_GRACE_SECONDS="${TX_SCHEDULE_GRACE_SECONDS:-1800}"
TX_MAX_CONSECUTIVE_FAILURES="${TX_MAX_CONSECUTIVE_FAILURES:-10}"
TX_RECEIPT_INTERVAL="${TX_RECEIPT_INTERVAL:-12}"
TX_RECEIPT_MODE="${TX_RECEIPT_MODE:-receipts}"
TX_DRAIN_TIMEOUT="${TX_DRAIN_TIMEOUT:-120}"
TX_DRAIN_INTERVAL="${TX_DRAIN_INTERVAL:-12}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-5400}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-60}"
BUILD_MODE="${BUILD_MODE:-nobuild-mount}"
BUILD_KIT="${DOCKER_BUILDKIT:-0}"
BUILD_BATCH_SIZE="${BUILD_BATCH_SIZE:-75}"
BUILD_JOBS="${BUILD_JOBS:-4}"
BUILD_RETRY="${BUILD_RETRY:-1}"
UP_PRESET="${UP_PRESET:-fast}"
MEMORY_BACKEND="${MEMORY_BACKEND:-cgroup-scan}"
CONTAINER_NOFILE_LIMIT="${CONTAINER_NOFILE_LIMIT:-524288}"
GETH_CACHE_MB="${GETH_CACHE_MB:-0}"
GETH_GO_MEM_LIMIT_MB="${GETH_GO_MEM_LIMIT_MB:-0}"
GETH_MEMORY_MAX_MB="${GETH_MEMORY_MAX_MB:-0}"
HOST_MIN_INOTIFY_INSTANCES="${HOST_MIN_INOTIFY_INSTANCES:-8192}"
HOST_MEMORY_GUARD_ENABLED="${HOST_MEMORY_GUARD_ENABLED:-1}"
HOST_MIN_AVAILABLE_GIB="${HOST_MIN_AVAILABLE_GIB:-96}"
HOST_MAX_SWAP_USED_GIB="${HOST_MAX_SWAP_USED_GIB:-1}"
PROCESS_STOP_TIMEOUT="${PROCESS_STOP_TIMEOUT:-30}"
COMPOSE_DOWN_TIMEOUT="${COMPOSE_DOWN_TIMEOUT:-600}"
LIGHTHOUSE_OFFICIAL_IMAGE="${LIGHTHOUSE_OFFICIAL_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
LIGHTHOUSE_COMPONENT_IMAGE="${LIGHTHOUSE_COMPONENT_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-baseline-components}"
LIGHTHOUSE_RAYON_THREADS="${LIGHTHOUSE_RAYON_THREADS:-2}"
LIGHTHOUSE_PROCESSOR_WORKERS_CAP="${LIGHTHOUSE_PROCESSOR_WORKERS_CAP:-2}"
LIGHTHOUSE_METRICS_ENABLED="${LIGHTHOUSE_METRICS_ENABLED:-0}"
LIGHTHOUSE_METRICS_PORT="${LIGHTHOUSE_METRICS_PORT:-5054}"
API_LOAD_ENABLED="${API_LOAD_ENABLED:-0}"
API_LOAD_PROFILE="${API_LOAD_PROFILE:-$ETHEREUM_DIR/configs/api_observer_read_v1.json}"
API_LOAD_PHASES="${API_LOAD_PHASES:-baseline:0:1800,load_1x:0.2:1800,recovery_1x:0:600,load_5x:1:1800,recovery_5x:0:600,load_10x:2:1800,recovery_10x:0:600,load_20x:4:1800}"
API_TARGET_COUNT="${API_TARGET_COUNT:-30}"
API_CONTROL_COUNT="${API_CONTROL_COUNT:-30}"
API_MONITOR_PER_ROLE="${API_MONITOR_PER_ROLE:-8}"
API_CONCURRENCY_PER_NODE="${API_CONCURRENCY_PER_NODE:-4}"
API_REQUEST_TIMEOUT="${API_REQUEST_TIMEOUT:-5}"
PROCESSOR_METRICS_ENABLED="${PROCESSOR_METRICS_ENABLED:-$API_LOAD_ENABLED}"
PROCESSOR_METRICS_BEACON_SAMPLE="${PROCESSOR_METRICS_BEACON_SAMPLE:-32}"
PROCESSOR_METRICS_INTERVAL="${PROCESSOR_METRICS_INTERVAL:-2}"
PROCESSOR_METRICS_TIMEOUT="${PROCESSOR_METRICS_TIMEOUT:-1.5}"
BLOCK_EVENT_ENABLED="${BLOCK_EVENT_ENABLED:-0}"
BLOCK_EVENT_BEACON_SAMPLE="${BLOCK_EVENT_BEACON_SAMPLE:-64}"
BLOCK_EVENT_STARTUP_TIMEOUT="${BLOCK_EVENT_STARTUP_TIMEOUT:-120}"
BLOCK_EVENT_READ_TIMEOUT="${BLOCK_EVENT_READ_TIMEOUT:-45}"
BLOCK_EVENT_RECONNECT_DELAY="${BLOCK_EVENT_RECONNECT_DELAY:-1}"
BLOCK_EVENT_MIN_READY_RATIO="${BLOCK_EVENT_MIN_READY_RATIO:-0.95}"
BLOCK_EVENT_MIN_COVERAGE_RATIO="${BLOCK_EVENT_MIN_COVERAGE_RATIO:-0.95}"
BLOCK_EVENT_WARMUP_SECONDS="${BLOCK_EVENT_WARMUP_SECONDS:-300}"
BLOCK_EVENT_TAIL_SECONDS="${BLOCK_EVENT_TAIL_SECONDS:-24}"
EXECUTION_RPC_PREFLIGHT_RETRIES="${EXECUTION_RPC_PREFLIGHT_RETRIES:-6}"
NODE_PROCESS_PREFLIGHT_RETRIES="${NODE_PROCESS_PREFLIGHT_RETRIES:-6}"
BUILD_FORK_IMAGE="${BUILD_FORK_IMAGE:-0}"
SKIP_BUILD="${SKIP_BUILD:-0}"
KEEP_LAST="${KEEP_LAST:-0}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)-b${BEACONS}-fork-components}"
RUN_ROOT="${RUN_ROOT:-$ETHEREUM_DIR/experiments/fork-component-benchmark}"
RUN_DIR="$RUN_ROOT/$RUN_ID"
DRY_RUN=0
LIGHTHOUSE_COMPONENT_IMAGE_ID=""
LIGHTHOUSE_COMPONENT_PATCH_SHA=""

CURRENT_MEMORY_PID=""
CURRENT_BLOCK_PID=""
CURRENT_CONSENSUS_PID=""
CURRENT_TX_PID=""
CURRENT_TX_RECEIPT_PID=""
CURRENT_API_LOAD_PID=""
CURRENT_PROCESSOR_METRICS_PID=""
CURRENT_BLOCK_EVENT_PID=""
COMPOSE_DOWN_ON_EXIT=1
HOST_OOM_KILL_BASELINE=""

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Run 300-beacon Lighthouse fork component benchmark experiments.

Default variants:
  baseline
  fork_cache_only
  fork_light_client_only
  fork_processor_only
  fork_processor_workers_only
  fork_processor_queues_only
  fork_processor_gossip_only
  fork_rayon_only
  fork_quic_only
  fork_all

All variants use:
  beacon nodes = $BEACONS
  geth nodes = beacon nodes
  validator clients = 3 * beacon nodes
  validator keys = validator clients * $VALIDATOR_KEYS_PER_CLIENT keys/client
  historic states = all
  cache profile = default
  beacon peers = default
  duration = $DURATION_SECONDS seconds

Options:
  --variants LIST             Space-separated variant list.
  --duration SEC              Sampling duration after health passes.
  --tx-rate RATE              Continuous transaction send rate.
  --tx-load-mode MODE         simple or mixed. Default: $TX_LOAD_MODE
  --processor-workers-cap N   Beacon processor worker cap for processor-worker fork runs.
  --validator-keys-per-client N
                              Genesis validator keys loaded by each Validator client.
  --geth-cache-mb N           Geth internal cache in MiB; 0 keeps the client default.
  --geth-go-mem-limit-mb N    Go heap target in MiB; 0 disables GOMEMLIMIT.
  --geth-memory-max-mb N      Per-Geth cgroup memory/swap limit; 0 means unlimited.
  --build-fork-image          Build the component-aware Lighthouse fork image first.
  --skip-build                Skip generated per-node docker compose build.
  --build-mode MODE           nobuild-mount or docker-build. Default: $BUILD_MODE
  --keep-last                 Leave the final variant running.
  --dry-run                   Print actions without running them.
  -h, --help                  Show this help.

Important environment variables:
  PYTHON_BIN, BEACONS, VALIDATOR_KEYS_PER_CLIENT, STUB_AS_COUNT, VARIANTS,
  RUN_ID, DURATION_SECONDS,
  TX_RATE, TX_LOAD_MODE, TX_MIX_PROFILE, TX_LOAD_PHASES, TX_ACCOUNT_COUNT,
  TX_RANDOM_SEED, TX_SCHEDULE_GRACE_SECONDS, TX_RECEIPT_MODE,
  TX_DRAIN_TIMEOUT, TX_DRAIN_INTERVAL,
  MEMORY_INTERVAL, CONTAINER_NOFILE_LIMIT, GETH_CACHE_MB,
  GETH_GO_MEM_LIMIT_MB, GETH_MEMORY_MAX_MB,
  HOST_MIN_INOTIFY_INSTANCES, HOST_MEMORY_GUARD_ENABLED,
  HOST_MIN_AVAILABLE_GIB, HOST_MAX_SWAP_USED_GIB,
  PROCESS_STOP_TIMEOUT, COMPOSE_DOWN_TIMEOUT,
  TX_MAX_CONSECUTIVE_FAILURES, BLOCK_MAX_CONSECUTIVE_ERRORS,
  CONSENSUS_INTERVAL, CONSENSUS_BEACON_SAMPLE, VALIDATOR_LIVENESS_TIMEOUT,
  VALIDATOR_LIVENESS_BATCH_SIZE,
  LIGHTHOUSE_COMPONENT_IMAGE, LIGHTHOUSE_RAYON_THREADS,
  LIGHTHOUSE_PROCESSOR_WORKERS_CAP, UP_PRESET,
  LIGHTHOUSE_METRICS_ENABLED, LIGHTHOUSE_METRICS_PORT,
  API_LOAD_ENABLED, API_LOAD_PROFILE, API_LOAD_PHASES,
  API_TARGET_COUNT, API_CONTROL_COUNT, API_MONITOR_PER_ROLE,
  API_CONCURRENCY_PER_NODE, API_REQUEST_TIMEOUT,
  PROCESSOR_METRICS_ENABLED, PROCESSOR_METRICS_BEACON_SAMPLE,
  PROCESSOR_METRICS_INTERVAL, PROCESSOR_METRICS_TIMEOUT,
  BLOCK_EVENT_ENABLED, BLOCK_EVENT_BEACON_SAMPLE,
  BLOCK_EVENT_STARTUP_TIMEOUT, BLOCK_EVENT_READ_TIMEOUT,
  BLOCK_EVENT_RECONNECT_DELAY, BLOCK_EVENT_MIN_READY_RATIO,
  BLOCK_EVENT_MIN_COVERAGE_RATIO, BLOCK_EVENT_WARMUP_SECONDS,
  BLOCK_EVENT_TAIL_SECONDS,
  BUILD_MODE, BUILD_FORK_IMAGE, SKIP_BUILD, KEEP_LAST
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --variants)
            VARIANTS="$2"
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
        --tx-load-mode)
            TX_LOAD_MODE="$2"
            shift 2
            ;;
        --processor-workers-cap)
            LIGHTHOUSE_PROCESSOR_WORKERS_CAP="$2"
            shift 2
            ;;
        --validator-keys-per-client)
            VALIDATOR_KEYS_PER_CLIENT="$2"
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
        --build-fork-image)
            BUILD_FORK_IMAGE=1
            shift
            ;;
        --skip-build)
            SKIP_BUILD=1
            shift
            ;;
        --build-mode)
            BUILD_MODE="$2"
            shift 2
            ;;
        --keep-last)
            KEEP_LAST=1
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

for value_name in BEACONS VALIDATOR_KEYS_PER_CLIENT STUB_AS_COUNT DURATION_SECONDS MEMORY_INTERVAL BLOCK_INTERVAL BLOCK_MAX_CONSECUTIVE_ERRORS CONSENSUS_BEACON_SAMPLE REORG_DEPTH REORG_SCAN_INTERVAL VALIDATOR_LIVENESS_TIMEOUT VALIDATOR_LIVENESS_BATCH_SIZE TX_ACCOUNT_COUNT TX_RANDOM_SEED TX_SCHEDULE_GRACE_SECONDS TX_RECEIPT_INTERVAL TX_DRAIN_TIMEOUT TX_DRAIN_INTERVAL TX_MAX_CONSECUTIVE_FAILURES HEALTH_TIMEOUT HEALTH_INTERVAL BUILD_BATCH_SIZE BUILD_JOBS BUILD_RETRY CONTAINER_NOFILE_LIMIT GETH_CACHE_MB GETH_GO_MEM_LIMIT_MB GETH_MEMORY_MAX_MB HOST_MIN_INOTIFY_INSTANCES HOST_MEMORY_GUARD_ENABLED HOST_MIN_AVAILABLE_GIB HOST_MAX_SWAP_USED_GIB PROCESS_STOP_TIMEOUT COMPOSE_DOWN_TIMEOUT LIGHTHOUSE_RAYON_THREADS LIGHTHOUSE_PROCESSOR_WORKERS_CAP LIGHTHOUSE_METRICS_ENABLED LIGHTHOUSE_METRICS_PORT API_LOAD_ENABLED API_TARGET_COUNT API_CONTROL_COUNT API_MONITOR_PER_ROLE API_CONCURRENCY_PER_NODE PROCESSOR_METRICS_ENABLED PROCESSOR_METRICS_BEACON_SAMPLE BLOCK_EVENT_ENABLED BLOCK_EVENT_BEACON_SAMPLE BLOCK_EVENT_STARTUP_TIMEOUT BLOCK_EVENT_WARMUP_SECONDS BLOCK_EVENT_TAIL_SECONDS EXECUTION_RPC_PREFLIGHT_RETRIES NODE_PROCESS_PREFLIGHT_RETRIES; do
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
if [[ ! "$TX_RATE" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "TX_RATE must be a non-negative number." >&2
    exit 2
fi
if (( LIGHTHOUSE_METRICS_PORT < 1 || LIGHTHOUSE_METRICS_PORT > 65535 )); then
    echo "LIGHTHOUSE_METRICS_PORT must be between 1 and 65535." >&2
    exit 2
fi
for value_name in API_REQUEST_TIMEOUT PROCESSOR_METRICS_INTERVAL PROCESSOR_METRICS_TIMEOUT BLOCK_EVENT_READ_TIMEOUT BLOCK_EVENT_RECONNECT_DELAY BLOCK_EVENT_MIN_READY_RATIO BLOCK_EVENT_MIN_COVERAGE_RATIO; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || [[ "$value" == "0" || "$value" == "0.0" ]]; then
        echo "$value_name must be a positive number." >&2
        exit 2
    fi
done
for value_name in HOST_MEMORY_GUARD_ENABLED LIGHTHOUSE_METRICS_ENABLED API_LOAD_ENABLED PROCESSOR_METRICS_ENABLED BLOCK_EVENT_ENABLED; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[01]$ ]]; then
        echo "$value_name must be 0 or 1." >&2
        exit 2
    fi
done
case "$TX_LOAD_MODE" in
    simple|mixed)
        ;;
    *)
        echo "TX_LOAD_MODE must be simple or mixed." >&2
        exit 2
        ;;
esac
case "$TX_RECEIPT_MODE" in
    receipts|blocks)
        ;;
    *)
        echo "TX_RECEIPT_MODE must be receipts or blocks." >&2
        exit 2
        ;;
esac
if (( TX_ACCOUNT_COUNT < 2 )); then
    echo "TX_ACCOUNT_COUNT must be at least 2." >&2
    exit 2
fi
if [[ "$TX_LOAD_MODE" == "mixed" && ! -f "$TX_MIX_PROFILE" ]]; then
    echo "missing TX_MIX_PROFILE: $TX_MIX_PROFILE" >&2
    exit 2
fi
if [[ "$PROCESSOR_METRICS_ENABLED" -eq 1 && "$LIGHTHOUSE_METRICS_ENABLED" -ne 1 ]]; then
    echo "PROCESSOR_METRICS_ENABLED=1 requires LIGHTHOUSE_METRICS_ENABLED=1" >&2
    exit 2
fi
if [[ "$API_LOAD_ENABLED" -eq 1 && "$PROCESSOR_METRICS_ENABLED" -ne 1 ]]; then
    echo "API_LOAD_ENABLED=1 requires PROCESSOR_METRICS_ENABLED=1" >&2
    exit 2
fi
if [[ "$BLOCK_EVENT_ENABLED" -eq 1 ]]; then
    if (( BLOCK_EVENT_BEACON_SAMPLE < 2 || BLOCK_EVENT_BEACON_SAMPLE > BEACONS )); then
        echo "BLOCK_EVENT_BEACON_SAMPLE must be between 2 and BEACONS." >&2
        exit 2
    fi
    if (( DURATION_SECONDS <= BLOCK_EVENT_WARMUP_SECONDS + BLOCK_EVENT_TAIL_SECONDS )); then
        echo "DURATION_SECONDS must exceed block-event warmup plus tail time." >&2
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
fi
for value_name in VALIDATOR_KEYS_PER_CLIENT VALIDATOR_LIVENESS_TIMEOUT VALIDATOR_LIVENESS_BATCH_SIZE LIGHTHOUSE_RAYON_THREADS LIGHTHOUSE_PROCESSOR_WORKERS_CAP; do
    value="${!value_name}"
    if (( value < 1 )); then
        echo "$value_name must be positive." >&2
        exit 2
    fi
done
if (( CONTAINER_NOFILE_LIMIT < 1024 || HOST_MIN_INOTIFY_INSTANCES < 1024 )); then
    echo "CONTAINER_NOFILE_LIMIT and HOST_MIN_INOTIFY_INSTANCES must be at least 1024." >&2
    exit 2
fi
for value_name in HOST_MIN_AVAILABLE_GIB PROCESS_STOP_TIMEOUT COMPOSE_DOWN_TIMEOUT; do
    value="${!value_name}"
    if (( value < 1 )); then
        echo "$value_name must be positive." >&2
        exit 2
    fi
done

VALIDATOR_CLIENTS=$((BEACONS * 3))
ACTIVE_VALIDATORS=$((VALIDATOR_CLIENTS * VALIDATOR_KEYS_PER_CLIENT))

case "$BUILD_MODE" in
    nobuild-mount|docker-build)
        ;;
    *)
        echo "BUILD_MODE must be nobuild-mount or docker-build." >&2
        exit 2
        ;;
esac

mkdir -p "$RUN_DIR"

log() {
    printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$RUN_DIR/run.log"
}

run_cmd() {
    log "+ $*"
    if [[ "$DRY_RUN" -eq 0 ]]; then
        "$@"
    fi
}

system_oom_kill_count() {
    local events="/sys/fs/cgroup/system.slice/memory.events"
    if [[ -r "$events" ]]; then
        awk '$1 == "oom_kill" { print $2; found=1 } END { if (!found) print 0 }' "$events"
    else
        echo ""
    fi
}

host_memory_values() {
    awk '
        $1 == "MemAvailable:" { available=$2 }
        $1 == "SwapTotal:" { swap_total=$2 }
        $1 == "SwapFree:" { swap_free=$2 }
        END {
            printf "%d %d %d\n", available, swap_total, swap_total - swap_free
        }
    ' /proc/meminfo
}

verify_host_prerequisites() {
    local inotify_instances
    local available_kib swap_total_kib swap_used_kib
    local minimum_kib=$((HOST_MIN_AVAILABLE_GIB * 1024 * 1024))
    local maximum_swap_kib=$((HOST_MAX_SWAP_USED_GIB * 1024 * 1024))

    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi

    inotify_instances="$(sysctl -n fs.inotify.max_user_instances)"
    if (( inotify_instances < HOST_MIN_INOTIFY_INSTANCES )); then
        cat >&2 <<EOF
fs.inotify.max_user_instances=$inotify_instances is too small for this experiment.
Run once before the experiment:
  $SCRIPT_DIR/configure_large_scale_host.sh
EOF
        return 1
    fi

    read -r available_kib swap_total_kib swap_used_kib < <(host_memory_values)
    if (( available_kib < minimum_kib )); then
        echo "host has less than ${HOST_MIN_AVAILABLE_GIB} GiB MemAvailable" >&2
        return 1
    fi
    if (( swap_used_kib > maximum_swap_kib )); then
        cat >&2 <<EOF
host already uses more than ${HOST_MAX_SWAP_USED_GIB} GiB swap.
Reset stale swap before a reproducible run:
  sudo swapoff -a
  sudo swapon -a
EOF
        return 1
    fi

    HOST_OOM_KILL_BASELINE="$(system_oom_kill_count)"
    log "host preflight passed inotify_instances=$inotify_instances mem_available_gib=$((available_kib / 1024 / 1024)) swap_used_gib=$((swap_used_kib / 1024 / 1024)) system_oom_kill=${HOST_OOM_KILL_BASELINE:-unavailable}"
}

initialize_host_resource_log() {
    local data_dir="$1"
    printf '%s\n' \
        "timestamp,elapsed_sec,mem_available_kib,swap_total_kib,swap_used_kib,system_oom_kill" \
        > "$data_dir/host_resources.csv"
}

sample_host_resources() {
    local data_dir="$1"
    local available_kib swap_total_kib swap_used_kib oom_kills
    local minimum_kib=$((HOST_MIN_AVAILABLE_GIB * 1024 * 1024))

    read -r available_kib swap_total_kib swap_used_kib < <(host_memory_values)
    oom_kills="$(system_oom_kill_count)"
    printf '%s,%s,%s,%s,%s,%s\n' \
        "$(date -Is)" "$SECONDS" "$available_kib" "$swap_total_kib" \
        "$swap_used_kib" "${oom_kills:-}" \
        >> "$data_dir/host_resources.csv"

    if [[ -n "$HOST_OOM_KILL_BASELINE" && -n "$oom_kills" ]] && \
        (( oom_kills > HOST_OOM_KILL_BASELINE )); then
        log "host memory guard detected a new system.slice OOM kill"
        return 76
    fi
    if [[ "$HOST_MEMORY_GUARD_ENABLED" -eq 1 && "$available_kib" -lt "$minimum_kib" ]]; then
        log "host memory guard stopping run: mem_available_gib=$((available_kib / 1024 / 1024)) threshold_gib=$HOST_MIN_AVAILABLE_GIB"
        return 75
    fi
}

stop_pid() {
    local pid="$1"
    local deadline
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        pkill -TERM -P "$pid" 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
        deadline=$((SECONDS + PROCESS_STOP_TIMEOUT))
        while kill -0 "$pid" 2>/dev/null && (( SECONDS < deadline )); do
            pkill -TERM -P "$pid" 2>/dev/null || true
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            pkill -KILL -P "$pid" 2>/dev/null || true
            kill -KILL "$pid" 2>/dev/null || true
        fi
        wait "$pid" 2>/dev/null || true
    fi
}

cleanup_bg() {
    stop_pid "$CURRENT_BLOCK_EVENT_PID"
    CURRENT_BLOCK_EVENT_PID=""
    stop_pid "$CURRENT_API_LOAD_PID"
    CURRENT_API_LOAD_PID=""
    stop_pid "$CURRENT_PROCESSOR_METRICS_PID"
    CURRENT_PROCESSOR_METRICS_PID=""
    stop_pid "$CURRENT_TX_PID"
    CURRENT_TX_PID=""
    stop_pid "$CURRENT_TX_RECEIPT_PID"
    CURRENT_TX_RECEIPT_PID=""
    stop_pid "$CURRENT_CONSENSUS_PID"
    CURRENT_CONSENSUS_PID=""
    stop_pid "$CURRENT_BLOCK_PID"
    CURRENT_BLOCK_PID=""
    stop_pid "$CURRENT_MEMORY_PID"
    CURRENT_MEMORY_PID=""
}

compose_down() {
    if [[ -f "$COMPOSE_FILE" ]]; then
        log "+ docker compose -f $COMPOSE_FILE down -v --remove-orphans"
        if [[ "$DRY_RUN" -eq 1 ]]; then
            return 0
        fi
        if ! timeout "$COMPOSE_DOWN_TIMEOUT" \
            docker compose -f "$COMPOSE_FILE" down -v --remove-orphans; then
            log "compose down timed out or failed; forcing project containers to stop"
            timeout 120 docker compose -f "$COMPOSE_FILE" kill || true
            timeout "$COMPOSE_DOWN_TIMEOUT" \
                docker compose -f "$COMPOSE_FILE" down -v --remove-orphans
        fi
    fi
}

on_exit() {
    local status=$?
    cleanup_bg || true
    if [[ "$COMPOSE_DOWN_ON_EXIT" -eq 1 ]]; then
        compose_down || true
    fi
    exit "$status"
}

on_signal() {
    local status="$1"
    cleanup_bg || true
    compose_down || true
    COMPOSE_DOWN_ON_EXIT=0
    exit "$status"
}

trap on_exit EXIT
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 131' QUIT

variant_components() {
    local variant="$1"
    CFG_PROFILE="official"
    CFG_IMAGE="$LIGHTHOUSE_OFFICIAL_IMAGE"
    CFG_COMPONENTS=""
    CFG_RAYON_THREADS="$LIGHTHOUSE_RAYON_THREADS"

    case "$variant" in
        baseline)
            ;;
        fork_cache_only)
            CFG_PROFILE="seedemu-lite"
            CFG_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE"
            CFG_COMPONENTS="cache"
            ;;
        fork_light_client_only)
            CFG_PROFILE="seedemu-lite"
            CFG_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE"
            CFG_COMPONENTS="light-client"
            ;;
        fork_processor_only)
            CFG_PROFILE="seedemu-lite"
            CFG_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE"
            CFG_COMPONENTS="processor"
            ;;
        fork_processor_workers_only)
            CFG_PROFILE="seedemu-lite"
            CFG_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE"
            CFG_COMPONENTS="processor-workers"
            ;;
        fork_processor_queues_only)
            CFG_PROFILE="seedemu-lite"
            CFG_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE"
            CFG_COMPONENTS="processor-queues"
            ;;
        fork_processor_gossip_only)
            CFG_PROFILE="seedemu-lite"
            CFG_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE"
            CFG_COMPONENTS="processor-gossip"
            ;;
        fork_rayon_only)
            CFG_PROFILE="seedemu-lite"
            CFG_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE"
            CFG_COMPONENTS="rayon"
            ;;
        fork_quic_only)
            CFG_PROFILE="seedemu-lite"
            CFG_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE"
            CFG_COMPONENTS="network"
            ;;
        fork_all)
            CFG_PROFILE="seedemu-lite"
            CFG_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE"
            CFG_COMPONENTS="all"
            ;;
        *)
            echo "invalid variant: $variant" >&2
            exit 2
            ;;
    esac
}

variant_needs_fork() {
    local variant
    for variant in "$@"; do
        [[ "$variant" == fork_* ]] && return 0
    done
    return 1
}

check_or_build_images() {
    local variants=("$@")
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    docker image inspect "$LIGHTHOUSE_OFFICIAL_IMAGE" >/dev/null
    if variant_needs_fork "${variants[@]}"; then
        if [[ "$BUILD_FORK_IMAGE" -eq 1 ]]; then
            run_cmd env LIGHTHOUSE_BASE_IMAGE="$LIGHTHOUSE_OFFICIAL_IMAGE" LIGHTHOUSE_FORK_IMAGE="$LIGHTHOUSE_COMPONENT_IMAGE" "$SCRIPT_DIR/build_lighthouse_component_fork.sh"
        else
            docker image inspect "$LIGHTHOUSE_COMPONENT_IMAGE" >/dev/null || {
                echo "missing component fork image: $LIGHTHOUSE_COMPONENT_IMAGE" >&2
                echo "build it first with: LIGHTHOUSE_BASE_IMAGE=$LIGHTHOUSE_OFFICIAL_IMAGE LIGHTHOUSE_FORK_IMAGE=$LIGHTHOUSE_COMPONENT_IMAGE $SCRIPT_DIR/build_lighthouse_component_fork.sh" >&2
                exit 1
            }
        fi
        "$SCRIPT_DIR/verify_lighthouse_component_image.sh" "$LIGHTHOUSE_COMPONENT_IMAGE"
        LIGHTHOUSE_COMPONENT_IMAGE_ID="$(docker image inspect "$LIGHTHOUSE_COMPONENT_IMAGE" --format '{{.Id}}')"
        LIGHTHOUSE_COMPONENT_PATCH_SHA="$(docker image inspect "$LIGHTHOUSE_COMPONENT_IMAGE" --format '{{index .Config.Labels "org.seedemu.lighthouse.component_patch_sha256"}}')"
    fi
}

wait_for_health() {
    local data_dir="$1"
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    local latest_log="$data_dir/health-latest.log"

    while (( SECONDS < deadline )); do
        log "running check_pos_health.sh"
        if [[ "$DRY_RUN" -eq 1 ]]; then
            return 0
        fi
        if env EXPECTED_ACTIVE_VALIDATORS="$ACTIVE_VALIDATORS" \
            "$SCRIPT_DIR/check_pos_health.sh" > "$latest_log" 2>&1; then
            cp "$latest_log" "$data_dir/health-passed.log"
            log "health check passed"
            return 0
        fi
        tail -n 20 "$latest_log" || true
        log "health check not ready; sleeping ${HEALTH_INTERVAL}s"
        sleep "$HEALTH_INTERVAL"
    done

    cat "$latest_log" >&2 || true
    echo "health check did not pass within ${HEALTH_TIMEOUT}s" >&2
    return 1
}

generate_output() {
    local data_dir="$1"
    local cmd=(
        "$PYTHON_BIN" "$SRC_DIR/ethereum_pos.py"
        --platform "$PLATFORM"
        --beacons "$BEACONS"
        --validator-keys-per-client "$VALIDATOR_KEYS_PER_CLIENT"
        --local-account-count "$TX_ACCOUNT_COUNT"
        --stub-as-count "$STUB_AS_COUNT"
        --output "$OUTPUT_DIR"
        --beacon-historic-states all
        --lighthouse-cache-profile default
    )

    if [[ "$ETHER_VIEW" -eq 1 ]]; then
        cmd+=(--ether-view)
    fi
    if [[ "$INCLUDE_TOOLS" -eq 0 ]]; then
        cmd+=(--no-tools)
    fi
    if [[ "$INTERNET_MAP" -eq 0 ]]; then
        cmd+=(--no-internet-map)
    fi
    if [[ "$LIGHTHOUSE_METRICS_ENABLED" -eq 1 ]]; then
        cmd+=(--lighthouse-metrics --lighthouse-metrics-port "$LIGHTHOUSE_METRICS_PORT")
    fi

    run_cmd rm -rf "$OUTPUT_DIR"
    run_cmd "${cmd[@]}"

    if (( GETH_CACHE_MB > 0 || GETH_GO_MEM_LIMIT_MB > 0 || GETH_MEMORY_MAX_MB > 0 )); then
        local geth_memory_cmd=(
            "$PYTHON_BIN"
            "$SCRIPT_DIR/patch_output_geth_memory.py"
            --output "$OUTPUT_DIR"
            --expected-nodes "$BEACONS"
        )
        if (( GETH_CACHE_MB > 0 )); then
            geth_memory_cmd+=(--cache-mb "$GETH_CACHE_MB")
        fi
        if (( GETH_GO_MEM_LIMIT_MB > 0 )); then
            geth_memory_cmd+=(--go-mem-limit-mb "$GETH_GO_MEM_LIMIT_MB")
        fi
        if (( GETH_MEMORY_MAX_MB > 0 )); then
            geth_memory_cmd+=(--container-memory-mb "$GETH_MEMORY_MAX_MB")
        fi
        run_cmd "${geth_memory_cmd[@]}"
    fi

    if [[ "$CFG_PROFILE" != "official" ]]; then
        run_cmd "$PYTHON_BIN" "$SCRIPT_DIR/patch_output_lighthouse_fork.py" \
            --output "$OUTPUT_DIR" \
            --official-image "$LIGHTHOUSE_OFFICIAL_IMAGE" \
            --fork-image "$CFG_IMAGE" \
            --profile "$CFG_PROFILE" \
            --scope all \
            --components "$CFG_COMPONENTS" \
            --rayon-threads "$CFG_RAYON_THREADS" \
            --processor-workers-cap "$LIGHTHOUSE_PROCESSOR_WORKERS_CAP"
    fi

    if [[ "$BUILD_MODE" == "nobuild-mount" ]]; then
        run_cmd "$PYTHON_BIN" "$SCRIPT_DIR/patch_output_to_runtime_mounts.py" \
            --output "$OUTPUT_DIR" \
            --nofile-limit "$CONTAINER_NOFILE_LIMIT"
    fi

    if [[ "$DRY_RUN" -eq 0 ]]; then
        docker compose -f "$COMPOSE_FILE" config --services > "$data_dir/services.txt"
        cp "$COMPOSE_FILE" "$data_dir/docker-compose.yml"
        [[ -f "$OUTPUT_DIR/lighthouse-fork-summary.json" ]] && cp "$OUTPUT_DIR/lighthouse-fork-summary.json" "$data_dir/lighthouse-fork-summary.json"
        [[ -f "$OUTPUT_DIR/geth-memory-summary.json" ]] && cp "$OUTPUT_DIR/geth-memory-summary.json" "$data_dir/geth-memory-summary.json"
        [[ -f "$OUTPUT_DIR/nobuild-mount-summary.json" ]] && cp "$OUTPUT_DIR/nobuild-mount-summary.json" "$data_dir/nobuild-mount-summary.json"
        [[ -f "$OUTPUT_DIR/validator-pressure-summary.json" ]] && cp "$OUTPUT_DIR/validator-pressure-summary.json" "$data_dir/validator-pressure-summary.json"
    fi
}

verify_container_nofile() {
    local data_dir="$1"
    local role id pid soft hard
    local -a ids=()
    local output="$data_dir/container-limits.txt"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi

    : > "$output"
    for role in Geth Beacon Validator; do
        mapfile -t ids < <(
            docker ps \
                --filter "name=Ethereum-POS-$role-" \
                --format '{{.ID}}'
        )
        if [[ "${#ids[@]}" -eq 0 ]]; then
            echo "cannot find a running Ethereum-POS-$role container" >&2
            return 1
        fi
        id="${ids[0]}"
        pid="$(docker inspect --format '{{.State.Pid}}' "$id")"
        read -r soft hard < <(
            awk '$1 == "Max" && $2 == "open" && $3 == "files" { print $4, $5 }' \
                "/proc/$pid/limits"
        )
        printf '%s container=%s pid=%s soft=%s hard=%s\n' \
            "$role" "$id" "$pid" "$soft" "$hard" | tee -a "$output"
        if [[ ! "$soft" =~ ^[0-9]+$ || ! "$hard" =~ ^[0-9]+$ ]] || \
            (( soft < CONTAINER_NOFILE_LIMIT || hard < CONTAINER_NOFILE_LIMIT )); then
            echo "$role container nofile limit is below $CONTAINER_NOFILE_LIMIT" >&2
            return 1
        fi
    done
    log "container nofile preflight passed limit=$CONTAINER_NOFILE_LIMIT"
}

start_memory_collector() {
    local data_dir="$1"
    log "starting memory collector interval=${MEMORY_INTERVAL}s backend=$MEMORY_BACKEND"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/collect_node_memory.py" \
        --backend "$MEMORY_BACKEND" \
        --compose-file "$COMPOSE_FILE" \
        --expect-beacons "$BEACONS" \
        --expect-geth "$BEACONS" \
        --expect-validators "$VALIDATOR_CLIENTS" \
        --interval "$MEMORY_INTERVAL" \
        --output-dir "$data_dir" \
        > "$data_dir/memory-collector.log" 2>&1 &
    CURRENT_MEMORY_PID="$!"
    echo "$CURRENT_MEMORY_PID" > "$data_dir/memory-collector.pid"
}

verify_no_geth_oom_kills() {
    local data_dir="$1"
    local memory_file="$data_dir/memory_by_type.csv"
    if [[ "$DRY_RUN" -eq 1 || ! -s "$memory_file" ]]; then
        return 0
    fi
    if ! awk -F, '
        NR == 1 {
            for (i = 1; i <= NF; i++) {
                if ($i == "node_type") node_col = i
                if ($i == "memory_oom_kill_total") oom_col = i
            }
            next
        }
        node_col && oom_col && $node_col == "geth" && $oom_col + 0 > 0 {
            found = 1
        }
        END { exit(found ? 1 : 0) }
    ' "$memory_file"; then
        log "Geth cgroup memory guard detected at least one OOM kill"
        return 77
    fi
}

verify_node_processes() {
    local data_dir="$1"
    local attempt
    local preflight_dir="$data_dir/node-process-preflight"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    mkdir -p "$preflight_dir"
    for ((attempt = 1; attempt <= NODE_PROCESS_PREFLIGHT_RETRIES; attempt++)); do
        log "checking node process counts attempt=$attempt/$NODE_PROCESS_PREFLIGHT_RETRIES"
        if "$PYTHON_BIN" "$SCRIPT_DIR/collect_node_memory.py" \
            --backend "$MEMORY_BACKEND" \
            --compose-file "$COMPOSE_FILE" \
            --output-dir "$preflight_dir/attempt-$attempt" \
            --expect-beacons "$BEACONS" \
            --expect-geth "$BEACONS" \
            --expect-validators "$VALIDATOR_CLIENTS" \
            --once \
            > "$preflight_dir/attempt-$attempt.log" 2>&1; then
            log "node process count check passed"
            return 0
        fi
        log "node process count mismatch; restarting detected fallback services"
        "$PYTHON_BIN" "$SCRIPT_DIR/repair_failed_ethereum_processes.py" \
            --memory-raw "$preflight_dir/attempt-$attempt/memory_raw.csv" \
            --compose-file "$COMPOSE_FILE" \
            > "$preflight_dir/repair-$attempt.json" 2>&1 || true
        sleep 10
    done
    cat "$preflight_dir/attempt-$NODE_PROCESS_PREFLIGHT_RETRIES.log" >&2 || true
    echo "node process count preflight failed" >&2
    return 1
}

start_block_collector() {
    local data_dir="$1"
    log "starting block metrics collector interval=${BLOCK_INTERVAL}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/collect_block_metrics.py" \
        --compose-file "$COMPOSE_FILE" \
        --interval "$BLOCK_INTERVAL" \
        --max-consecutive-errors "$BLOCK_MAX_CONSECUTIVE_ERRORS" \
        --output "$data_dir/block_metrics.csv" \
        > "$data_dir/block-metrics.log" 2>&1 &
    CURRENT_BLOCK_PID="$!"
    echo "$CURRENT_BLOCK_PID" > "$data_dir/block-metrics.pid"
}

verify_execution_rpc() {
    local data_dir="$1"
    local attempt
    local preflight_dir="$data_dir/execution-rpc-preflight"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    mkdir -p "$preflight_dir"
    for ((attempt = 1; attempt <= EXECUTION_RPC_PREFLIGHT_RETRIES; attempt++)); do
        log "checking execution RPC attempt=$attempt/$EXECUTION_RPC_PREFLIGHT_RETRIES"
        if "$PYTHON_BIN" "$SCRIPT_DIR/collect_block_metrics.py" \
            --compose-file "$COMPOSE_FILE" \
            --output "$preflight_dir/block_metrics.csv" \
            --interval 1 \
            --samples 1 \
            --require-success \
            > "$preflight_dir/block-metrics.log" 2>&1; then
            if ! tx_load_enabled || "$PYTHON_BIN" "$SCRIPT_DIR/send_rand_tx.py" \
                --compose-file "$COMPOSE_FILE" \
                --accounts "$TX_ACCOUNT_COUNT" \
                --rate 1 \
                --gas-price-gwei 1 \
                --log-dir "$preflight_dir/tx" \
                --dry-run \
                > "$preflight_dir/tx.log" 2>&1; then
                log "execution RPC and transaction sender check passed"
                return 0
            fi
        fi
        sleep 10
    done
    cat "$preflight_dir/block-metrics.log" >&2 || true
    cat "$preflight_dir/tx.log" >&2 || true
    echo "execution RPC preflight failed" >&2
    return 1
}

tx_load_enabled() {
    [[ "$TX_LOAD_MODE" == "mixed" || "$TX_RATE" != "0" ]]
}

prepare_tx_load() {
    local data_dir="$1"
    if ! tx_load_enabled; then
        return 0
    fi
    mkdir -p "$data_dir/tx-load"
    if [[ "$TX_LOAD_MODE" != "mixed" ]]; then
        return 0
    fi
    log "deploying mixed-transaction setup contract"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/generate_mixed_tx_load.py" \
        --compose-file "$COMPOSE_FILE" \
        --accounts "$TX_ACCOUNT_COUNT" \
        --profile "$TX_MIX_PROFILE" \
        --phases "$TX_LOAD_PHASES" \
        --setup-file "$data_dir/tx-load/setup.json" \
        --seed "$TX_RANDOM_SEED" \
        --prepare-only \
        > "$data_dir/tx-load/setup.log" 2>&1
}

start_consensus_collector() {
    local data_dir="$1"
    log "starting consensus benchmark collector interval=${CONSENSUS_INTERVAL}s sample=${CONSENSUS_BEACON_SAMPLE}"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    local cmd=(
        "$PYTHON_BIN" "$SCRIPT_DIR/collect_consensus_benchmark.py"
        --interval "$CONSENSUS_INTERVAL"
        --beacon-sample "$CONSENSUS_BEACON_SAMPLE"
        --reorg-depth "$REORG_DEPTH"
        --reorg-scan-interval "$REORG_SCAN_INTERVAL"
        --expected-active-validators "$ACTIVE_VALIDATORS"
        --validator-liveness-timeout "$VALIDATOR_LIVENESS_TIMEOUT"
        --validator-liveness-batch-size "$VALIDATOR_LIVENESS_BATCH_SIZE"
        --output-dir "$data_dir/consensus"
    )
    if [[ "$API_LOAD_ENABLED" -eq 1 ]]; then
        cmd+=(
            --beacon-indices-file "$data_dir/api-experiment/api_monitored_beacon_indices.txt"
            --reference-beacon-index "$(<"$data_dir/api-experiment/api_reference_beacon_index.txt")"
        )
    fi
    "${cmd[@]}" \
        > "$data_dir/consensus-benchmark.log" 2>&1 &
    CURRENT_CONSENSUS_PID="$!"
    echo "$CURRENT_CONSENSUS_PID" > "$data_dir/consensus-benchmark.pid"
}

start_block_event_collector() {
    local data_dir="$1"
    if [[ "$BLOCK_EVENT_ENABLED" -eq 0 ]]; then
        return 0
    fi
    log "starting Beacon head event collector sample=${BLOCK_EVENT_BEACON_SAMPLE}"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    mkdir -p "$data_dir/block-visibility"
    "$PYTHON_BIN" "$SCRIPT_DIR/collect_beacon_head_events.py" \
        --compose-file "$COMPOSE_FILE" \
        --output-dir "$data_dir/block-visibility" \
        --beacon-sample "$BLOCK_EVENT_BEACON_SAMPLE" \
        --startup-timeout "$BLOCK_EVENT_STARTUP_TIMEOUT" \
        --read-timeout "$BLOCK_EVENT_READ_TIMEOUT" \
        --reconnect-delay "$BLOCK_EVENT_RECONNECT_DELAY" \
        --min-ready-ratio "$BLOCK_EVENT_MIN_READY_RATIO" \
        > "$data_dir/block-visibility/collector.log" 2>&1 &
    CURRENT_BLOCK_EVENT_PID="$!"
    echo "$CURRENT_BLOCK_EVENT_PID" > "$data_dir/block-visibility/collector.pid"
}

wait_for_block_event_ready() {
    local data_dir="$1"
    local deadline=$((SECONDS + BLOCK_EVENT_STARTUP_TIMEOUT + 10))
    local run_info="$data_dir/block-visibility/run_info.json"
    if [[ "$BLOCK_EVENT_ENABLED" -eq 0 || "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    while (( SECONDS < deadline )); do
        require_background_alive \
            CURRENT_BLOCK_EVENT_PID \
            "Beacon head event collector" \
            "$data_dir/block-visibility/collector.log" || return $?
        if [[ -f "$run_info" ]] && grep -q '"startup_ready": true' "$run_info"; then
            log "Beacon head event streams are ready"
            return 0
        fi
        sleep 1
    done
    cat "$run_info" >&2 2>/dev/null || true
    echo "Beacon head event collector did not become ready" >&2
    return 1
}

stop_block_event_collector() {
    local pid="$CURRENT_BLOCK_EVENT_PID"
    local status=0
    if [[ "$BLOCK_EVENT_ENABLED" -eq 0 ]]; then
        return 0
    fi
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
    fi
    if [[ -n "$pid" ]]; then
        set +e
        wait "$pid"
        status=$?
        set -e
    fi
    CURRENT_BLOCK_EVENT_PID=""
    if [[ "$status" -ne 0 ]]; then
        echo "Beacon head event collector failed with status $status" >&2
        return "$status"
    fi
}

prepare_api_experiment() {
    local data_dir="$1"
    if [[ "$API_LOAD_ENABLED" -eq 0 ]]; then
        return 0
    fi
    if [[ "$LIGHTHOUSE_METRICS_ENABLED" -ne 1 ]]; then
        echo "API_LOAD_ENABLED=1 requires LIGHTHOUSE_METRICS_ENABLED=1" >&2
        return 1
    fi
    if [[ ! -f "$API_LOAD_PROFILE" ]]; then
        echo "missing API load profile: $API_LOAD_PROFILE" >&2
        return 1
    fi
    mkdir -p "$data_dir/api-experiment"
    run_cmd "$PYTHON_BIN" "$SCRIPT_DIR/prepare_api_cohorts.py" \
        --compose-file "$COMPOSE_FILE" \
        --output-dir "$data_dir/api-experiment" \
        --target-count "$API_TARGET_COUNT" \
        --control-count "$API_CONTROL_COUNT" \
        --monitor-per-role "$API_MONITOR_PER_ROLE" \
        --metrics-port "$LIGHTHOUSE_METRICS_PORT"
    if [[ "$DRY_RUN" -eq 0 ]]; then
        cp "$API_LOAD_PROFILE" "$data_dir/api-experiment/api_load_profile.json"
    fi
}

prepare_processor_experiment() {
    local data_dir="$1"
    if [[ "$PROCESSOR_METRICS_ENABLED" -eq 0 || "$API_LOAD_ENABLED" -eq 1 ]]; then
        return 0
    fi
    mkdir -p "$data_dir/processor-experiment"
    run_cmd "$PYTHON_BIN" "$SCRIPT_DIR/prepare_processor_metrics_targets.py" \
        --compose-file "$COMPOSE_FILE" \
        --output "$data_dir/processor-experiment/processor_metrics_beacons.csv" \
        --sample "$PROCESSOR_METRICS_BEACON_SAMPLE" \
        --metrics-port "$LIGHTHOUSE_METRICS_PORT"
}

processor_metrics_beacons_file() {
    local data_dir="$1"
    if [[ "$API_LOAD_ENABLED" -eq 1 ]]; then
        printf '%s\n' "$data_dir/api-experiment/api_cohorts.csv"
    else
        printf '%s\n' "$data_dir/processor-experiment/processor_metrics_beacons.csv"
    fi
}

processor_metrics_phase_state() {
    local data_dir="$1"
    if [[ "$API_LOAD_ENABLED" -eq 1 ]]; then
        printf '%s\n' "$data_dir/api-load/api_phase_state.json"
    elif [[ "$TX_LOAD_MODE" == "mixed" ]]; then
        printf '%s\n' "$data_dir/tx-load/tx_phase_state.json"
    else
        printf '%s\n' ""
    fi
}

expected_processor_worker_cap() {
    local normalized=",${CFG_COMPONENTS// /},"
    if [[ "$normalized" == *",all,"* \
        || "$normalized" == *",processor,"* \
        || "$normalized" == *",processor-workers,"* ]]; then
        printf '%s\n' "$LIGHTHOUSE_PROCESSOR_WORKERS_CAP"
    else
        printf '0\n'
    fi
}

verify_processor_metrics() {
    local data_dir="$1"
    local attempt
    local expected_workers
    if [[ "$PROCESSOR_METRICS_ENABLED" -eq 0 || "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    expected_workers="$(expected_processor_worker_cap)"
    mkdir -p "$data_dir/processor-metrics-preflight"
    for attempt in 1 2 3 4 5; do
        log "checking Lighthouse processor metrics attempt=$attempt/5"
        if "$PYTHON_BIN" "$SCRIPT_DIR/collect_lighthouse_processor_metrics.py" \
            --cohorts "$(processor_metrics_beacons_file "$data_dir")" \
            --output-dir "$data_dir/processor-metrics-preflight" \
            --interval 1 \
            --timeout "$PROCESSOR_METRICS_TIMEOUT" \
            --samples 1 \
            --require-all \
            --expected-max-workers "$expected_workers" \
            > "$data_dir/processor-metrics-preflight.log" 2>&1; then
            log "Lighthouse processor metrics check passed"
            return 0
        fi
        sleep 10
    done
    cat "$data_dir/processor-metrics-preflight.log" >&2 || true
    echo "Lighthouse processor metrics check failed" >&2
    return 1
}

start_processor_metrics_collector() {
    local data_dir="$1"
    local expected_workers
    local phase_state
    local cmd
    if [[ "$PROCESSOR_METRICS_ENABLED" -eq 0 ]]; then
        return 0
    fi
    expected_workers="$(expected_processor_worker_cap)"
    log "starting Beacon Processor metrics collector interval=${PROCESSOR_METRICS_INTERVAL}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    phase_state="$(processor_metrics_phase_state "$data_dir")"
    cmd=(
        "$PYTHON_BIN" "$SCRIPT_DIR/collect_lighthouse_processor_metrics.py"
        --cohorts "$(processor_metrics_beacons_file "$data_dir")"
        --output-dir "$data_dir/processor-metrics"
        --interval "$PROCESSOR_METRICS_INTERVAL"
        --timeout "$PROCESSOR_METRICS_TIMEOUT"
        --expected-max-workers "$expected_workers"
    )
    if [[ -n "$phase_state" ]]; then
        cmd+=(--phase-state "$phase_state")
    fi
    "${cmd[@]}" > "$data_dir/processor-metrics.log" 2>&1 &
    CURRENT_PROCESSOR_METRICS_PID="$!"
    echo "$CURRENT_PROCESSOR_METRICS_PID" > "$data_dir/processor-metrics.pid"
}

start_api_load() {
    local data_dir="$1"
    if [[ "$API_LOAD_ENABLED" -eq 0 ]]; then
        return 0
    fi
    mkdir -p "$data_dir/api-load"
    log "starting API load targets=$API_TARGET_COUNT phases=$API_LOAD_PHASES"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/generate_beacon_api_load.py" \
        --cohorts "$data_dir/api-experiment/api_cohorts.csv" \
        --profile "$API_LOAD_PROFILE" \
        --phases "$API_LOAD_PHASES" \
        --output-dir "$data_dir/api-load" \
        --concurrency-per-node "$API_CONCURRENCY_PER_NODE" \
        --timeout "$API_REQUEST_TIMEOUT" \
        > "$data_dir/api-load.log" 2>&1 &
    CURRENT_API_LOAD_PID="$!"
    echo "$CURRENT_API_LOAD_PID" > "$data_dir/api-load.pid"
}

wait_for_sampling() {
    local data_dir="$1"
    local deadline=$((SECONDS + DURATION_SECONDS))
    local hard_deadline=$((deadline + TX_SCHEDULE_GRACE_SECONDS))
    local status
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    while true; do
        sample_host_resources "$data_dir" || {
            status=$?
            return "$status"
        }
        require_background_alive \
            CURRENT_BLOCK_PID "block metrics collector" "$data_dir/block-metrics.log" || return $?
        require_background_alive \
            CURRENT_CONSENSUS_PID "consensus collector" "$data_dir/consensus-benchmark.log" || return $?
        require_background_alive \
            CURRENT_MEMORY_PID "memory collector" "$data_dir/memory-collector.log" || return $?
        if [[ "$BLOCK_EVENT_ENABLED" -eq 1 ]]; then
            require_background_alive \
                CURRENT_BLOCK_EVENT_PID \
                "Beacon head event collector" \
                "$data_dir/block-visibility/collector.log" || return $?
        fi
        if tx_load_enabled; then
            require_background_alive \
                CURRENT_TX_PID "transaction sender" "$data_dir/tx-load/stdout.log" || return $?
            require_background_alive \
                CURRENT_TX_RECEIPT_PID "transaction receipt collector" "$data_dir/tx-receipts.log" || return $?
        fi
        if [[ "$PROCESSOR_METRICS_ENABLED" -eq 1 ]]; then
            require_background_alive \
                CURRENT_PROCESSOR_METRICS_PID \
                "processor metrics collector" \
                "$data_dir/processor-metrics.log" || return $?
        fi
        if [[ "$API_LOAD_ENABLED" -eq 1 && -n "$CURRENT_API_LOAD_PID" ]] && \
            ! kill -0 "$CURRENT_API_LOAD_PID" 2>/dev/null; then
            set +e
            wait "$CURRENT_API_LOAD_PID"
            status=$?
            set -e
            CURRENT_API_LOAD_PID=""
            if [[ "$status" -ne 0 ]]; then
                echo "API load generator failed with status $status" >&2
                return "$status"
            fi
            log "API load schedule completed"
        fi
        if [[ "$TX_LOAD_MODE" == "mixed" ]]; then
            status="$(read_tx_receipt_field "$data_dir/tx-load/tx_phase_state.json" status)"
            if [[ "$status" == "complete" ]]; then
                log "mixed transaction phase schedule completed"
                return 0
            fi
            if [[ "$status" == "failed" || "$status" == "stopped" ]]; then
                echo "mixed transaction phase schedule ended with status=$status" >&2
                return 1
            fi
            if (( SECONDS >= hard_deadline )); then
                echo "mixed transaction schedule exceeded ${DURATION_SECONDS}s plus ${TX_SCHEDULE_GRACE_SECONDS}s grace" >&2
                return 1
            fi
        elif (( SECONDS >= deadline )); then
            return 0
        fi
        sleep 10
    done
}

require_background_alive() {
    local variable_name="$1"
    local label="$2"
    local log_path="${3:-}"
    local pid="${!variable_name}"
    local status
    if [[ -z "$pid" ]] || kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    set +e
    wait "$pid"
    status=$?
    set -e
    printf -v "$variable_name" '%s' ""
    if [[ "$status" -eq 0 ]]; then
        log "$label exited unexpectedly before sampling completed"
        if [[ -n "$log_path" && -f "$log_path" ]]; then
            tail -n 20 "$log_path" | tee -a "$RUN_DIR/run.log" >&2
        fi
        return 1
    fi
    log "$label failed with status $status"
    if [[ -n "$log_path" && -f "$log_path" ]]; then
        tail -n 20 "$log_path" | tee -a "$RUN_DIR/run.log" >&2
    fi
    return "$status"
}

start_tx_load() {
    local data_dir="$1"
    if ! tx_load_enabled; then
        log "tx load disabled"
        return 0
    fi
    mkdir -p "$data_dir/tx-load"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    if [[ "$TX_LOAD_MODE" == "mixed" ]]; then
        log "starting mixed tx phases=$TX_LOAD_PHASES"
        "$PYTHON_BIN" "$SCRIPT_DIR/generate_mixed_tx_load.py" \
            --compose-file "$COMPOSE_FILE" \
            --accounts "$TX_ACCOUNT_COUNT" \
            --profile "$TX_MIX_PROFILE" \
            --phases "$TX_LOAD_PHASES" \
            --setup-file "$data_dir/tx-load/setup.json" \
            --log-dir "$data_dir/tx-load" \
            --seed "$TX_RANDOM_SEED" \
            --max-consecutive-failures "$TX_MAX_CONSECUTIVE_FAILURES" \
            --hold-after-schedule \
            > "$data_dir/tx-load/stdout.log" 2>&1 &
    else
        log "starting tx load rate=${TX_RATE}/s"
        "$PYTHON_BIN" "$SCRIPT_DIR/send_rand_tx.py" \
            --compose-file "$COMPOSE_FILE" \
            --accounts "$TX_ACCOUNT_COUNT" \
            --rate "$TX_RATE" \
            --max-consecutive-failures "$TX_MAX_CONSECUTIVE_FAILURES" \
            --gas-price-gwei 1 \
            --log-dir "$data_dir/tx-load" \
            > "$data_dir/tx-load/stdout.log" 2>&1 &
    fi
    CURRENT_TX_PID="$!"
    echo "$CURRENT_TX_PID" > "$data_dir/tx-load/pid"
}

start_tx_receipt_collector() {
    local data_dir="$1"
    if ! tx_load_enabled; then
        return 0
    fi
    log "starting tx receipt collector interval=${TX_RECEIPT_INTERVAL}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/collect_tx_receipts.py" \
        --compose-file "$COMPOSE_FILE" \
        --tx-log "$data_dir/tx-load/tx_sender.csv" \
        --output "$data_dir/tx_receipts.csv" \
        --interval "$TX_RECEIPT_INTERVAL" \
        --mode "$TX_RECEIPT_MODE" \
        > "$data_dir/tx-receipts.log" 2>&1 &
    CURRENT_TX_RECEIPT_PID="$!"
    echo "$CURRENT_TX_RECEIPT_PID" > "$data_dir/tx-receipts.pid"
}

stop_tx_load() {
    stop_pid "$CURRENT_TX_PID"
    CURRENT_TX_PID=""
}

read_tx_receipt_field() {
    local run_info="$1"
    local field="$2"
    if [[ ! -f "$run_info" ]]; then
        echo ""
        return 0
    fi
    "$PYTHON_BIN" -c 'import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
field = sys.argv[2]
try:
    print(json.loads(path.read_text()).get(field, ""))
except Exception:
    print("")
' "$run_info" "$field"
}

drain_tx_receipts() {
    local data_dir="$1"
    local run_info="$data_dir/tx_receipts_run_info.json"
    local pending known committed deadline

    if ! tx_load_enabled; then
        return 0
    fi
    if [[ -z "$CURRENT_TX_RECEIPT_PID" ]]; then
        return 0
    fi

    log "draining tx receipts timeout=${TX_DRAIN_TIMEOUT}s interval=${TX_DRAIN_INTERVAL}s"
    deadline=$((SECONDS + TX_DRAIN_TIMEOUT))
    while (( SECONDS < deadline )); do
        if ! kill -0 "$CURRENT_TX_RECEIPT_PID" 2>/dev/null; then
            log "tx receipt collector exited during drain"
            return 0
        fi

        pending="$(read_tx_receipt_field "$run_info" pending_transactions)"
        known="$(read_tx_receipt_field "$run_info" known_sent_transactions)"
        committed="$(read_tx_receipt_field "$run_info" committed_transactions)"

        if [[ "$pending" =~ ^[0-9]+$ ]]; then
            log "tx receipt drain known=${known:-?} committed=${committed:-?} pending=$pending"
            if (( pending == 0 )); then
                log "tx receipt drain complete"
                return 0
            fi
        else
            log "tx receipt drain pending=unknown"
        fi

        sleep "$TX_DRAIN_INTERVAL"
    done

    pending="$(read_tx_receipt_field "$run_info" pending_transactions)"
    log "tx receipt drain timeout reached pending=${pending:-unknown}; continuing cleanup"
}

write_experiment_env() {
    local data_dir="$1"
    local variant="$2"
    {
        echo "run_id=$RUN_ID-$variant"
        echo "variant=$variant"
        echo "beacons=$BEACONS"
        echo "geth=$BEACONS"
        echo "validator_clients=$VALIDATOR_CLIENTS"
        echo "validator_keys_per_client=$VALIDATOR_KEYS_PER_CLIENT"
        echo "validators=$ACTIVE_VALIDATORS"
        echo "stub_as_count=$STUB_AS_COUNT"
        echo "historic_states=all"
        echo "cache_profile=default"
        echo "beacon_peers=default"
        echo "lighthouse_profile=$CFG_PROFILE"
        echo "lighthouse_image=$CFG_IMAGE"
        echo "lighthouse_component_image_id=$LIGHTHOUSE_COMPONENT_IMAGE_ID"
        echo "lighthouse_component_patch_sha256=$LIGHTHOUSE_COMPONENT_PATCH_SHA"
        echo "lighthouse_components=$CFG_COMPONENTS"
        echo "lighthouse_rayon_threads=$CFG_RAYON_THREADS"
        echo "lighthouse_processor_workers_cap=$LIGHTHOUSE_PROCESSOR_WORKERS_CAP"
        echo "geth_cache_mb=$GETH_CACHE_MB"
        echo "geth_go_mem_limit_mb=$GETH_GO_MEM_LIMIT_MB"
        echo "geth_memory_max_mb=$GETH_MEMORY_MAX_MB"
        echo "lighthouse_metrics_enabled=$LIGHTHOUSE_METRICS_ENABLED"
        echo "lighthouse_metrics_port=$LIGHTHOUSE_METRICS_PORT"
        echo "api_load_enabled=$API_LOAD_ENABLED"
        echo "api_load_profile=$API_LOAD_PROFILE"
        echo "api_load_phases=$API_LOAD_PHASES"
        echo "api_target_count=$API_TARGET_COUNT"
        echo "api_control_count=$API_CONTROL_COUNT"
        echo "api_monitor_per_role=$API_MONITOR_PER_ROLE"
        echo "api_concurrency_per_node=$API_CONCURRENCY_PER_NODE"
        echo "api_request_timeout=$API_REQUEST_TIMEOUT"
        echo "processor_metrics_enabled=$PROCESSOR_METRICS_ENABLED"
        echo "processor_metrics_beacon_sample=$PROCESSOR_METRICS_BEACON_SAMPLE"
        echo "processor_metrics_interval=$PROCESSOR_METRICS_INTERVAL"
        echo "processor_metrics_timeout=$PROCESSOR_METRICS_TIMEOUT"
        echo "block_event_enabled=$BLOCK_EVENT_ENABLED"
        echo "block_event_beacon_sample=$BLOCK_EVENT_BEACON_SAMPLE"
        echo "block_event_startup_timeout=$BLOCK_EVENT_STARTUP_TIMEOUT"
        echo "block_event_read_timeout=$BLOCK_EVENT_READ_TIMEOUT"
        echo "block_event_reconnect_delay=$BLOCK_EVENT_RECONNECT_DELAY"
        echo "block_event_min_ready_ratio=$BLOCK_EVENT_MIN_READY_RATIO"
        echo "block_event_min_coverage_ratio=$BLOCK_EVENT_MIN_COVERAGE_RATIO"
        echo "block_event_warmup_seconds=$BLOCK_EVENT_WARMUP_SECONDS"
        echo "block_event_tail_seconds=$BLOCK_EVENT_TAIL_SECONDS"
        echo "duration_seconds=$DURATION_SECONDS"
        echo "tx_rate=$TX_RATE"
        echo "tx_load_mode=$TX_LOAD_MODE"
        echo "tx_mix_profile=$TX_MIX_PROFILE"
        echo "tx_load_phases=$TX_LOAD_PHASES"
        echo "tx_account_count=$TX_ACCOUNT_COUNT"
        echo "tx_random_seed=$TX_RANDOM_SEED"
        echo "tx_schedule_grace_seconds=$TX_SCHEDULE_GRACE_SECONDS"
        echo "tx_receipt_mode=$TX_RECEIPT_MODE"
        echo "tx_max_consecutive_failures=$TX_MAX_CONSECUTIVE_FAILURES"
        echo "tx_drain_timeout=$TX_DRAIN_TIMEOUT"
        echo "tx_drain_interval=$TX_DRAIN_INTERVAL"
        echo "memory_interval=$MEMORY_INTERVAL"
        echo "container_nofile_limit=$CONTAINER_NOFILE_LIMIT"
        echo "host_min_inotify_instances=$HOST_MIN_INOTIFY_INSTANCES"
        echo "host_memory_guard_enabled=$HOST_MEMORY_GUARD_ENABLED"
        echo "host_min_available_gib=$HOST_MIN_AVAILABLE_GIB"
        echo "host_max_swap_used_gib=$HOST_MAX_SWAP_USED_GIB"
        echo "block_interval=$BLOCK_INTERVAL"
        echo "block_max_consecutive_errors=$BLOCK_MAX_CONSECUTIVE_ERRORS"
        echo "consensus_interval=$CONSENSUS_INTERVAL"
        echo "consensus_beacon_sample=$CONSENSUS_BEACON_SAMPLE"
        echo "validator_liveness_timeout=$VALIDATOR_LIVENESS_TIMEOUT"
        echo "validator_liveness_batch_size=$VALIDATOR_LIVENESS_BATCH_SIZE"
        echo "reorg_depth=$REORG_DEPTH"
        echo "reorg_scan_interval=$REORG_SCAN_INTERVAL"
        echo "tx_receipt_interval=$TX_RECEIPT_INTERVAL"
        echo "build_mode=$BUILD_MODE"
        echo "up_preset=$UP_PRESET"
        echo "python_bin=$PYTHON_BIN"
        echo "started_at=$(date -Is)"
    } > "$data_dir/experiment.env"
}

capture_final_state() {
    local data_dir="$1"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    env EXPECTED_ACTIVE_VALIDATORS="$ACTIVE_VALIDATORS" \
        "$SCRIPT_DIR/check_pos_health.sh" > "$data_dir/health-final.log" 2>&1 || true
    docker compose -f "$COMPOSE_FILE" ps > "$data_dir/compose-ps-end.txt" 2>&1 || true
    docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' > "$data_dir/docker-ps-end.txt" 2>&1 || true
}

run_variant() {
    local variant="$1"
    local is_last="$2"
    local data_dir="$RUN_DIR/$variant"
    local build_log_dir="$data_dir/build-logs"
    local up_log_dir="$data_dir/up-logs"

    COMPOSE_DOWN_ON_EXIT=1
    variant_components "$variant"
    mkdir -p "$data_dir" "$build_log_dir" "$up_log_dir"
    write_experiment_env "$data_dir" "$variant"

    log "========== variant=$variant components=${CFG_COMPONENTS:-official} geth_cache_mb=$GETH_CACHE_MB geth_go_mem_limit_mb=$GETH_GO_MEM_LIMIT_MB geth_memory_max_mb=$GETH_MEMORY_MAX_MB =========="
    cleanup_bg
    compose_down
    generate_output "$data_dir"

    if [[ "$BUILD_MODE" == "nobuild-mount" ]]; then
        log "BUILD_MODE=nobuild-mount: generated per-node build skipped"
    elif [[ "$SKIP_BUILD" -eq 0 ]]; then
        run_cmd env DOCKER_BUILDKIT="$BUILD_KIT" LOG_DIR="$build_log_dir" \
            "$SCRIPT_DIR/batch_parallel_build.sh" \
            --buildkit "$BUILD_KIT" \
            --batch-size "$BUILD_BATCH_SIZE" \
            --jobs "$BUILD_JOBS" \
            --retry "$BUILD_RETRY"
    else
        log "skip build enabled"
    fi

    run_cmd env LOG_DIR="$up_log_dir" "$SCRIPT_DIR/batch_parallel_up.sh" --preset "$UP_PRESET"
    verify_container_nofile "$data_dir"
    wait_for_health "$data_dir"
    verify_node_processes "$data_dir"
    prepare_api_experiment "$data_dir"
    prepare_processor_experiment "$data_dir"
    verify_processor_metrics "$data_dir"
    verify_execution_rpc "$data_dir"
    prepare_tx_load "$data_dir"

    start_block_event_collector "$data_dir"
    wait_for_block_event_ready "$data_dir"
    start_block_collector "$data_dir"
    start_consensus_collector "$data_dir"
    start_memory_collector "$data_dir"
    start_processor_metrics_collector "$data_dir"
    start_tx_load "$data_dir"
    start_tx_receipt_collector "$data_dir"
    start_api_load "$data_dir"

    initialize_host_resource_log "$data_dir"
    sample_host_resources "$data_dir"
    log "sampling for ${DURATION_SECONDS}s"
    wait_for_sampling "$data_dir"
    verify_no_geth_oom_kills "$data_dir"

    stop_block_event_collector
    stop_tx_load
    drain_tx_receipts "$data_dir"
    capture_final_state "$data_dir"
    cleanup_bg
    if [[ "$API_LOAD_ENABLED" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
        log "summarizing API contention data"
        "$PYTHON_BIN" "$SCRIPT_DIR/summarize_api_worker_contention.py" \
            --data-dir "$data_dir" \
            > "$data_dir/api-summary.log" 2>&1
    fi
    if [[ "$BLOCK_EVENT_ENABLED" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
        log "summarizing high-resolution block visibility"
        "$PYTHON_BIN" "$SCRIPT_DIR/summarize_beacon_head_events.py" \
            --data-dir "$data_dir" \
            --warmup-seconds "$BLOCK_EVENT_WARMUP_SECONDS" \
            --tail-seconds "$BLOCK_EVENT_TAIL_SECONDS" \
            --min-coverage-ratio "$BLOCK_EVENT_MIN_COVERAGE_RATIO" \
            > "$data_dir/block-visibility/summary.log" 2>&1
    fi
    if [[ "$TX_LOAD_MODE" == "mixed" && "$DRY_RUN" -eq 0 ]]; then
        log "summarizing mixed transaction stress data"
        "$PYTHON_BIN" "$SCRIPT_DIR/summarize_worker_tx_stress.py" \
            --data-dir "$data_dir" \
            > "$data_dir/tx-stress-summary.log" 2>&1
    fi
    [[ -f "$up_log_dir/summary.log" ]] && cp "$up_log_dir/summary.log" "$data_dir/up-summary.log"
    [[ -f "$build_log_dir/summary.log" ]] && cp "$build_log_dir/summary.log" "$data_dir/build-summary.log"

    if [[ "$KEEP_LAST" -eq 0 || "$is_last" -eq 0 ]]; then
        compose_down
    else
        COMPOSE_DOWN_ON_EXIT=0
    fi
    log "completed variant=$variant"
}

chmod +x \
    "$SCRIPT_DIR/compose_endpoints.py" \
    "$SCRIPT_DIR/collect_node_memory.py" \
    "$SCRIPT_DIR/collect_block_metrics.py" \
    "$SCRIPT_DIR/collect_beacon_head_events.py" \
    "$SCRIPT_DIR/collect_consensus_benchmark.py" \
    "$SCRIPT_DIR/collect_lighthouse_processor_metrics.py" \
    "$SCRIPT_DIR/collect_tx_receipts.py" \
    "$SCRIPT_DIR/generate_mixed_tx_load.py" \
    "$SCRIPT_DIR/generate_beacon_api_load.py" \
    "$SCRIPT_DIR/prepare_api_cohorts.py" \
    "$SCRIPT_DIR/prepare_processor_metrics_targets.py" \
    "$SCRIPT_DIR/repair_failed_ethereum_processes.py" \
    "$SCRIPT_DIR/summarize_api_worker_contention.py" \
    "$SCRIPT_DIR/summarize_beacon_head_events.py" \
    "$SCRIPT_DIR/summarize_worker_tx_stress.py" \
    "$SCRIPT_DIR/send_rand_tx.py" || true
chmod +x \
    "$SCRIPT_DIR/configure_large_scale_host.sh" \
    "$SCRIPT_DIR/run_worker_block_visibility_sweep.sh" \
    "$SCRIPT_DIR/verify_lighthouse_component_image.sh" || true

read -r -a VARIANT_ARRAY <<< "$VARIANTS"
if [[ "${#VARIANT_ARRAY[@]}" -eq 0 ]]; then
    echo "No variants specified." >&2
    exit 2
fi

log "fork component benchmark started: run_dir=$RUN_DIR variants=[$VARIANTS]"
verify_host_prerequisites
check_or_build_images "${VARIANT_ARRAY[@]}"
for index in "${!VARIANT_ARRAY[@]}"; do
    last=0
    if [[ "$index" -eq "$((${#VARIANT_ARRAY[@]} - 1))" ]]; then
        last=1
    fi
    run_variant "${VARIANT_ARRAY[$index]}" "$last"
done

if [[ "$KEEP_LAST" -eq 0 ]]; then
    COMPOSE_DOWN_ON_EXIT=0
fi
log "fork component benchmark completed"
