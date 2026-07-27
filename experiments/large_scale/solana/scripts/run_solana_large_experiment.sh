#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_solana_large_experiment.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOLANA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
D61_DIR="$(cd "$SOLANA_DIR/.." && pwd)"
REPO_ROOT="$(cd "$D61_DIR/../../.." && pwd)"
SRC_DIR="$SOLANA_DIR/src"
OUTPUT_DIR="${OUTPUT_DIR:-}"
COMPOSE_FILE="${COMPOSE_FILE:-}"

PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

VALIDATORS="${VALIDATORS:-300}"
STUB_AS_COUNT="${STUB_AS_COUNT:-64}"
PLATFORM="${PLATFORM:-amd}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
INTERNET_MAP="${INTERNET_MAP:-1}"
DURATION_SECONDS="${DURATION_SECONDS:-36000}"
MEMORY_INTERVAL="${MEMORY_INTERVAL:-60}"
BLOCK_INTERVAL="${BLOCK_INTERVAL:-12}"
ACTIVE_INTERVAL="${ACTIVE_INTERVAL:-30}"
GOSSIP_INTERVAL="${GOSSIP_INTERVAL:-300}"
SOLANA_METRICS_RPC_TIMEOUT="${SOLANA_METRICS_RPC_TIMEOUT:-60}"
TX_RATE="${TX_RATE:-1}"
TX_AMOUNT="${TX_AMOUNT:-0.001}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-1800}"
SLOT_RETRIES="${SLOT_RETRIES:-5}"
HEALTH_DOCKER_TIMEOUT="${HEALTH_DOCKER_TIMEOUT:-120}"
CLEANUP_DOCKER_TIMEOUT="${CLEANUP_DOCKER_TIMEOUT:-900}"
LOG_DOCKER_TIMEOUT="${LOG_DOCKER_TIMEOUT:-30}"
SOLANA_LOG_TAIL="${SOLANA_LOG_TAIL:-2000}"
SOLANA_SLOT_LOG_TAIL="${SOLANA_SLOT_LOG_TAIL:-500}"
MIN_VOTING_VALIDATORS_SET="${MIN_VOTING_VALIDATORS+x}"
MIN_VOTING_VALIDATORS="${MIN_VOTING_VALIDATORS:-1}"
MAX_DELINQUENT_VALIDATORS_SET="${MAX_DELINQUENT_VALIDATORS+x}"
MAX_DELINQUENT_VALIDATORS="${MAX_DELINQUENT_VALIDATORS:-}"
STRICT_VALIDATOR_STATUS_SET="${STRICT_VALIDATOR_STATUS+x}"
STRICT_VALIDATOR_STATUS="${STRICT_VALIDATOR_STATUS:-1}"
MIN_ACTIVE_SOLANA="${MIN_ACTIVE_SOLANA:-$VALIDATORS}"
MIN_GOSSIP_NODES="${MIN_GOSSIP_NODES:-0}"
ACTIVE_WAIT="${ACTIVE_WAIT:-3600}"
ACTIVE_COUNT_MODE="${ACTIVE_COUNT_MODE:-process-scan}"
ACTIVE_CONFIRM_RETRIES="${ACTIVE_CONFIRM_RETRIES:-2}"
ACTIVE_CONFIRM_DELAY="${ACTIVE_CONFIRM_DELAY:-5}"
SKIP_DOCKER_RUNNING_COUNT="${SKIP_DOCKER_RUNNING_COUNT:-}"
REQUIRE_TX_HEALTH="${REQUIRE_TX_HEALTH:-1}"
TX_CHECK_TIMEOUT="${TX_CHECK_TIMEOUT:-180}"
TX_SUBMIT_TIMEOUT="${TX_SUBMIT_TIMEOUT:-90}"
BUILD_MODE="${BUILD_MODE:-nobuild-mount}"
SKIP_BASE_BUILD="${SKIP_BASE_BUILD:-0}"
SKIP_BUILD="${SKIP_BUILD:-0}"
BUILD_ONLY="${BUILD_ONLY:-0}"
KEEP_LAST="${KEEP_LAST:-0}"
KEEP_ON_FAILURE="${KEEP_ON_FAILURE:-0}"
CLEAN_D61_SOLANA_DOCKER="${CLEAN_D61_SOLANA_DOCKER:-1}"
CLEAN_D61_SOLANA_PROJECT_REGEX="${CLEAN_D61_SOLANA_PROJECT_REGEX:-(^[0-9]{8}-[0-9]{6}-solana-v[0-9]+$|^solana-[0-9].*)}"
DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-0}"
DOCKER_BUILD_NETWORK="${DOCKER_BUILD_NETWORK:-host}"
BUILD_BATCH_SIZE="${BUILD_BATCH_SIZE:-50}"
BUILD_JOBS="${BUILD_JOBS:-4}"
BUILD_RETRY="${BUILD_RETRY:-1}"
UP_PRESET="${UP_PRESET:-fast}"
UP_PHASE_DELAY_SET="${UP_PHASE_DELAY+x}"
UP_PHASE_DELAY="${UP_PHASE_DELAY:-120}"
MEMORY_BACKEND="${MEMORY_BACKEND:-cgroup-scan}"
SOLANA_TWO_STAGE_STARTUP="${SOLANA_TWO_STAGE_STARTUP:-0}"
CORE_HEALTH_TIMEOUT="${CORE_HEALTH_TIMEOUT:-$HEALTH_TIMEOUT}"
CORE_ACTIVE_WAIT="${CORE_ACTIVE_WAIT:-$ACTIVE_WAIT}"
CORE_SLOT_RETRIES="${CORE_SLOT_RETRIES:-$SLOT_RETRIES}"
CORE_REQUIRE_TX_HEALTH="${CORE_REQUIRE_TX_HEALTH:-1}"
CORE_TX_CHECK_TIMEOUT="${CORE_TX_CHECK_TIMEOUT:-$TX_CHECK_TIMEOUT}"
CORE_STABILIZE_SECONDS="${CORE_STABILIZE_SECONDS:-300}"
CORE_STABILIZE_CHECK_INTERVAL="${CORE_STABILIZE_CHECK_INTERVAL:-30}"
SOLANA_SLOTS_PER_EPOCH="${SOLANA_SLOTS_PER_EPOCH:-64}"
SOLANA_VALIDATOR_STAKE_SOL="${SOLANA_VALIDATOR_STAKE_SOL:-2}"
SOLANA_ENABLE_VALIDATOR_STAKE="${SOLANA_ENABLE_VALIDATOR_STAKE:-1}"
SOLANA_STAKED_VALIDATORS="${SOLANA_STAKED_VALIDATORS:-0}"
SOLANA_NON_STAKED_NO_VOTING="${SOLANA_NON_STAKED_NO_VOTING:-0}"
SOLANA_SPLIT_VALIDATOR_PHASES="${SOLANA_SPLIT_VALIDATOR_PHASES:-0}"
SOLANA_GENESIS_VOTING_VALIDATORS="${SOLANA_GENESIS_VOTING_VALIDATORS:-1}"
SOLANA_GENESIS_STAGED_STARTUP="${SOLANA_GENESIS_STAGED_STARTUP:-0}"
SOLANA_GENESIS_START_BARRIER_VALIDATORS="${SOLANA_GENESIS_START_BARRIER_VALIDATORS:-0}"
SOLANA_COMBINED_STARTUP="${SOLANA_COMBINED_STARTUP:-$SOLANA_GENESIS_VOTING_VALIDATORS}"
SOLANA_OBSERVER_BATCH_SIZE="${SOLANA_OBSERVER_BATCH_SIZE:-10}"
SOLANA_OBSERVER_JOBS="${SOLANA_OBSERVER_JOBS:-1}"
SOLANA_OBSERVER_BATCH_DELAY="${SOLANA_OBSERVER_BATCH_DELAY:-60}"
SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT="${SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT:-4}"
SOLANA_NOFILE="${SOLANA_NOFILE:-1000000}"
SOLANA_DISABLE_WARMUP_EPOCHS="${SOLANA_DISABLE_WARMUP_EPOCHS:-1}"
SOLANA_TARGET_TICK_DURATION_MS="${SOLANA_TARGET_TICK_DURATION_MS:-0}"
SOLANA_TICKS_PER_SLOT="${SOLANA_TICKS_PER_SLOT:-0}"
SOLANA_GENESIS_CREATION_TIME="${SOLANA_GENESIS_CREATION_TIME:-2026-01-01T00:00:00Z}"
SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS="${SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS:-100000000000}"
SOLANA_GENESIS_BOOTSTRAP_BALANCE_LAMPORTS="${SOLANA_GENESIS_BOOTSTRAP_BALANCE_LAMPORTS:-500000000000}"
SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS="${SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS:-50000000}"
SOLANA_GENESIS_VALIDATOR_BALANCE_LAMPORTS="${SOLANA_GENESIS_VALIDATOR_BALANCE_LAMPORTS:-500000000000}"
SOLANA_GENESIS_CORE_VALIDATORS="${SOLANA_GENESIS_CORE_VALIDATORS:-31}"
SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS="${SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS:-1000000000000}"
SOLANA_TPU_DISABLE_QUIC="${SOLANA_TPU_DISABLE_QUIC:-0}"
SOLANA_TPU_ENABLE_UDP="${SOLANA_TPU_ENABLE_UDP:-0}"
SOLANA_AGAVE_LOW_MEMORY_PROFILE="${SOLANA_AGAVE_LOW_MEMORY_PROFILE:-0}"
SOLANA_AGAVE_LOW_MEMORY_LEVEL="${SOLANA_AGAVE_LOW_MEMORY_LEVEL:-standard}"
SLOT_STALL_TIMEOUT_SET="${SLOT_STALL_TIMEOUT+x}"
SLOT_STALL_TIMEOUT="${SLOT_STALL_TIMEOUT:-0}"
SLOT_STALL_GRACE="${SLOT_STALL_GRACE:-300}"
SLOT_STALL_CHECK_INTERVAL="${SLOT_STALL_CHECK_INTERVAL:-60}"
ACTIVE_STALL_TIMEOUT="${ACTIVE_STALL_TIMEOUT:-0}"
ACTIVE_STALL_GRACE="${ACTIVE_STALL_GRACE:-300}"
POST_HEALTH_STABILIZE_SECONDS="${POST_HEALTH_STABILIZE_SECONDS:-0}"
STRICT_ACTIVE_SAMPLING_SET="${STRICT_ACTIVE_SAMPLING+x}"
STRICT_ACTIVE_SAMPLING="${STRICT_ACTIVE_SAMPLING:-0}"
WAIT_FOR_LOCK="${WAIT_FOR_LOCK:-0}"
WAIT_FOR_LOAD_BELOW="${WAIT_FOR_LOAD_BELOW:-0}"
LOAD_WAIT_TIMEOUT="${LOAD_WAIT_TIMEOUT:-0}"
LOAD_WAIT_INTERVAL="${LOAD_WAIT_INTERVAL:-30}"
HOST_MEMORY_MIN_AVAILABLE_GIB="${HOST_MEMORY_MIN_AVAILABLE_GIB:-0}"
HOST_MEMORY_CHECK_INTERVAL="${HOST_MEMORY_CHECK_INTERVAL:-30}"
CAPTURE_FINAL_DIAGNOSTICS="${CAPTURE_FINAL_DIAGNOSTICS:-0}"
DRY_RUN=0

CURRENT_COLLECTOR_PID=""
CURRENT_BLOCK_PID=""
CURRENT_ACTIVE_PID=""
CURRENT_GOSSIP_PID=""
CURRENT_TX_PID=""
CURRENT_HOST_MEMORY_PID=""
COMPOSE_DOWN_ON_EXIT=1

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --validators N       Total Solana validators including bootstrap. Default: $VALIDATORS
  --stub-as-count N    Stub AS count. Default: $STUB_AS_COUNT
  --duration SEC       Sampling duration after health passes. Default: $DURATION_SECONDS
  --tx-rate N          Continuous transfer target rate per second. Default: $TX_RATE
  --build-mode MODE    nobuild-mount or docker-build. Default: $BUILD_MODE
  --staked-validators N
                       Legacy mode only: stake/delegate only the first N
                       non-bootstrap validators. -1 means all.
  --genesis-voting-validators
                       Put all validators in genesis and start them as voting validators.
                       This is the default; set SOLANA_GENESIS_VOTING_VALIDATORS=0
                       only for legacy observer/dynamic-stake experiments.
  --disable-validator-stake
                       Legacy mode only: run non-bootstrap validators without
                       delegating stake
  --skip-base-build    Do not build seedemu-solana base image
  --skip-build         Reuse existing per-node images when BUILD_MODE=docker-build
  --build-only         Stop after generate/patch/build; do not up or sample
  --keep-last          Do not compose down after successful completion
  --keep-on-failure    Do not compose down after a failed run
  --dry-run            Print actions without running them
  -h, --help           Show this help

Environment overrides:
  VALIDATORS, STUB_AS_COUNT, PLATFORM, DOCKER_PLATFORM, INTERNET_MAP,
  DURATION_SECONDS, MEMORY_INTERVAL, BLOCK_INTERVAL, ACTIVE_INTERVAL,
  GOSSIP_INTERVAL,
  SOLANA_METRICS_RPC_TIMEOUT,
  TX_RATE, TX_AMOUNT, TX_SUBMIT_TIMEOUT,
  HEALTH_TIMEOUT, SLOT_RETRIES, HEALTH_DOCKER_TIMEOUT, SOLANA_LOG_TAIL,
  MIN_VOTING_VALIDATORS, MAX_DELINQUENT_VALIDATORS, STRICT_VALIDATOR_STATUS,
  MIN_ACTIVE_SOLANA, MIN_GOSSIP_NODES, ACTIVE_WAIT, ACTIVE_COUNT_MODE,
  ACTIVE_CONFIRM_RETRIES, ACTIVE_CONFIRM_DELAY,
  BUILD_MODE, SKIP_BASE_BUILD,
  REQUIRE_TX_HEALTH, TX_CHECK_TIMEOUT, SKIP_BUILD, BUILD_ONLY, KEEP_LAST,
  KEEP_ON_FAILURE, CLEAN_D61_SOLANA_DOCKER, CLEAN_D61_SOLANA_PROJECT_REGEX,
  DOCKER_BUILDKIT, DOCKER_BUILD_NETWORK, BUILD_BATCH_SIZE, BUILD_JOBS,
  BUILD_RETRY, UP_PRESET, UP_PHASE_DELAY, MEMORY_BACKEND, SOLANA_TWO_STAGE_STARTUP,
  CORE_HEALTH_TIMEOUT, CORE_ACTIVE_WAIT, CORE_SLOT_RETRIES, CORE_REQUIRE_TX_HEALTH,
  CORE_TX_CHECK_TIMEOUT, CORE_STABILIZE_SECONDS, CORE_STABILIZE_CHECK_INTERVAL,
  SOLANA_SLOTS_PER_EPOCH,
  SOLANA_VALIDATOR_STAKE_SOL, SOLANA_ENABLE_VALIDATOR_STAKE,
  SOLANA_STAKED_VALIDATORS, SOLANA_NON_STAKED_NO_VOTING,
  SOLANA_SPLIT_VALIDATOR_PHASES, SOLANA_GENESIS_VOTING_VALIDATORS,
  SOLANA_GENESIS_STAGED_STARTUP, SOLANA_GENESIS_START_BARRIER_VALIDATORS,
  SOLANA_COMBINED_STARTUP,
  SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS,
  SOLANA_GENESIS_BOOTSTRAP_BALANCE_LAMPORTS,
  SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS,
  SOLANA_GENESIS_VALIDATOR_BALANCE_LAMPORTS,
  SOLANA_GENESIS_CORE_VALIDATORS, SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS,
  SOLANA_TPU_DISABLE_QUIC, SOLANA_TPU_ENABLE_UDP,
  SOLANA_AGAVE_LOW_MEMORY_PROFILE, SOLANA_AGAVE_LOW_MEMORY_LEVEL,
  SOLANA_OBSERVER_BATCH_SIZE,
  SOLANA_OBSERVER_JOBS, SOLANA_OBSERVER_BATCH_DELAY,
  SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT, SOLANA_NOFILE,
  SOLANA_DISABLE_WARMUP_EPOCHS, SOLANA_TARGET_TICK_DURATION_MS,
  SOLANA_TICKS_PER_SLOT,
  SOLANA_GENESIS_CREATION_TIME,
  SLOT_STALL_TIMEOUT, SLOT_STALL_GRACE, SLOT_STALL_CHECK_INTERVAL,
      ACTIVE_STALL_TIMEOUT, ACTIVE_STALL_GRACE, POST_HEALTH_STABILIZE_SECONDS,
      STRICT_ACTIVE_SAMPLING,
      WAIT_FOR_LOCK, OUTPUT_DIR, COMPOSE_FILE, COMPOSE_PROJECT_NAME,
      RUN_PREFIX, PYTHON_BIN
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --validators)
            VALIDATORS="$2"; shift 2 ;;
        --stub-as-count)
            STUB_AS_COUNT="$2"; shift 2 ;;
        --duration)
            DURATION_SECONDS="$2"; shift 2 ;;
        --tx-rate)
            TX_RATE="$2"; shift 2 ;;
        --build-mode)
            BUILD_MODE="$2"; shift 2 ;;
        --staked-validators)
            SOLANA_STAKED_VALIDATORS="$2"; shift 2 ;;
        --genesis-voting-validators)
            SOLANA_GENESIS_VOTING_VALIDATORS=1
            shift ;;
        --disable-validator-stake)
            SOLANA_ENABLE_VALIDATOR_STAKE=0
            SOLANA_STAKED_VALIDATORS=0
            shift ;;
        --skip-base-build)
            SKIP_BASE_BUILD=1; shift ;;
        --skip-build)
            SKIP_BUILD=1; shift ;;
        --build-only)
            BUILD_ONLY=1; shift ;;
        --keep-last)
            KEEP_LAST=1; shift ;;
        --keep-on-failure)
            KEEP_ON_FAILURE=1; shift ;;
        --dry-run)
            DRY_RUN=1; shift ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for value_name in VALIDATORS STUB_AS_COUNT DURATION_SECONDS MEMORY_INTERVAL BLOCK_INTERVAL ACTIVE_INTERVAL GOSSIP_INTERVAL SOLANA_METRICS_RPC_TIMEOUT HEALTH_TIMEOUT SLOT_RETRIES HEALTH_DOCKER_TIMEOUT SOLANA_LOG_TAIL SOLANA_SLOT_LOG_TAIL MIN_VOTING_VALIDATORS MIN_ACTIVE_SOLANA MIN_GOSSIP_NODES ACTIVE_WAIT ACTIVE_CONFIRM_RETRIES ACTIVE_CONFIRM_DELAY TX_CHECK_TIMEOUT TX_SUBMIT_TIMEOUT BUILD_BATCH_SIZE BUILD_JOBS BUILD_RETRY UP_PHASE_DELAY CORE_HEALTH_TIMEOUT CORE_ACTIVE_WAIT CORE_SLOT_RETRIES CORE_TX_CHECK_TIMEOUT CORE_STABILIZE_SECONDS CORE_STABILIZE_CHECK_INTERVAL SOLANA_SLOTS_PER_EPOCH SOLANA_TARGET_TICK_DURATION_MS SOLANA_TICKS_PER_SLOT SOLANA_VALIDATOR_STAKE_SOL SOLANA_OBSERVER_BATCH_SIZE SOLANA_OBSERVER_JOBS SOLANA_OBSERVER_BATCH_DELAY SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT SOLANA_NOFILE SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS SOLANA_GENESIS_BOOTSTRAP_BALANCE_LAMPORTS SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS SOLANA_GENESIS_VALIDATOR_BALANCE_LAMPORTS SOLANA_GENESIS_CORE_VALIDATORS SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS SOLANA_GENESIS_START_BARRIER_VALIDATORS SLOT_STALL_TIMEOUT SLOT_STALL_GRACE SLOT_STALL_CHECK_INTERVAL ACTIVE_STALL_TIMEOUT ACTIVE_STALL_GRACE POST_HEALTH_STABILIZE_SECONDS WAIT_FOR_LOAD_BELOW LOAD_WAIT_TIMEOUT LOAD_WAIT_INTERVAL HOST_MEMORY_MIN_AVAILABLE_GIB HOST_MEMORY_CHECK_INTERVAL; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done
if [[ -n "$MAX_DELINQUENT_VALIDATORS" && ! "$MAX_DELINQUENT_VALIDATORS" =~ ^[0-9]+$ ]]; then
    echo "MAX_DELINQUENT_VALIDATORS must be empty or a non-negative integer." >&2
    exit 2
fi

if [[ "$VALIDATORS" -lt 1 || "$STUB_AS_COUNT" -lt 1 || "$MEMORY_INTERVAL" -lt 1 || "$BLOCK_INTERVAL" -lt 1 || "$ACTIVE_INTERVAL" -lt 1 || "$GOSSIP_INTERVAL" -lt 1 || "$SOLANA_METRICS_RPC_TIMEOUT" -lt 1 || "$CORE_STABILIZE_CHECK_INTERVAL" -lt 1 ]]; then
    echo "VALIDATORS, STUB_AS_COUNT, MEMORY_INTERVAL, BLOCK_INTERVAL, ACTIVE_INTERVAL, GOSSIP_INTERVAL, SOLANA_METRICS_RPC_TIMEOUT, and CORE_STABILIZE_CHECK_INTERVAL must be positive." >&2
    exit 2
fi
if [[ -z "$SOLANA_GENESIS_CREATION_TIME" ]]; then
    echo "SOLANA_GENESIS_CREATION_TIME cannot be empty." >&2
    exit 2
fi

if [[ "$REQUIRE_TX_HEALTH" != "0" && "$REQUIRE_TX_HEALTH" != "1" ]]; then
    echo "REQUIRE_TX_HEALTH must be 0 or 1." >&2
    exit 2
fi
if [[ "$STRICT_VALIDATOR_STATUS" != "0" && "$STRICT_VALIDATOR_STATUS" != "1" ]]; then
    echo "STRICT_VALIDATOR_STATUS must be 0 or 1." >&2
    exit 2
fi
if [[ "$ACTIVE_COUNT_MODE" != "process-scan" && "$ACTIVE_COUNT_MODE" != "running" && "$ACTIVE_COUNT_MODE" != "docker-top" ]]; then
    echo "ACTIVE_COUNT_MODE must be process-scan, running, or docker-top." >&2
    exit 2
fi
if [[ "$CORE_REQUIRE_TX_HEALTH" != "0" && "$CORE_REQUIRE_TX_HEALTH" != "1" ]]; then
    echo "CORE_REQUIRE_TX_HEALTH must be 0 or 1." >&2
    exit 2
fi
if [[ "$STRICT_ACTIVE_SAMPLING" != "0" && "$STRICT_ACTIVE_SAMPLING" != "1" ]]; then
    echo "STRICT_ACTIVE_SAMPLING must be 0 or 1." >&2
    exit 2
fi
if [[ "$KEEP_ON_FAILURE" != "0" && "$KEEP_ON_FAILURE" != "1" ]]; then
    echo "KEEP_ON_FAILURE must be 0 or 1." >&2
    exit 2
fi
if [[ "$CLEAN_D61_SOLANA_DOCKER" != "0" && "$CLEAN_D61_SOLANA_DOCKER" != "1" ]]; then
    echo "CLEAN_D61_SOLANA_DOCKER must be 0 or 1." >&2
    exit 2
fi
if [[ "$CAPTURE_FINAL_DIAGNOSTICS" != "0" && "$CAPTURE_FINAL_DIAGNOSTICS" != "1" ]]; then
    echo "CAPTURE_FINAL_DIAGNOSTICS must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_TWO_STAGE_STARTUP" != "0" && "$SOLANA_TWO_STAGE_STARTUP" != "1" ]]; then
    echo "SOLANA_TWO_STAGE_STARTUP must be 0 or 1." >&2
    exit 2
fi

if [[ "$SOLANA_ENABLE_VALIDATOR_STAKE" != "0" && "$SOLANA_ENABLE_VALIDATOR_STAKE" != "1" ]]; then
    echo "SOLANA_ENABLE_VALIDATOR_STAKE must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_NON_STAKED_NO_VOTING" != "0" && "$SOLANA_NON_STAKED_NO_VOTING" != "1" ]]; then
    echo "SOLANA_NON_STAKED_NO_VOTING must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_SPLIT_VALIDATOR_PHASES" != "0" && "$SOLANA_SPLIT_VALIDATOR_PHASES" != "1" ]]; then
    echo "SOLANA_SPLIT_VALIDATOR_PHASES must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_GENESIS_VOTING_VALIDATORS" != "0" && "$SOLANA_GENESIS_VOTING_VALIDATORS" != "1" ]]; then
    echo "SOLANA_GENESIS_VOTING_VALIDATORS must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_GENESIS_STAGED_STARTUP" != "0" && "$SOLANA_GENESIS_STAGED_STARTUP" != "1" ]]; then
    echo "SOLANA_GENESIS_STAGED_STARTUP must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_COMBINED_STARTUP" != "0" && "$SOLANA_COMBINED_STARTUP" != "1" ]]; then
    echo "SOLANA_COMBINED_STARTUP must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_DISABLE_WARMUP_EPOCHS" != "0" && "$SOLANA_DISABLE_WARMUP_EPOCHS" != "1" ]]; then
    echo "SOLANA_DISABLE_WARMUP_EPOCHS must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_TPU_DISABLE_QUIC" != "0" && "$SOLANA_TPU_DISABLE_QUIC" != "1" ]]; then
    echo "SOLANA_TPU_DISABLE_QUIC must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_TPU_ENABLE_UDP" != "0" && "$SOLANA_TPU_ENABLE_UDP" != "1" ]]; then
    echo "SOLANA_TPU_ENABLE_UDP must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_AGAVE_LOW_MEMORY_PROFILE" != "0" && "$SOLANA_AGAVE_LOW_MEMORY_PROFILE" != "1" ]]; then
    echo "SOLANA_AGAVE_LOW_MEMORY_PROFILE must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_AGAVE_LOW_MEMORY_LEVEL" != "standard" && "$SOLANA_AGAVE_LOW_MEMORY_LEVEL" != "aggressive" && "$SOLANA_AGAVE_LOW_MEMORY_LEVEL" != "ultra" ]]; then
    echo "SOLANA_AGAVE_LOW_MEMORY_LEVEL must be standard, aggressive, or ultra." >&2
    exit 2
fi
if [[ ! "$SOLANA_STAKED_VALIDATORS" =~ ^-?[0-9]+$ ]] || (( SOLANA_STAKED_VALIDATORS < -1 )); then
    echo "SOLANA_STAKED_VALIDATORS must be -1 or a non-negative integer." >&2
    exit 2
fi
if [[ "$SOLANA_GENESIS_VOTING_VALIDATORS" == "1" ]]; then
    SOLANA_ENABLE_VALIDATOR_STAKE=0
    SOLANA_NON_STAKED_NO_VOTING=0
    if [[ "$SOLANA_GENESIS_STAGED_STARTUP" == "1" ]]; then
        if (( SOLANA_GENESIS_CORE_VALIDATORS < 1 || SOLANA_GENESIS_CORE_VALIDATORS > VALIDATORS - 1 )); then
            echo "SOLANA_GENESIS_STAGED_STARTUP=1 requires 1 <= SOLANA_GENESIS_CORE_VALIDATORS <= VALIDATORS-1." >&2
            exit 2
        fi
        SOLANA_STAKED_VALIDATORS="$SOLANA_GENESIS_CORE_VALIDATORS"
        SOLANA_SPLIT_VALIDATOR_PHASES=1
        SOLANA_TWO_STAGE_STARTUP=1
        SOLANA_COMBINED_STARTUP=0
        if (( SOLANA_GENESIS_START_BARRIER_VALIDATORS == 0 )); then
            SOLANA_GENESIS_START_BARRIER_VALIDATORS=$((1 + SOLANA_GENESIS_CORE_VALIDATORS))
        fi
    else
        SOLANA_STAKED_VALIDATORS=0
        SOLANA_SPLIT_VALIDATOR_PHASES=0
        SOLANA_TWO_STAGE_STARTUP=0
    fi
    if (( SOLANA_GENESIS_START_BARRIER_VALIDATORS > VALIDATORS )); then
        echo "SOLANA_GENESIS_START_BARRIER_VALIDATORS cannot exceed VALIDATORS." >&2
        exit 2
    fi
    if [[ -z "$STRICT_VALIDATOR_STATUS_SET" ]]; then
        STRICT_VALIDATOR_STATUS=0
    fi
    if [[ -z "$MIN_VOTING_VALIDATORS_SET" ]]; then
        MIN_VOTING_VALIDATORS=0
    fi
    if [[ -z "$MAX_DELINQUENT_VALIDATORS_SET" ]]; then
        MAX_DELINQUENT_VALIDATORS=""
    fi
    if [[ -z "$UP_PHASE_DELAY_SET" ]]; then
        UP_PHASE_DELAY=0
    fi
    if [[ -z "$STRICT_ACTIVE_SAMPLING_SET" ]]; then
        STRICT_ACTIVE_SAMPLING=1
    fi
    if [[ -z "$SLOT_STALL_TIMEOUT_SET" ]]; then
        SLOT_STALL_TIMEOUT=600
    fi
fi
if [[ "$SOLANA_ENABLE_VALIDATOR_STAKE" == "0" && ! ( "$SOLANA_GENESIS_VOTING_VALIDATORS" == "1" && "$SOLANA_GENESIS_STAGED_STARTUP" == "1" ) ]]; then
    SOLANA_STAKED_VALIDATORS=0
fi
if [[ "$SOLANA_TWO_STAGE_STARTUP" == "1" && "$SOLANA_SPLIT_VALIDATOR_PHASES" != "1" ]]; then
    echo "SOLANA_TWO_STAGE_STARTUP=1 requires SOLANA_SPLIT_VALIDATOR_PHASES=1." >&2
    exit 2
fi

case "$BUILD_MODE" in
    nobuild-mount|docker-build) ;;
    *)
        echo "BUILD_MODE must be nobuild-mount or docker-build." >&2
        exit 2
        ;;
esac

if [[ -z "${RUN_PREFIX:-}" ]]; then
    RUN_PREFIX="$(date +%Y%m%d-%H%M%S)-solana-v${VALIDATORS}"
fi
RUN_PREFIX="${RUN_PREFIX#"${RUN_PREFIX%%[![:space:]]*}"}"
RUN_PREFIX="${RUN_PREFIX%"${RUN_PREFIX##*[![:space:]]}"}"
if [[ -z "$RUN_PREFIX" ]]; then
    echo "RUN_PREFIX cannot be empty." >&2
    exit 2
fi
RUN_DIR="$SOLANA_DIR/experiments/$RUN_PREFIX"
mkdir -p "$RUN_DIR"

LOCK_FILE="${LOCK_FILE:-$SOLANA_DIR/.solana-large-experiment.lock}"
exec 9>"$LOCK_FILE"
if [[ "$WAIT_FOR_LOCK" == "1" ]]; then
    flock 9
elif ! flock -n 9; then
    echo "Another Solana large experiment is running. Set WAIT_FOR_LOCK=1 to wait, or stop the old run before starting a new one." >&2
    exit 3
fi

if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="$RUN_DIR/output"
fi
if [[ -z "$COMPOSE_FILE" ]]; then
    COMPOSE_FILE="$OUTPUT_DIR/docker-compose.yml"
fi

sanitize_compose_project() {
    local value="$1"
    value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-')"
    value="${value##[-_]}"
    value="${value%%[-_]}"
    if [[ -z "$value" || ! "$value" =~ ^[a-z0-9] ]]; then
        value="solana-$value"
    fi
    printf '%s\n' "$value"
}

COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(sanitize_compose_project "$RUN_PREFIX")}"
export COMPOSE_PROJECT_NAME

log() {
    printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$RUN_DIR/run.log"
}

run_cmd() {
    log "+ $*"
    if [[ "$DRY_RUN" -eq 0 ]]; then
        "$@"
    fi
}

stop_pid() {
    local pid="$1"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
}

cleanup_bg() {
    stop_pid "$CURRENT_HOST_MEMORY_PID"
    CURRENT_HOST_MEMORY_PID=""
    stop_pid "$CURRENT_TX_PID"
    CURRENT_TX_PID=""
    stop_pid "$CURRENT_ACTIVE_PID"
    CURRENT_ACTIVE_PID=""
    stop_pid "$CURRENT_GOSSIP_PID"
    CURRENT_GOSSIP_PID=""
    stop_pid "$CURRENT_BLOCK_PID"
    CURRENT_BLOCK_PID=""
    stop_pid "$CURRENT_COLLECTOR_PID"
    CURRENT_COLLECTOR_PID=""
}

start_host_memory_watchdog() {
    local data_dir="$1"
    local threshold_gib="$HOST_MEMORY_MIN_AVAILABLE_GIB"
    local interval="$HOST_MEMORY_CHECK_INTERVAL"
    local parent_pid="$$"

    log "starting host memory sampler min_available=${threshold_gib}GiB interval=${interval}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi

    "$SCRIPT_DIR/collect_host_memory.sh" \
        --output "$data_dir/host_memory.csv" \
        --interval "$interval" \
        --threshold-gib "$threshold_gib" \
        --trigger-output "$data_dir/host-memory-watchdog-triggered.env" \
        --event-log "$data_dir/host-memory-watchdog.log" \
        --parent-pid "$parent_pid" \
        > "$data_dir/host-memory-sampler.log" 2>&1 &
    CURRENT_HOST_MEMORY_PID="$!"
    echo "$CURRENT_HOST_MEMORY_PID" > "$data_dir/host-memory-watchdog.pid"
}

cleanup_orphan_solana_processes() {
    local pids=()
    local remaining=()

    mapfile -t pids < <(
        ps -e -o pid=,args= 2>/dev/null \
            | awk '/agave-validator --identity \/opt\/solana\/config/ {print $1}'
    )
    if [[ "${#pids[@]}" -eq 0 ]]; then
        return
    fi

    log "terminating orphan agave-validator processes: ${#pids[@]}"
    kill "${pids[@]}" 2>/dev/null || true
    sleep 2

    mapfile -t remaining < <(
        ps -e -o pid=,args= 2>/dev/null \
            | awk '/agave-validator --identity \/opt\/solana\/config/ {print $1}'
    )
    if [[ "${#remaining[@]}" -gt 0 ]]; then
        log "force killing orphan agave-validator processes: ${#remaining[@]}"
        kill -9 "${remaining[@]}" 2>/dev/null || true
    fi
}

compose_down() {
    if [[ -f "$COMPOSE_FILE" ]]; then
        local attempt
        local ids=()
        for attempt in 1 2 3 4 5; do
            run_cmd timeout "$CLEANUP_DOCKER_TIMEOUT" docker compose -p "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" down -v --remove-orphans || true
            if [[ "$DRY_RUN" -eq 1 ]]; then
                return
            fi
            sleep 2
            mapfile -t ids < <(
                timeout "$HEALTH_DOCKER_TIMEOUT" docker ps -aq \
                    --filter "label=com.docker.compose.project.config_files=$COMPOSE_FILE" \
                    2>/dev/null || true
            )
            if [[ "${#ids[@]}" -eq 0 ]]; then
                cleanup_orphan_solana_processes
                return
            fi
            log "compose down retry needed: remaining_containers=${#ids[@]} attempt=$attempt"
        done

        mapfile -t ids < <(
            timeout "$HEALTH_DOCKER_TIMEOUT" docker ps -aq \
                --filter "label=com.docker.compose.project.config_files=$COMPOSE_FILE" \
                2>/dev/null || true
        )
        if [[ "${#ids[@]}" -gt 0 ]]; then
            log "force removing remaining compose containers: ${#ids[@]}"
            docker rm -f "${ids[@]}" >/dev/null 2>&1 || true
        fi
        cleanup_orphan_solana_processes
    fi
}

cleanup_stale_d61_solana_docker() {
    local project
    local raw_project_file
    local projects=()
    local project_file
    local container_ids=()
    local network_ids=()

    if [[ "$CLEAN_D61_SOLANA_DOCKER" -ne 1 || "$DRY_RUN" -eq 1 ]]; then
        return
    fi

    raw_project_file="$(mktemp)"
    project_file="$(mktemp)"
    {
        timeout "$HEALTH_DOCKER_TIMEOUT" docker network ls \
            --filter "label=com.docker.compose.project" \
            --format '{{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.project.config_files"}}' \
            2>/dev/null || true
        timeout "$HEALTH_DOCKER_TIMEOUT" docker ps -a \
            --filter "label=com.docker.compose.project" \
            --format '{{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.project.config_files"}}' \
            2>/dev/null || true
    } > "$raw_project_file"
    "$PYTHON_BIN" - "$raw_project_file" "$SOLANA_DIR/" "$COMPOSE_PROJECT_NAME" "$CLEAN_D61_SOLANA_PROJECT_REGEX" > "$project_file" <<'PY'
import re
import sys
from pathlib import Path

raw_file = Path(sys.argv[1])
solana_dir = sys.argv[2]
current_project = sys.argv[3]
project_regex = re.compile(sys.argv[4])
projects = set()

for raw_line in raw_file.read_text(errors="ignore").splitlines():
    line = raw_line.strip()
    if not line:
        continue
    parts = line.split(maxsplit=1)
    project = parts[0]
    config_files = parts[1] if len(parts) > 1 else ""
    if not project or project == current_project:
        continue
    if solana_dir in config_files or project_regex.search(project):
        projects.add(project)

for project in sorted(projects):
    print(project)
PY
    mapfile -t projects < "$project_file"
    rm -f "$raw_project_file" "$project_file"
    if [[ "${#projects[@]}" -eq 0 ]]; then
        return
    fi

    log "cleaning stale D61 Solana compose projects before new run: ${projects[*]}"
    for project in "${projects[@]}"; do
        [[ -n "$project" ]] || continue
        mapfile -t container_ids < <(
            timeout "$HEALTH_DOCKER_TIMEOUT" docker ps -aq \
                --filter "label=com.docker.compose.project=$project" \
                2>/dev/null || true
        )
        if [[ "${#container_ids[@]}" -gt 0 ]]; then
            log "removing stale containers project=$project count=${#container_ids[@]}"
            timeout "$CLEANUP_DOCKER_TIMEOUT" docker rm -f "${container_ids[@]}" >/dev/null 2>&1 || true
        fi

        mapfile -t network_ids < <(
            timeout "$HEALTH_DOCKER_TIMEOUT" docker network ls -q \
                --filter "label=com.docker.compose.project=$project" \
                2>/dev/null || true
        )
        if [[ "${#network_ids[@]}" -gt 0 ]]; then
            log "removing stale networks project=$project count=${#network_ids[@]}"
            timeout "$CLEANUP_DOCKER_TIMEOUT" docker network rm "${network_ids[@]}" >/dev/null 2>&1 || true
        fi
    done
}

compose_container_names() {
    "$PYTHON_BIN" - "$COMPOSE_FILE" <<'PY'
import sys
from pathlib import Path

for raw_line in Path(sys.argv[1]).read_text(errors="ignore").splitlines():
    line = raw_line.strip()
    if line.startswith("container_name:"):
        print(line.split(":", 1)[1].strip().strip("'\""))
PY
}

on_exit() {
    local status=$?
    cleanup_bg || true
    if [[ "$COMPOSE_DOWN_ON_EXIT" -eq 1 ]]; then
        if [[ "$status" -ne 0 && "$KEEP_ON_FAILURE" -eq 1 ]]; then
            log "KEEP_ON_FAILURE=1: leaving compose project running after failure"
            COMPOSE_DOWN_ON_EXIT=0
        else
            compose_down || true
        fi
    fi
    exit "$status"
}

on_signal() {
    log "received termination signal; cleaning up compose project"
    cleanup_bg || true
    compose_down || true
    COMPOSE_DOWN_ON_EXIT=0
    log "large Solana experiment failed: received termination signal"
    exit 130
}

trap on_exit EXIT
trap on_signal INT TERM

build_proxy_args() {
    local proxy_var
    for proxy_var in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; do
        if [[ -n "${!proxy_var:-}" ]]; then
            printf '%s\n' --build-arg "$proxy_var"
        fi
    done
}

build_base_image() {
    if [[ "$SKIP_BASE_BUILD" -eq 1 ]]; then
        log "SKIP_BASE_BUILD=1: seedemu-solana image build skipped"
        return
    fi
    local args=()
    if [[ -n "$DOCKER_BUILD_NETWORK" ]]; then
        args+=(--network "$DOCKER_BUILD_NETWORK")
    fi
    while IFS= read -r item; do
        args+=("$item")
    done < <(build_proxy_args)
    run_cmd docker build --platform "$DOCKER_PLATFORM" "${args[@]}" \
        -t seedemu-solana "$REPO_ROOT/docker_images/seedemu-solana"
}

generate_output() {
    local data_dir="$1"
    local cmd=(
        "$PYTHON_BIN" "$SRC_DIR/solana_large.py"
        --validators "$VALIDATORS"
        --stub-as-count "$STUB_AS_COUNT"
        --output "$OUTPUT_DIR"
        --platform "$PLATFORM"
    )
    if [[ "$INTERNET_MAP" -eq 0 ]]; then
        cmd+=(--no-internet-map)
    fi

    run_cmd rm -rf "$OUTPUT_DIR"
    run_cmd "${cmd[@]}"
    local patch_cmd=(
        "$PYTHON_BIN" "$SCRIPT_DIR/patch_solana_output.py"
        --output "$OUTPUT_DIR"
        --slots-per-epoch "$SOLANA_SLOTS_PER_EPOCH"
        --target-tick-duration-ms "$SOLANA_TARGET_TICK_DURATION_MS"
        --ticks-per-slot "$SOLANA_TICKS_PER_SLOT"
        --validator-stake-sol "$SOLANA_VALIDATOR_STAKE_SOL"
        --staked-validators "$SOLANA_STAKED_VALIDATORS"
        --nofile "$SOLANA_NOFILE"
        --genesis-creation-time "$SOLANA_GENESIS_CREATION_TIME"
        --genesis-bootstrap-stake-lamports "$SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS"
        --genesis-bootstrap-balance-lamports "$SOLANA_GENESIS_BOOTSTRAP_BALANCE_LAMPORTS"
        --genesis-validator-stake-lamports "$SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS"
        --genesis-validator-balance-lamports "$SOLANA_GENESIS_VALIDATOR_BALANCE_LAMPORTS"
        --genesis-core-validator-count "$SOLANA_GENESIS_CORE_VALIDATORS"
        --genesis-core-validator-stake-lamports "$SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS"
    )
    if [[ "$SOLANA_GENESIS_START_BARRIER_VALIDATORS" -gt 0 ]]; then
        patch_cmd+=(--genesis-start-barrier-validators "$SOLANA_GENESIS_START_BARRIER_VALIDATORS")
    fi
    if [[ "$SOLANA_NON_STAKED_NO_VOTING" -eq 1 ]]; then
        patch_cmd+=(--non-staked-no-voting)
    fi
    if [[ "$SOLANA_GENESIS_VOTING_VALIDATORS" -eq 1 ]]; then
        patch_cmd+=(--genesis-voting-validators)
    fi
    if [[ "$SOLANA_DISABLE_WARMUP_EPOCHS" -eq 1 ]]; then
        patch_cmd+=(--disable-warmup-epochs)
    fi
    if [[ "$SOLANA_TPU_DISABLE_QUIC" -eq 1 ]]; then
        patch_cmd+=(--tpu-disable-quic)
    fi
    if [[ "$SOLANA_TPU_ENABLE_UDP" -eq 1 ]]; then
        patch_cmd+=(--tpu-enable-udp)
    fi
    if [[ "$SOLANA_AGAVE_LOW_MEMORY_PROFILE" -eq 1 ]]; then
        patch_cmd+=(--agave-low-memory-profile)
        patch_cmd+=(--agave-low-memory-level "$SOLANA_AGAVE_LOW_MEMORY_LEVEL")
    fi
    run_cmd "${patch_cmd[@]}"

    if [[ "$DRY_RUN" -eq 0 && "$SOLANA_GENESIS_VOTING_VALIDATORS" == "1" ]]; then
        "$PYTHON_BIN" - "$OUTPUT_DIR/solana-output-patch-summary.json" "$VALIDATORS" "$SOLANA_SLOTS_PER_EPOCH" "$SOLANA_DISABLE_WARMUP_EPOCHS" "$OUTPUT_DIR" "$SOLANA_GENESIS_START_BARRIER_VALIDATORS" "$SOLANA_TARGET_TICK_DURATION_MS" "$SOLANA_TICKS_PER_SLOT" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text())
validators = int(sys.argv[2])
slots_per_epoch = int(sys.argv[3])
disable_warmup = sys.argv[4] == "1"
output_dir = Path(sys.argv[5])
expected_barrier = int(sys.argv[6])
target_tick_duration_ms = int(sys.argv[7])
ticks_per_slot = int(sys.argv[8])
if expected_barrier == 0:
    expected_barrier = validators
non_bootstrap = max(0, validators - 1)
actual_total = int(summary.get("genesis_voting_validators", 0))
actual_scripts = int(summary.get("genesis_voting_validator_script_updates", 0))
actual_bootstrap = int(summary.get("bootstrap_genesis_voting_script_updates", 0))
actual_slots = int(summary.get("slots_per_epoch", 0))
actual_disable_warmup = bool(summary.get("disable_warmup_epochs", False))
actual_target_tick_duration_ms = int(summary.get("target_tick_duration_ms", 0))
actual_ticks_per_slot = int(summary.get("ticks_per_slot", 0))
actual_barrier = int(summary.get("genesis_start_barrier_validators", 0))
if actual_total != validators or actual_scripts != non_bootstrap or actual_bootstrap != 1:
    raise SystemExit(
        "genesis voting patch mismatch: "
        f"expected total={validators}, non_bootstrap_scripts={non_bootstrap}, bootstrap=1; "
        f"actual total={actual_total}, scripts={actual_scripts}, bootstrap={actual_bootstrap}. "
        "Refusing to run a mislabeled all-voting experiment."
    )
if actual_slots != slots_per_epoch or actual_disable_warmup != disable_warmup:
    raise SystemExit(
        "genesis epoch patch summary mismatch: "
        f"expected slots={slots_per_epoch}, disable_warmup={disable_warmup}; "
        f"actual slots={actual_slots}, disable_warmup={actual_disable_warmup}."
    )
if actual_target_tick_duration_ms != target_tick_duration_ms or actual_ticks_per_slot != ticks_per_slot:
    raise SystemExit(
        "genesis tick patch summary mismatch: "
        f"expected target_tick_duration_ms={target_tick_duration_ms}, ticks_per_slot={ticks_per_slot}; "
        f"actual target_tick_duration_ms={actual_target_tick_duration_ms}, ticks_per_slot={actual_ticks_per_slot}."
    )
if actual_barrier != expected_barrier:
    raise SystemExit(
        "genesis start barrier patch summary mismatch: "
        f"expected barrier={expected_barrier}; actual barrier={actual_barrier}."
    )
bootstrap_scripts = []
for path in output_dir.glob("hnode_*/*"):
    if not path.is_file():
        continue
    text = path.read_text(errors="ignore")
    if "# seedemu-solana: genesis / bootstrap validator" in text:
        bootstrap_scripts.append((path, text))
if len(bootstrap_scripts) != 1:
    raise SystemExit(f"expected exactly one bootstrap genesis script, found {len(bootstrap_scripts)}")
bootstrap_path, bootstrap_text = bootstrap_scripts[0]
if f"--slots-per-epoch {slots_per_epoch}" not in bootstrap_text:
    raise SystemExit(f"bootstrap genesis script lacks --slots-per-epoch {slots_per_epoch}: {bootstrap_path}")
if target_tick_duration_ms > 0 and f"--target-tick-duration {target_tick_duration_ms}" not in bootstrap_text:
    raise SystemExit(
        f"bootstrap genesis script lacks --target-tick-duration {target_tick_duration_ms}: {bootstrap_path}"
    )
if ticks_per_slot > 0 and f"--ticks-per-slot {ticks_per_slot}" not in bootstrap_text:
    raise SystemExit(f"bootstrap genesis script lacks --ticks-per-slot {ticks_per_slot}: {bootstrap_path}")
if disable_warmup and "--enable-warmup-epochs" in bootstrap_text:
    raise SystemExit(f"bootstrap genesis script still enables warmup epochs: {bootstrap_path}")
PY
    fi

    if [[ "$DRY_RUN" -eq 0 && "$SOLANA_GENESIS_VOTING_VALIDATORS" != "1" && "$SOLANA_STAKED_VALIDATORS" != "0" ]]; then
        "$PYTHON_BIN" - "$OUTPUT_DIR/solana-output-patch-summary.json" "$SOLANA_STAKED_VALIDATORS" "$VALIDATORS" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text())
requested = int(sys.argv[2])
validators = int(sys.argv[3])
expected = validators - 1 if requested < 0 else min(requested, validators - 1)
actual = int(summary.get("validator_script_updates", 0))
if actual != expected:
    raise SystemExit(
        f"validator stake patch mismatch: expected {expected}, actual {actual}. "
        "Refusing to run a mislabeled stake experiment."
    )
PY
    fi

    if [[ "$DRY_RUN" -eq 0 && "$SOLANA_GENESIS_VOTING_VALIDATORS" != "1" && "$SOLANA_NON_STAKED_NO_VOTING" == "1" ]]; then
        "$PYTHON_BIN" - "$OUTPUT_DIR/solana-output-patch-summary.json" "$SOLANA_STAKED_VALIDATORS" "$VALIDATORS" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text())
requested = int(sys.argv[2])
validators = int(sys.argv[3])
non_bootstrap = max(0, validators - 1)
staked = non_bootstrap if requested < 0 else min(requested, non_bootstrap)
expected = max(0, non_bootstrap - staked)
actual = int(summary.get("non_staked_no_voting_updates", 0))
if actual != expected:
    raise SystemExit(
        f"non-staked no-voting patch mismatch: expected {expected}, actual {actual}. "
        "Refusing to run a mislabeled observer experiment."
    )
PY
    fi

    if [[ "$BUILD_MODE" == "nobuild-mount" ]]; then
        run_cmd "$PYTHON_BIN" "$SCRIPT_DIR/patch_output_to_runtime_mounts.py" --output "$OUTPUT_DIR"
    fi

    if [[ "$DRY_RUN" -eq 0 ]]; then
        docker compose -f "$COMPOSE_FILE" config --services > "$data_dir/services.txt"
        cp "$COMPOSE_FILE" "$data_dir/docker-compose.yml"
        for name in docker-compose.build.yml nobuild-mount-summary.json solana-output-patch-summary.json; do
            if [[ -f "$OUTPUT_DIR/$name" ]]; then
                cp "$OUTPUT_DIR/$name" "$data_dir/$name"
            fi
        done
    fi
}

wait_for_health() {
    local data_dir="$1"
    local label="${2:-health}"
    local expected_validators="${3:-$VALIDATORS}"
    local min_running="${4:-$VALIDATORS}"
    local min_active="${5:-$MIN_ACTIVE_SOLANA}"
    local active_wait="${6:-$ACTIVE_WAIT}"
    local min_voting="${7:-$MIN_VOTING_VALIDATORS}"
    local require_tx="${8:-$REQUIRE_TX_HEALTH}"
    local slot_retries="${9:-$SLOT_RETRIES}"
    local tx_timeout="${10:-$TX_CHECK_TIMEOUT}"
    local max_wait="${11:-$HEALTH_TIMEOUT}"
    local max_delinquent="$MAX_DELINQUENT_VALIDATORS"
    local health_log="$data_dir/${label}-health-passed.log"
    local failed_log="$data_dir/${label}-health-failed.log"
    local tmp_log="$data_dir/${label}-health-running.log"
    local boot_container_name

    if (( $# >= 12 )); then
        max_delinquent="${12}"
    fi

    boot_container_name="$(bootstrap_container_name || true)"

    log "running Solana health check label=$label expected=$expected_validators min_running=$min_running min_active=$min_active"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    if EXPECTED_VALIDATORS="$expected_validators" \
        MIN_RUNNING_SOLANA="$min_running" \
        MIN_ACTIVE_SOLANA="$min_active" \
        ACTIVE_WAIT="$active_wait" \
        ACTIVE_COUNT_MODE="$ACTIVE_COUNT_MODE" \
        SKIP_DOCKER_RUNNING_COUNT="$SKIP_DOCKER_RUNNING_COUNT" \
        BOOT_CONTAINER_NAME="$boot_container_name" \
        PYTHON_BIN="$PYTHON_BIN" \
        MIN_VOTING_VALIDATORS="$min_voting" \
        MAX_DELINQUENT_VALIDATORS="$max_delinquent" \
        STRICT_VALIDATOR_STATUS="$STRICT_VALIDATOR_STATUS" \
        REQUIRE_TX_HEALTH="$require_tx" \
        TX_CHECK_TIMEOUT="$tx_timeout" \
        MAX_WAIT="$max_wait" \
        SLOT_RETRIES="$slot_retries" \
        DOCKER_TIMEOUT="$HEALTH_DOCKER_TIMEOUT" \
        "$SCRIPT_DIR/check_solana_health.sh" > "$tmp_log" 2>&1; then
        mv "$tmp_log" "$health_log"
        rm -f "$failed_log"
        log "health check passed label=$label"
        return 0
    fi
    mv "$tmp_log" "$failed_log" 2>/dev/null || true
    rm -f "$health_log"
    tail -n 80 "$failed_log" || true
    capture_solana_logs "$data_dir"
    capture_solana_diagnostics "$data_dir"
    return 1
}

bootstrap_container_name() {
    "$PYTHON_BIN" - "$COMPOSE_FILE" <<'PY'
import sys
from pathlib import Path

compose = Path(sys.argv[1])
current = ""
for raw_line in compose.read_text(errors="ignore").splitlines():
    line = raw_line.strip()
    if line.startswith("container_name:"):
        current = line.split(":", 1)[1].strip().strip("'\"")
    if "Solana-Bootstrap-000" in line and current:
        print(current)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

staked_non_bootstrap_count() {
    local non_bootstrap=$((VALIDATORS - 1))
    if (( non_bootstrap < 0 )); then
        non_bootstrap=0
    fi
    if (( SOLANA_STAKED_VALIDATORS < 0 )); then
        printf '%s\n' "$non_bootstrap"
    elif (( SOLANA_STAKED_VALIDATORS > non_bootstrap )); then
        printf '%s\n' "$non_bootstrap"
    else
        printf '%s\n' "$SOLANA_STAKED_VALIDATORS"
    fi
}

core_validator_count() {
    local staked
    staked="$(staked_non_bootstrap_count)"
    printf '%s\n' "$((1 + staked))"
}

up_phase() {
    local phase="$1"
    local log_dir="$2"
    mkdir -p "$log_dir"
    run_cmd env LOG_DIR="$log_dir" \
        COMPOSE_FILE="$COMPOSE_FILE" \
        PHASE_DELAY=0 \
        SOLANA_STAKED_VALIDATORS="$SOLANA_STAKED_VALIDATORS" \
        SOLANA_SPLIT_VALIDATOR_PHASES="$SOLANA_SPLIT_VALIDATOR_PHASES" \
        SOLANA_OBSERVER_BATCH_SIZE="$SOLANA_OBSERVER_BATCH_SIZE" \
        SOLANA_OBSERVER_JOBS="$SOLANA_OBSERVER_JOBS" \
        SOLANA_OBSERVER_BATCH_DELAY="$SOLANA_OBSERVER_BATCH_DELAY" \
        SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT="$SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT" \
        "$SCRIPT_DIR/batch_parallel_up.sh" --preset "$UP_PRESET" --phase "$phase"
    if [[ "$DRY_RUN" -eq 0 && -f "$log_dir/summary.log" ]]; then
        {
            printf '===== %s =====\n' "$phase"
            cat "$log_dir/summary.log"
            printf '\n'
        } >> "$UP_LOG_DIR/summary.log"
    fi
}

sleep_between_up_phases() {
    local reason="$1"
    if [[ "$DRY_RUN" -eq 0 && "$UP_PHASE_DELAY" -gt 0 ]]; then
        log "sleeping ${UP_PHASE_DELAY}s after $reason"
        sleep "$UP_PHASE_DELAY"
    fi
}

read_boot_slot() {
    local boot
    local slot
    boot="$(bootstrap_container_name || true)"
    [[ -n "$boot" ]] || return 1
    slot="$(timeout "$HEALTH_DOCKER_TIMEOUT" docker exec "$boot" solana --url http://127.0.0.1:8899 slot 2>/dev/null | tr -dc '0-9')"
    [[ -n "$slot" ]] || return 1
    printf '%s\n' "$slot"
}

stabilize_core_slots() {
    local data_dir="$1"
    local log_file="$data_dir/core-stabilize.log"
    local deadline
    local now
    local slot
    local last_slot=""

    if [[ "$CORE_STABILIZE_SECONDS" -eq 0 ]]; then
        log "core slot stabilization skipped: CORE_STABILIZE_SECONDS=0"
        return 0
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi

    log "stabilizing Solana core slots for ${CORE_STABILIZE_SECONDS}s interval=${CORE_STABILIZE_CHECK_INTERVAL}s"
    : > "$log_file"
    deadline=$(( $(date +%s) + CORE_STABILIZE_SECONDS ))
    while (( $(date +%s) < deadline )); do
        now="$(date -Is)"
        if ! slot="$(read_boot_slot)"; then
            {
                echo "timestamp=$now"
                echo "status=slot_read_failed"
            } >> "$log_file"
            log "core slot stabilization failed: could not read bootstrap slot"
            capture_solana_logs "$data_dir"
            return 1
        fi
        {
            echo "timestamp=$now"
            echo "slot=$slot"
            echo "last_slot=${last_slot:-}"
        } >> "$log_file"
        log "core stabilize slot=$slot last=${last_slot:-none}"
        if [[ -n "$last_slot" && "$slot" -le "$last_slot" ]]; then
            {
                echo "status=slot_stalled"
                echo "slot=$slot"
                echo "last_slot=$last_slot"
            } >> "$log_file"
            log "core slot stabilization failed: slot did not advance ($last_slot -> $slot)"
            capture_solana_logs "$data_dir"
            return 1
        fi
        last_slot="$slot"
        sleep "$CORE_STABILIZE_CHECK_INTERVAL"
    done
    log "core slot stabilization passed"
}

stabilize_full_network_after_health() {
    local data_dir="$1"
    if [[ "$POST_HEALTH_STABILIZE_SECONDS" -eq 0 ]]; then
        return 0
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi

    log "stabilizing full Solana network for ${POST_HEALTH_STABILIZE_SECONDS}s after final health"
    sleep "$POST_HEALTH_STABILIZE_SECONDS"
    wait_for_health "$data_dir" final-settle "$VALIDATORS" "$VALIDATORS" "$MIN_ACTIVE_SOLANA" "$ACTIVE_WAIT" "$MIN_VOTING_VALIDATORS" "$REQUIRE_TX_HEALTH" "$SLOT_RETRIES" "$TX_CHECK_TIMEOUT" "$HEALTH_TIMEOUT"
}

start_solana_network() {
    local data_dir="$1"
    local core_count
    local core_max_delinquent
    local core_min_voting
    core_count="$(core_validator_count)"
    core_max_delinquent="$MAX_DELINQUENT_VALIDATORS"
    core_min_voting=1

    if [[ "$SOLANA_TWO_STAGE_STARTUP" -eq 0 ]]; then
        run_cmd env LOG_DIR="$UP_LOG_DIR" \
            COMPOSE_FILE="$COMPOSE_FILE" \
            PHASE_DELAY="$UP_PHASE_DELAY" \
            SOLANA_STAKED_VALIDATORS="$SOLANA_STAKED_VALIDATORS" \
            SOLANA_SPLIT_VALIDATOR_PHASES="$SOLANA_SPLIT_VALIDATOR_PHASES" \
            SOLANA_COMBINED_STARTUP="$SOLANA_COMBINED_STARTUP" \
            SOLANA_OBSERVER_BATCH_SIZE="$SOLANA_OBSERVER_BATCH_SIZE" \
            SOLANA_OBSERVER_JOBS="$SOLANA_OBSERVER_JOBS" \
            SOLANA_OBSERVER_BATCH_DELAY="$SOLANA_OBSERVER_BATCH_DELAY" \
            SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT="$SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT" \
            "$SCRIPT_DIR/batch_parallel_up.sh" --preset "$UP_PRESET"
        wait_for_health "$data_dir" final "$VALIDATORS" "$VALIDATORS" "$MIN_ACTIVE_SOLANA" "$ACTIVE_WAIT" "$MIN_VOTING_VALIDATORS" "$REQUIRE_TX_HEALTH" "$SLOT_RETRIES" "$TX_CHECK_TIMEOUT" "$HEALTH_TIMEOUT"
        stabilize_full_network_after_health "$data_dir"
        return
    fi

    log "two-stage Solana startup enabled: core_validators=$core_count total_validators=$VALIDATORS"
    if [[ "$SOLANA_GENESIS_VOTING_VALIDATORS" == "1" && "$SOLANA_GENESIS_STAGED_STARTUP" == "1" ]]; then
        core_min_voting="$core_count"
        core_max_delinquent=""
        if [[ "$STRICT_VALIDATOR_STATUS" == "1" ]]; then
            log "genesis staged startup: core health requires voting_validators>=$core_min_voting and ignores delinquent validators until final health"
        else
            log "genesis staged startup: validator current/delinquent status is advisory; liveness requires active processes, RPC, tx health, and advancing slots"
        fi
    fi
    : > "$UP_LOG_DIR/summary.log"
    up_phase infra "$UP_LOG_DIR/01-infra"
    sleep_between_up_phases infra
    up_phase solana_bootstrap "$UP_LOG_DIR/02-bootstrap"
    sleep_between_up_phases solana_bootstrap
    up_phase solana_staked_validator "$UP_LOG_DIR/03-staked"

    wait_for_health "$data_dir" core "$core_count" "$core_count" "$core_count" "$CORE_ACTIVE_WAIT" "$core_min_voting" "$CORE_REQUIRE_TX_HEALTH" "$CORE_SLOT_RETRIES" "$CORE_TX_CHECK_TIMEOUT" "$CORE_HEALTH_TIMEOUT" "$core_max_delinquent"
    stabilize_core_slots "$data_dir"

    sleep_between_up_phases core-health
    up_phase solana_observer_validator "$UP_LOG_DIR/04-observers"
    sleep_between_up_phases solana_observer_validator
    up_phase solana_validator "$UP_LOG_DIR/05-unsplit-validators"
    sleep_between_up_phases solana_validator
    up_phase internet_map "$UP_LOG_DIR/06-internet-map"
    sleep_between_up_phases internet_map
    up_phase other "$UP_LOG_DIR/07-other"

    wait_for_health "$data_dir" final "$VALIDATORS" "$VALIDATORS" "$MIN_ACTIVE_SOLANA" "$ACTIVE_WAIT" "$MIN_VOTING_VALIDATORS" "$REQUIRE_TX_HEALTH" "$SLOT_RETRIES" "$TX_CHECK_TIMEOUT" "$HEALTH_TIMEOUT"
    stabilize_full_network_after_health "$data_dir"
}

capture_solana_logs() {
    local data_dir="$1"
    local log_dir="$data_dir/solana-container-logs"
    local name
    local safe_name
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    mkdir -p "$log_dir"
    while IFS= read -r name; do
        [[ -n "$name" ]] || continue
        safe_name="${name//[^A-Za-z0-9_.-]/_}"
        timeout "$LOG_DOCKER_TIMEOUT" docker logs --tail "$SOLANA_LOG_TAIL" "$name" > "$log_dir/$safe_name.log" 2>&1 || true
        timeout "$LOG_DOCKER_TIMEOUT" docker top "$name" -eo pid,ppid,stat,comm,args > "$log_dir/$safe_name.top" 2>&1 || true
    done < <(compose_container_names | grep -E 'Solana-(Bootstrap|Validator)' || true)
}

capture_solana_diagnostics() {
    local data_dir="$1"
    local diag_dir="$data_dir/solana-diagnostics"
    local bootstrap
    local name
    local safe_name
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi

    mkdir -p "$diag_dir"
    timeout "$HEALTH_DOCKER_TIMEOUT" docker compose -p "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" ps > "$diag_dir/compose-ps.txt" 2>&1 || true
    compose_container_names > "$diag_dir/compose-containers.txt" 2>&1 || true

    bootstrap="$(compose_container_names | grep -E 'Solana-Bootstrap' | head -n 1 || true)"
    if [[ -n "$bootstrap" ]]; then
        timeout "$HEALTH_DOCKER_TIMEOUT" docker exec "$bootstrap" sh -lc '
set +e
echo "bootstrap_container=$(hostname)"
echo "bootstrap_identity=$(solana-keygen pubkey /opt/solana/config/bootstrap-validator/identity.json 2>/dev/null)"
echo "slot=$(solana --url http://127.0.0.1:8899 slot 2>&1)"
echo "block_height=$(solana --url http://127.0.0.1:8899 block-height 2>&1)"
echo "epoch_info_begin"
solana --url http://127.0.0.1:8899 epoch-info 2>&1
echo "epoch_info_end"
echo "validators_begin"
solana --url http://127.0.0.1:8899 validators 2>&1
echo "validators_end"
echo "leader_schedule_epoch0_begin"
solana --url http://127.0.0.1:8899 leader-schedule --epoch 0 2>&1
echo "leader_schedule_epoch0_end"
echo "gossip_begin"
solana --url http://127.0.0.1:8899 gossip 2>&1
echo "gossip_end"
echo "ledger_files_begin"
ls -la /opt/solana/config/bootstrap-validator 2>&1
sha256sum /opt/solana/config/bootstrap-validator/genesis.bin 2>/dev/null || true
echo "ledger_files_end"
' > "$diag_dir/bootstrap-rpc-state.txt" 2>&1 || true
    fi

    : > "$diag_dir/container-keypairs-and-genesis.txt"
    while IFS= read -r name; do
        [[ -n "$name" ]] || continue
        safe_name="${name//[^A-Za-z0-9_.-]/_}"
        {
            echo "container=$name"
            timeout "$LOG_DOCKER_TIMEOUT" docker exec "$name" sh -lc '
set +e
kind=unknown
key=/opt/solana/config/validator/identity.json
ledger=/opt/solana/config/validator
if [ -f /opt/solana/config/bootstrap-validator/identity.json ]; then
  kind=bootstrap
  key=/opt/solana/config/bootstrap-validator/identity.json
  ledger=/opt/solana/config/bootstrap-validator
elif [ -f /opt/solana/config/validator/identity.json ]; then
  kind=validator
fi
echo "kind=$kind"
echo "identity=$(solana-keygen pubkey "$key" 2>/dev/null)"
if [ -f "$ledger/vote-account.json" ]; then echo "vote=$(solana-keygen pubkey "$ledger/vote-account.json" 2>/dev/null)"; fi
if [ -f "$ledger/stake-account.json" ]; then echo "stake=$(solana-keygen pubkey "$ledger/stake-account.json" 2>/dev/null)"; fi
if [ -f "$ledger/genesis.bin" ]; then sha256sum "$ledger/genesis.bin"; else echo "missing_genesis_bin=$ledger/genesis.bin"; fi
'
            echo
        } >> "$diag_dir/container-keypairs-and-genesis.txt" 2>&1 || true

        timeout "$LOG_DOCKER_TIMEOUT" docker exec "$name" sh -lc '
set +e
ps -eo pid,ppid,stat,comm,args | grep -E "agave-validator|solana" | grep -v grep
' > "$diag_dir/$safe_name.processes.txt" 2>&1 || true
    done < <(compose_container_names | grep -E 'Solana-(Bootstrap|Validator)' || true)
}

start_memory_collector() {
    local data_dir="$1"
    log "starting Solana memory collector backend=$MEMORY_BACKEND interval=${MEMORY_INTERVAL}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/collect_solana_memory.py" \
        --backend "$MEMORY_BACKEND" \
        --interval "$MEMORY_INTERVAL" \
        --compose-file "$COMPOSE_FILE" \
        --output-dir "$data_dir" \
        > "$data_dir/memory-collector.log" 2>&1 &
    CURRENT_COLLECTOR_PID="$!"
    echo "$CURRENT_COLLECTOR_PID" > "$data_dir/memory-collector.pid"
}

start_block_collector() {
    local data_dir="$1"
    local boot_container_name
    log "starting Solana block metrics collector interval=${BLOCK_INTERVAL}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    boot_container_name="$(bootstrap_container_name || true)"
    BOOT_CONTAINER_NAME="$boot_container_name" "$PYTHON_BIN" "$SCRIPT_DIR/collect_solana_block_metrics.py" \
        --interval "$BLOCK_INTERVAL" \
        --rpc-timeout "$SOLANA_METRICS_RPC_TIMEOUT" \
        --log-slot-fallback \
        --log-tail "$SOLANA_SLOT_LOG_TAIL" \
        --output "$data_dir/block_metrics.csv" \
        > "$data_dir/block-metrics.log" 2>&1 &
    CURRENT_BLOCK_PID="$!"
    echo "$CURRENT_BLOCK_PID" > "$data_dir/block-metrics.pid"
}

start_active_collector() {
    local data_dir="$1"
    log "starting Solana active validator collector interval=${ACTIVE_INTERVAL}s min_active=${MIN_ACTIVE_SOLANA}"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/collect_solana_active_validators.py" \
        --interval "$ACTIVE_INTERVAL" \
        --output "$data_dir/active_validators.csv" \
        --expected-validators "$VALIDATORS" \
        --min-active "$MIN_ACTIVE_SOLANA" \
        --docker-timeout "$HEALTH_DOCKER_TIMEOUT" \
        --mode "$ACTIVE_COUNT_MODE" \
        --compose-project "$COMPOSE_PROJECT_NAME" \
        --confirm-retries "$ACTIVE_CONFIRM_RETRIES" \
        --confirm-delay "$ACTIVE_CONFIRM_DELAY" \
        > "$data_dir/active-validator-collector.log" 2>&1 &
    CURRENT_ACTIVE_PID="$!"
    echo "$CURRENT_ACTIVE_PID" > "$data_dir/active-validator-collector.pid"
}

start_gossip_collector() {
    local data_dir="$1"
    local boot_container_name
    log "starting Solana gossip collector interval=${GOSSIP_INTERVAL}s min_gossip_nodes=${MIN_GOSSIP_NODES}"
    if [[ "$DRY_RUN" -eq 1 || "$MIN_GOSSIP_NODES" -eq 0 ]]; then
        return 0
    fi
    boot_container_name="$(bootstrap_container_name || true)"
    BOOT_CONTAINER_NAME="$boot_container_name" "$PYTHON_BIN" "$SCRIPT_DIR/collect_solana_gossip_metrics.py" \
        --interval "$GOSSIP_INTERVAL" \
        --rpc-timeout "$SOLANA_METRICS_RPC_TIMEOUT" \
        --output "$data_dir/gossip_metrics.csv" \
        > "$data_dir/gossip-metrics.log" 2>&1 &
    CURRENT_GOSSIP_PID="$!"
    echo "$CURRENT_GOSSIP_PID" > "$data_dir/gossip-metrics.pid"
}

start_tx_load() {
    local data_dir="$1"
    local boot_container_name
    if [[ "$TX_RATE" == "0" ]]; then
        log "tx load disabled"
        return 0
    fi
    mkdir -p "$data_dir/tx-load"
    log "starting Solana tx load rate=${TX_RATE}/s amount=${TX_AMOUNT} submit_timeout=${TX_SUBMIT_TIMEOUT}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    boot_container_name="$(bootstrap_container_name || true)"
    BOOT_CONTAINER_NAME="$boot_container_name" "$PYTHON_BIN" "$SCRIPT_DIR/send_solana_tx.py" \
        --rate "$TX_RATE" \
        --amount "$TX_AMOUNT" \
        --submit-timeout "$TX_SUBMIT_TIMEOUT" \
        --log-dir "$data_dir/tx-load" \
        > "$data_dir/tx-load/stdout.log" 2>&1 &
    CURRENT_TX_PID="$!"
    echo "$CURRENT_TX_PID" > "$data_dir/tx-load/pid"
}

write_experiment_env() {
    local data_dir="$1"
    {
        echo "run_id=$RUN_PREFIX"
        echo "validators=$VALIDATORS"
        echo "bootstrap=1"
        echo "non_bootstrap_validators=$((VALIDATORS - 1))"
        echo "stub_as_count=$STUB_AS_COUNT"
        echo "platform=$PLATFORM"
        echo "docker_platform=$DOCKER_PLATFORM"
        echo "internet_map=$INTERNET_MAP"
        echo "duration_seconds=$DURATION_SECONDS"
        echo "memory_interval=$MEMORY_INTERVAL"
        echo "block_interval=$BLOCK_INTERVAL"
        echo "active_interval=$ACTIVE_INTERVAL"
        echo "gossip_interval=$GOSSIP_INTERVAL"
        echo "solana_metrics_rpc_timeout=$SOLANA_METRICS_RPC_TIMEOUT"
        echo "health_timeout=$HEALTH_TIMEOUT"
        echo "slot_retries=$SLOT_RETRIES"
        echo "health_docker_timeout=$HEALTH_DOCKER_TIMEOUT"
        echo "solana_log_tail=$SOLANA_LOG_TAIL"
        echo "solana_slot_log_tail=$SOLANA_SLOT_LOG_TAIL"
        echo "tx_rate=$TX_RATE"
        echo "tx_amount=$TX_AMOUNT"
        echo "tx_submit_timeout=$TX_SUBMIT_TIMEOUT"
        echo "build_mode=$BUILD_MODE"
        echo "skip_base_build=$SKIP_BASE_BUILD"
        echo "skip_build=$SKIP_BUILD"
        echo "keep_on_failure=$KEEP_ON_FAILURE"
        echo "clean_d61_solana_docker=$CLEAN_D61_SOLANA_DOCKER"
        echo "up_preset=$UP_PRESET"
        echo "up_phase_delay=$UP_PHASE_DELAY"
        echo "solana_two_stage_startup=$SOLANA_TWO_STAGE_STARTUP"
        echo "core_health_timeout=$CORE_HEALTH_TIMEOUT"
        echo "core_active_wait=$CORE_ACTIVE_WAIT"
        echo "core_slot_retries=$CORE_SLOT_RETRIES"
        echo "core_require_tx_health=$CORE_REQUIRE_TX_HEALTH"
        echo "core_tx_check_timeout=$CORE_TX_CHECK_TIMEOUT"
        echo "core_stabilize_seconds=$CORE_STABILIZE_SECONDS"
        echo "core_stabilize_check_interval=$CORE_STABILIZE_CHECK_INTERVAL"
        echo "core_validator_count=$(core_validator_count)"
        echo "min_voting_validators=$MIN_VOTING_VALIDATORS"
        echo "max_delinquent_validators=$MAX_DELINQUENT_VALIDATORS"
        echo "strict_validator_status=$STRICT_VALIDATOR_STATUS"
        echo "min_active_solana=$MIN_ACTIVE_SOLANA"
        echo "min_gossip_nodes=$MIN_GOSSIP_NODES"
        echo "active_wait=$ACTIVE_WAIT"
        echo "active_count_mode=$ACTIVE_COUNT_MODE"
        echo "active_confirm_retries=$ACTIVE_CONFIRM_RETRIES"
        echo "active_confirm_delay=$ACTIVE_CONFIRM_DELAY"
        echo "require_tx_health=$REQUIRE_TX_HEALTH"
        echo "tx_check_timeout=$TX_CHECK_TIMEOUT"
        echo "solana_slots_per_epoch=$SOLANA_SLOTS_PER_EPOCH"
        echo "solana_target_tick_duration_ms=$SOLANA_TARGET_TICK_DURATION_MS"
        echo "solana_ticks_per_slot=$SOLANA_TICKS_PER_SLOT"
        echo "solana_validator_stake_sol=$SOLANA_VALIDATOR_STAKE_SOL"
        echo "solana_enable_validator_stake=$SOLANA_ENABLE_VALIDATOR_STAKE"
        echo "solana_staked_validators=$SOLANA_STAKED_VALIDATORS"
        echo "solana_non_staked_no_voting=$SOLANA_NON_STAKED_NO_VOTING"
        echo "solana_split_validator_phases=$SOLANA_SPLIT_VALIDATOR_PHASES"
        echo "solana_genesis_voting_validators=$SOLANA_GENESIS_VOTING_VALIDATORS"
        echo "solana_genesis_staged_startup=$SOLANA_GENESIS_STAGED_STARTUP"
        echo "solana_genesis_start_barrier_validators=$SOLANA_GENESIS_START_BARRIER_VALIDATORS"
        echo "solana_combined_startup=$SOLANA_COMBINED_STARTUP"
        echo "solana_genesis_creation_time=$SOLANA_GENESIS_CREATION_TIME"
        echo "solana_genesis_bootstrap_stake_lamports=$SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS"
        echo "solana_genesis_bootstrap_balance_lamports=$SOLANA_GENESIS_BOOTSTRAP_BALANCE_LAMPORTS"
        echo "solana_genesis_validator_stake_lamports=$SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS"
        echo "solana_genesis_validator_balance_lamports=$SOLANA_GENESIS_VALIDATOR_BALANCE_LAMPORTS"
        echo "solana_genesis_core_validators=$SOLANA_GENESIS_CORE_VALIDATORS"
        echo "solana_genesis_core_validator_stake_lamports=$SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS"
        echo "solana_tpu_disable_quic=$SOLANA_TPU_DISABLE_QUIC"
        echo "solana_tpu_enable_udp=$SOLANA_TPU_ENABLE_UDP"
        echo "solana_agave_low_memory_profile=$SOLANA_AGAVE_LOW_MEMORY_PROFILE"
        echo "solana_agave_low_memory_level=$SOLANA_AGAVE_LOW_MEMORY_LEVEL"
        echo "solana_observer_batch_size=$SOLANA_OBSERVER_BATCH_SIZE"
        echo "solana_observer_jobs=$SOLANA_OBSERVER_JOBS"
        echo "solana_observer_batch_delay=$SOLANA_OBSERVER_BATCH_DELAY"
        echo "solana_observer_compose_parallel_limit=$SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT"
        echo "solana_nofile=$SOLANA_NOFILE"
        echo "solana_disable_warmup_epochs=$SOLANA_DISABLE_WARMUP_EPOCHS"
        echo "slot_stall_timeout=$SLOT_STALL_TIMEOUT"
        echo "slot_stall_grace=$SLOT_STALL_GRACE"
        echo "slot_stall_check_interval=$SLOT_STALL_CHECK_INTERVAL"
        echo "active_stall_timeout=$ACTIVE_STALL_TIMEOUT"
        echo "active_stall_grace=$ACTIVE_STALL_GRACE"
        echo "post_health_stabilize_seconds=$POST_HEALTH_STABILIZE_SECONDS"
        echo "strict_active_sampling=$STRICT_ACTIVE_SAMPLING"
        echo "output_dir=$OUTPUT_DIR"
        echo "compose_file=$COMPOSE_FILE"
        echo "compose_project_name=$COMPOSE_PROJECT_NAME"
        echo "wait_for_lock=$WAIT_FOR_LOCK"
        echo "wait_for_load_below=$WAIT_FOR_LOAD_BELOW"
        echo "load_wait_timeout=$LOAD_WAIT_TIMEOUT"
        echo "load_wait_interval=$LOAD_WAIT_INTERVAL"
        echo "host_memory_min_available_gib=$HOST_MEMORY_MIN_AVAILABLE_GIB"
        echo "host_memory_check_interval=$HOST_MEMORY_CHECK_INTERVAL"
        echo "capture_final_diagnostics=$CAPTURE_FINAL_DIAGNOSTICS"
        echo "python_bin=$PYTHON_BIN"
        echo "started_at=$(date -Is)"
    } > "$data_dir/experiment.env"
}

summarize_experiment() {
    local data_dir="$1"
    local fail_arg=()
    if [[ "$STRICT_ACTIVE_SAMPLING" -eq 1 ]]; then
        fail_arg=(--fail-on-fail)
    fi

    "$PYTHON_BIN" "$SCRIPT_DIR/summarize_solana_experiment.py" "$data_dir" \
        --expected-validators "$VALIDATORS" \
        --min-active "$MIN_ACTIVE_SOLANA" \
        --min-gossip-nodes "$MIN_GOSSIP_NODES" \
        --output-json "$data_dir/experiment_summary.json" \
        --output-text "$data_dir/experiment_summary.txt" \
        "${fail_arg[@]}" \
        > "$data_dir/experiment_summary.stdout" 2>&1
}

capture_final_state() {
    local data_dir="$1"
    local boot_container_name
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    boot_container_name="$(bootstrap_container_name || true)"
    EXPECTED_VALIDATORS="$VALIDATORS" \
        MIN_RUNNING_SOLANA=1 \
        MIN_ACTIVE_SOLANA=0 \
        ACTIVE_WAIT="$ACTIVE_WAIT" \
        ACTIVE_COUNT_MODE="$ACTIVE_COUNT_MODE" \
        SKIP_DOCKER_RUNNING_COUNT="$SKIP_DOCKER_RUNNING_COUNT" \
        BOOT_CONTAINER_NAME="$boot_container_name" \
        PYTHON_BIN="$PYTHON_BIN" \
        MIN_VOTING_VALIDATORS="$MIN_VOTING_VALIDATORS" \
        MAX_DELINQUENT_VALIDATORS="$MAX_DELINQUENT_VALIDATORS" \
        STRICT_VALIDATOR_STATUS="$STRICT_VALIDATOR_STATUS" \
        REQUIRE_TX_HEALTH=0 \
        TX_CHECK_TIMEOUT="$TX_CHECK_TIMEOUT" \
        MAX_WAIT=60 \
        SLOT_RETRIES=1 \
        DOCKER_TIMEOUT="$HEALTH_DOCKER_TIMEOUT" \
        "$SCRIPT_DIR/check_solana_health.sh" > "$data_dir/health-final.log" 2>&1 || true
    timeout "$HEALTH_DOCKER_TIMEOUT" docker compose -p "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" ps > "$data_dir/compose-ps-end.txt" 2>&1 || true
    timeout "$HEALTH_DOCKER_TIMEOUT" docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' > "$data_dir/docker-ps-end.txt" 2>&1 || true
    if [[ "$CAPTURE_FINAL_DIAGNOSTICS" == "1" ]]; then
        capture_solana_logs "$data_dir"
        capture_solana_diagnostics "$data_dir"
    else
        log "final Solana container logs and diagnostics skipped: CAPTURE_FINAL_DIAGNOSTICS=0"
        echo "skipped_at=$(date -Is)" > "$data_dir/final-diagnostics-skipped.log"
    fi
}

latest_block_slot() {
    local csv_file="$1"
    "$PYTHON_BIN" - "$csv_file" <<'PY'
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)

latest = None
with path.open(newline="") as handle:
    for row in csv.DictReader(handle):
        slot = (row.get("slot") or "").strip()
        if slot.isdigit():
            latest = (int(slot), row.get("timestamp", ""), row.get("sample", ""))

if latest is None:
    raise SystemExit(1)

print("\t".join(str(item) for item in latest))
PY
}

latest_active_count() {
    local csv_file="$1"
    "$PYTHON_BIN" - "$csv_file" <<'PY'
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)

latest = None
with path.open(newline="") as handle:
    for row in csv.DictReader(handle):
        active = (row.get("active_agave") or "").strip()
        running = (row.get("running_solana") or "").strip()
        if active.isdigit() and running.isdigit():
            latest = (
                int(active),
                int(running),
                row.get("timestamp", ""),
                row.get("sample", ""),
                row.get("status", ""),
            )

if latest is None:
    raise SystemExit(1)

print("\t".join(str(item) for item in latest))
PY
}

probe_live_slot() {
    local slot
    if slot="$(read_boot_slot 2>/dev/null)" && [[ "$slot" =~ ^[0-9]+$ ]]; then
        printf '%s\n' "$slot"
        return 0
    fi
    probe_log_slot
}

probe_log_slot() {
    local boot_container_name
    boot_container_name="$(bootstrap_container_name || true)"
    [[ -n "$boot_container_name" ]] || return 1
    timeout "$HEALTH_DOCKER_TIMEOUT" docker logs --tail "$SOLANA_SLOT_LOG_TAIL" "$boot_container_name" 2>&1 \
        | "$PYTHON_BIN" -c 'import re, sys
text = sys.stdin.read()
slots = []
for pattern in (
    r"\bpoh_recorder\b[^\n]*\bslot=([0-9]+)i?\b",
    r"\boptimistic_slot\b[^\n]*\bslot=([0-9]+)i?\b",
    r"\bleader_slot_[^\n]*\bslot=([0-9]+)i?\b",
):
    slots.extend(int(match) for match in re.findall(pattern, text))
if not slots:
    raise SystemExit(1)
print(max(slots))'
}

sample_for_duration() {
    local data_dir="$1"
    local block_csv="$data_dir/block_metrics.csv"
    local active_csv="$data_dir/active_validators.csv"
    local start_ts deadline now remaining sleep_sec
    local latest slot slot_ts sample
    local probed_slot
    local active_latest active_count running_count active_ts active_sample active_status
    local last_slot=""
    local last_change_ts
    local last_active_ok_ts

    log "sampling for ${DURATION_SECONDS}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    if [[ "$SLOT_STALL_TIMEOUT" -eq 0 && "$ACTIVE_STALL_TIMEOUT" -eq 0 ]]; then
        sleep "$DURATION_SECONDS"
        return 0
    fi

    start_ts="$(date +%s)"
    last_change_ts="$start_ts"
    last_active_ok_ts="$start_ts"
    deadline=$((start_ts + DURATION_SECONDS))
    while true; do
        now="$(date +%s)"
        if (( now >= deadline )); then
            return 0
        fi

        remaining=$((deadline - now))
        sleep_sec="$SLOT_STALL_CHECK_INTERVAL"
        if (( remaining < sleep_sec )); then
            sleep_sec="$remaining"
        fi
        sleep "$sleep_sec"

        now="$(date +%s)"
        if (( now >= deadline )); then
            return 0
        fi

        if [[ "$SLOT_STALL_TIMEOUT" -gt 0 ]]; then
            latest=""
            if latest="$(latest_block_slot "$block_csv" 2>/dev/null)"; then
                IFS=$'\t' read -r slot slot_ts sample <<< "$latest"
                if [[ -z "$last_slot" || "$slot" -gt "$last_slot" ]]; then
                    last_slot="$slot"
                    last_change_ts="$now"
                    log "slot progress observed: slot=$slot sample=$sample timestamp=$slot_ts"
                elif (( now - start_ts >= SLOT_STALL_GRACE && now - last_change_ts >= SLOT_STALL_TIMEOUT )); then
                    if probed_slot="$(probe_live_slot 2>/dev/null)" && [[ "$probed_slot" -gt "$last_slot" ]]; then
                        last_slot="$probed_slot"
                        last_change_ts="$now"
                        log "slot progress observed by direct probe: slot=$probed_slot"
                        continue
                    fi
                    {
                        echo "slot_stalled_at=$(date -Is)"
                        echo "slot=$slot"
                        echo "last_change_age_sec=$((now - last_change_ts))"
                        echo "stall_timeout_sec=$SLOT_STALL_TIMEOUT"
                        echo "sample=$sample"
                        echo "timestamp=$slot_ts"
                    } > "$data_dir/stall-detected.log"
                    log "slot stalled: slot=$slot unchanged_for=$((now - last_change_ts))s timeout=${SLOT_STALL_TIMEOUT}s"
                    return 1
                fi
            elif (( now - start_ts >= SLOT_STALL_GRACE && now - last_change_ts >= SLOT_STALL_TIMEOUT )); then
                if probed_slot="$(probe_live_slot 2>/dev/null)"; then
                    if [[ -z "$last_slot" || "$probed_slot" -gt "$last_slot" ]]; then
                        last_slot="$probed_slot"
                        last_change_ts="$now"
                        log "slot progress observed by direct probe after missing metrics: slot=$probed_slot"
                        continue
                    fi
                    {
                        echo "slot_stalled_at=$(date -Is)"
                        echo "slot=$probed_slot"
                        echo "last_change_age_sec=$((now - last_change_ts))"
                        echo "stall_timeout_sec=$SLOT_STALL_TIMEOUT"
                        echo "source=direct_probe_after_missing_metrics"
                    } > "$data_dir/stall-detected.log"
                    log "slot stalled by direct probe: slot=$probed_slot unchanged_for=$((now - last_change_ts))s timeout=${SLOT_STALL_TIMEOUT}s"
                    return 1
                fi
                {
                    echo "slot_metrics_missing_at=$(date -Is)"
                    echo "last_change_age_sec=$((now - last_change_ts))"
                    echo "stall_timeout_sec=$SLOT_STALL_TIMEOUT"
                } > "$data_dir/stall-detected.log"
                log "slot metrics missing for too long: unchanged_for=$((now - last_change_ts))s timeout=${SLOT_STALL_TIMEOUT}s"
                return 1
            fi
        fi

        if [[ "$ACTIVE_STALL_TIMEOUT" -gt 0 && "$MIN_ACTIVE_SOLANA" -gt 0 ]]; then
            active_latest=""
            if active_latest="$(latest_active_count "$active_csv" 2>/dev/null)"; then
                IFS=$'\t' read -r active_count running_count active_ts active_sample active_status <<< "$active_latest"
                if (( active_count >= MIN_ACTIVE_SOLANA )); then
                    last_active_ok_ts="$now"
                    log "active validator count ok: active=$active_count running=$running_count sample=$active_sample timestamp=$active_ts"
                elif (( now - start_ts >= ACTIVE_STALL_GRACE && now - last_active_ok_ts >= ACTIVE_STALL_TIMEOUT )); then
                    {
                        echo "active_stalled_at=$(date -Is)"
                        echo "active_agave=$active_count"
                        echo "running_solana=$running_count"
                        echo "min_active_solana=$MIN_ACTIVE_SOLANA"
                        echo "last_active_ok_age_sec=$((now - last_active_ok_ts))"
                        echo "active_stall_timeout_sec=$ACTIVE_STALL_TIMEOUT"
                        echo "sample=$active_sample"
                        echo "timestamp=$active_ts"
                        echo "status=$active_status"
                    } > "$data_dir/active-stall-detected.log"
                    log "active validator count below threshold: active=$active_count min=$MIN_ACTIVE_SOLANA unchanged_for=$((now - last_active_ok_ts))s"
                    return 1
                fi
            elif (( now - start_ts >= ACTIVE_STALL_GRACE && now - last_active_ok_ts >= ACTIVE_STALL_TIMEOUT )); then
                {
                    echo "active_metrics_missing_at=$(date -Is)"
                    echo "last_active_ok_age_sec=$((now - last_active_ok_ts))"
                    echo "active_stall_timeout_sec=$ACTIVE_STALL_TIMEOUT"
                } > "$data_dir/active-stall-detected.log"
                log "active validator metrics missing for too long: unchanged_for=$((now - last_active_ok_ts))s timeout=${ACTIVE_STALL_TIMEOUT}s"
                return 1
            fi
        fi
    done
}

wait_for_host_load() {
    local threshold="$1"
    local timeout_sec="$2"
    local interval_sec="$3"
    local start_ts
    local now
    local load1

    if [[ "$threshold" -le 0 || "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi

    start_ts="$(date +%s)"
    while true; do
        read -r load1 _ < /proc/loadavg
        if "$PYTHON_BIN" - "$load1" "$threshold" <<'PY'
import sys
load = float(sys.argv[1])
threshold = float(sys.argv[2])
raise SystemExit(0 if load < threshold else 1)
PY
        then
            log "host load gate passed: load1=$load1 threshold=$threshold"
            return 0
        fi

        now="$(date +%s)"
        if [[ "$timeout_sec" -gt 0 && $((now - start_ts)) -ge "$timeout_sec" ]]; then
            log "host load gate timed out: load1=$load1 threshold=$threshold timeout=${timeout_sec}s"
            return 1
        fi

        log "waiting for host load below $threshold: load1=$load1 interval=${interval_sec}s"
        sleep "$interval_sec"
    done
}

DATA_DIR="$RUN_DIR/solana-large"
BUILD_LOG_DIR="$DATA_DIR/build-logs"
UP_LOG_DIR="$DATA_DIR/up-logs"
mkdir -p "$DATA_DIR" "$BUILD_LOG_DIR" "$UP_LOG_DIR"
write_experiment_env "$DATA_DIR"

log "large Solana experiment started: run_dir=$RUN_DIR validators=$VALIDATORS stub_as=$STUB_AS_COUNT duration=${DURATION_SECONDS}s tx_rate=$TX_RATE build_mode=$BUILD_MODE genesis_voting=$SOLANA_GENESIS_VOTING_VALIDATORS"

cleanup_bg
compose_down
cleanup_stale_d61_solana_docker
wait_for_host_load "$WAIT_FOR_LOAD_BELOW" "$LOAD_WAIT_TIMEOUT" "$LOAD_WAIT_INTERVAL"
start_host_memory_watchdog "$DATA_DIR"
build_base_image
generate_output "$DATA_DIR"

if [[ "$BUILD_MODE" == "nobuild-mount" ]]; then
    log "BUILD_MODE=nobuild-mount: per-node docker compose build skipped"
elif [[ "$SKIP_BUILD" -eq 0 ]]; then
    run_cmd env DOCKER_BUILDKIT="$DOCKER_BUILDKIT" LOG_DIR="$BUILD_LOG_DIR" \
        COMPOSE_FILE="$COMPOSE_FILE" \
        "$SCRIPT_DIR/batch_parallel_build.sh" \
        --buildkit "$DOCKER_BUILDKIT" \
        --batch-size "$BUILD_BATCH_SIZE" \
        --jobs "$BUILD_JOBS" \
        --retry "$BUILD_RETRY"
else
    log "SKIP_BUILD=1: per-node docker compose build skipped"
fi

if [[ "$BUILD_ONLY" -eq 1 ]]; then
    log "BUILD_ONLY=1: stopping before docker compose up"
    COMPOSE_DOWN_ON_EXIT=0
    exit 0
fi

start_solana_network "$DATA_DIR"

start_block_collector "$DATA_DIR"
start_active_collector "$DATA_DIR"
start_gossip_collector "$DATA_DIR"
start_tx_load "$DATA_DIR"
start_memory_collector "$DATA_DIR"

RUN_STATUS=0
if ! sample_for_duration "$DATA_DIR"; then
    RUN_STATUS=1
fi

capture_final_state "$DATA_DIR"
cleanup_bg

if [[ -f "$UP_LOG_DIR/summary.log" ]]; then
    cp "$UP_LOG_DIR/summary.log" "$DATA_DIR/up-summary.log"
fi
if [[ -f "$BUILD_LOG_DIR/summary.log" ]]; then
    cp "$BUILD_LOG_DIR/summary.log" "$DATA_DIR/build-summary.log"
fi

if ! summarize_experiment "$DATA_DIR"; then
    RUN_STATUS=1
    log "experiment summary failed strict checks; see $DATA_DIR/experiment_summary.txt"
fi

if [[ "$KEEP_LAST" -eq 0 ]]; then
    compose_down
    COMPOSE_DOWN_ON_EXIT=0
else
    COMPOSE_DOWN_ON_EXIT=0
fi

log "large Solana experiment completed"
log "data_dir=$DATA_DIR"
if [[ "$RUN_STATUS" -ne 0 ]]; then
    log "large Solana experiment failed: sampling watchdog or strict summary check failed"
    exit "$RUN_STATUS"
fi
