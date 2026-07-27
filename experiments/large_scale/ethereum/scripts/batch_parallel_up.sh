#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILE="${COMPOSE_FILE:-$ETHEREUM_DIR/output/docker-compose.yml}"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi
ENV_BATCH_SIZE_SET="${BATCH_SIZE+x}"
ENV_JOBS_SET="${JOBS+x}"
ENV_PHASE_DELAY_SET="${PHASE_DELAY+x}"
ENV_BATCH_DELAY_SET="${BATCH_DELAY+x}"
ENV_COMPOSE_PARALLEL_LIMIT_SET="${COMPOSE_PARALLEL_LIMIT+x}"

BATCH_SIZE="${BATCH_SIZE:-25}"
JOBS="${JOBS:-2}"
RETRY="${RETRY:-1}"
LOG_DIR="${LOG_DIR:-$ETHEREUM_DIR/up-logs}"
SERVICES_REGEX="${SERVICES_REGEX:-}"
START_BATCH="${START_BATCH:-1}"
START_PHASE="${START_PHASE:-infra}"
ONLY_PHASE="${ONLY_PHASE:-}"
PHASE_DELAY="${PHASE_DELAY:-30}"
BATCH_DELAY="${BATCH_DELAY:-2}"
COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-8}"
UP_PRESET="${UP_PRESET:-conservative}"
NO_BUILD=1
NO_DEPS=1
DRY_RUN=0
EXTRA_UP_ARGS=()

CLI_BATCH_SIZE_SET=0
CLI_JOBS_SET=0
CLI_PHASE_DELAY_SET=0
CLI_BATCH_DELAY_SET=0
CLI_COMPOSE_PARALLEL_LIMIT_SET=0

PHASES=(infra geth_boot geth setup beacon_boot beacon validator tools explorer_db explorer_backend explorer_web other)

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] [-- extra docker compose up args]

Options:
  -f, --compose-file FILE   Compose file. Default: $COMPOSE_FILE
  -b, --batch-size N        Services per up command. Default: $BATCH_SIZE
  -j, --jobs N              Parallel up batches inside a phase. Default: $JOBS
      --retry N             Retries per failed batch. Default: $RETRY
      --log-dir DIR         Directory for logs. Default: $LOG_DIR
      --services-regex REG  Start only services matching REG
      --phase PHASE         Start only one phase
      --start-phase PHASE   Skip phases before PHASE. Default: $START_PHASE
      --start-batch N       Skip batches before N in executed phases
      --phase-delay SEC     Sleep between phases. Default: $PHASE_DELAY
      --batch-delay SEC     Sleep between launching batches. Default: $BATCH_DELAY
      --compose-parallel N  COMPOSE_PARALLEL_LIMIT. Default: $COMPOSE_PARALLEL_LIMIT
      --preset NAME         Start preset: conservative, balanced, fast, aggressive.
                            Default: $UP_PRESET
      --with-build          Do not pass --no-build
      --with-deps           Do not pass --no-deps
      --dry-run             Print planned batches without starting containers
  -h, --help                Show this help

Phases:
  infra, geth_boot, geth, setup, beacon_boot, beacon, validator, tools,
  explorer_db, explorer_backend, explorer_web, other

Environment overrides:
  COMPOSE_FILE, BATCH_SIZE, JOBS, RETRY, LOG_DIR, SERVICES_REGEX, START_BATCH,
  START_PHASE, ONLY_PHASE, PHASE_DELAY, BATCH_DELAY, COMPOSE_PARALLEL_LIMIT,
  UP_PRESET

Examples:
  ./batch_parallel_up.sh
  ./batch_parallel_up.sh --preset balanced
  ./batch_parallel_up.sh --preset fast
  JOBS=3 BATCH_SIZE=20 ./batch_parallel_up.sh
  ./batch_parallel_up.sh --phase beacon --start-batch 5
  ./batch_parallel_up.sh --start-phase validator
EOF
}

is_valid_phase() {
    local needle="$1"
    local phase
    for phase in "${PHASES[@]}"; do
        [[ "$phase" == "$needle" ]] && return 0
    done
    return 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--compose-file)
            COMPOSE_FILE="$2"
            shift 2
            ;;
        -b|--batch-size)
            BATCH_SIZE="$2"
            CLI_BATCH_SIZE_SET=1
            shift 2
            ;;
        -j|--jobs)
            JOBS="$2"
            CLI_JOBS_SET=1
            shift 2
            ;;
        --retry)
            RETRY="$2"
            shift 2
            ;;
        --log-dir)
            LOG_DIR="$2"
            shift 2
            ;;
        --services-regex)
            SERVICES_REGEX="$2"
            shift 2
            ;;
        --phase)
            ONLY_PHASE="$2"
            shift 2
            ;;
        --start-phase)
            START_PHASE="$2"
            shift 2
            ;;
        --start-batch)
            START_BATCH="$2"
            shift 2
            ;;
        --phase-delay)
            PHASE_DELAY="$2"
            CLI_PHASE_DELAY_SET=1
            shift 2
            ;;
        --batch-delay)
            BATCH_DELAY="$2"
            CLI_BATCH_DELAY_SET=1
            shift 2
            ;;
        --compose-parallel)
            COMPOSE_PARALLEL_LIMIT="$2"
            CLI_COMPOSE_PARALLEL_LIMIT_SET=1
            shift 2
            ;;
        --preset)
            UP_PRESET="$2"
            shift 2
            ;;
        --with-build)
            NO_BUILD=0
            shift
            ;;
        --with-deps)
            NO_DEPS=0
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
        --)
            shift
            EXTRA_UP_ARGS+=("$@")
            break
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

apply_preset_value() {
    local var_name="$1"
    local value="$2"
    local env_set_name="ENV_${var_name}_SET"
    local cli_set_name="CLI_${var_name}_SET"

    if [[ -z "${!env_set_name}" && "${!cli_set_name}" -eq 0 ]]; then
        printf -v "$var_name" '%s' "$value"
    fi
}

case "$UP_PRESET" in
    conservative)
        apply_preset_value BATCH_SIZE 25
        apply_preset_value JOBS 2
        apply_preset_value PHASE_DELAY 30
        apply_preset_value BATCH_DELAY 2
        apply_preset_value COMPOSE_PARALLEL_LIMIT 8
        ;;
    balanced)
        apply_preset_value BATCH_SIZE 50
        apply_preset_value JOBS 3
        apply_preset_value PHASE_DELAY 10
        apply_preset_value BATCH_DELAY 0
        apply_preset_value COMPOSE_PARALLEL_LIMIT 12
        ;;
    fast)
        apply_preset_value BATCH_SIZE 75
        apply_preset_value JOBS 4
        apply_preset_value PHASE_DELAY 5
        apply_preset_value BATCH_DELAY 0
        apply_preset_value COMPOSE_PARALLEL_LIMIT 20
        ;;
    aggressive)
        apply_preset_value BATCH_SIZE 100
        apply_preset_value JOBS 6
        apply_preset_value PHASE_DELAY 2
        apply_preset_value BATCH_DELAY 0
        apply_preset_value COMPOSE_PARALLEL_LIMIT 32
        ;;
    *)
        echo "Invalid preset: $UP_PRESET" >&2
        usage >&2
        exit 2
        ;;
esac

for value_name in BATCH_SIZE JOBS RETRY START_BATCH PHASE_DELAY BATCH_DELAY COMPOSE_PARALLEL_LIMIT; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done

if [[ "$BATCH_SIZE" -lt 1 || "$JOBS" -lt 1 || "$START_BATCH" -lt 1 || "$COMPOSE_PARALLEL_LIMIT" -lt 1 ]]; then
    echo "BATCH_SIZE, JOBS, START_BATCH, and COMPOSE_PARALLEL_LIMIT must be positive." >&2
    exit 2
fi

if [[ -n "$ONLY_PHASE" ]] && ! is_valid_phase "$ONLY_PHASE"; then
    echo "Invalid phase: $ONLY_PHASE" >&2
    usage >&2
    exit 2
fi

if ! is_valid_phase "$START_PHASE"; then
    echo "Invalid start phase: $START_PHASE" >&2
    usage >&2
    exit 2
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "Compose file not found: $COMPOSE_FILE" >&2
    exit 1
fi

mkdir -p "$LOG_DIR/service-lists"
SUMMARY_LOG="$LOG_DIR/summary.log"
: > "$SUMMARY_LOG"

log() {
    local message="$1"
    printf '%s %s\n' "$(date '+%F %T')" "$message" | tee -a "$SUMMARY_LOG"
}

parse_services() {
    "$PYTHON_BIN" - "$COMPOSE_FILE" <<'PY'
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "PyYAML is required for robust compose service classification. "
        "Set PYTHON_BIN to the repository Python environment."
    ) from exc

compose_file = Path(sys.argv[1])
data = yaml.safe_load(compose_file.read_text())
services = data.get("services", {}) if isinstance(data, dict) else {}
hex32 = re.compile(r"^[0-9a-f]{32}$")

def labels_to_text(labels):
    if labels is None:
        return ""
    if isinstance(labels, dict):
        return " ".join(str(value) for value in labels.values())
    if isinstance(labels, list):
        return " ".join(str(value) for value in labels)
    return str(labels)

def service_text(name, service):
    if not isinstance(service, dict):
        return name
    fields = [
        name,
        str(service.get("container_name", "")),
        labels_to_text(service.get("labels")),
    ]
    return " ".join(fields)

def category_for(name, service):
    text = service_text(name, service)
    if hex32.match(name):
        return "dummy"
    if name.startswith("brdnode_") or name.startswith("rs_ix_"):
        return "infra"
    if "Ethereum-POS-BeaconSetup" in text:
        return "setup"
    if "Ethereum-POS-Geth" in text and "bootnode" in text:
        return "geth_boot"
    if "Ethereum-POS-Geth" in text:
        return "geth"
    if "Ethereum-POS-Beacon" in text and "bootnode" in text:
        return "beacon_boot"
    if "Ethereum-POS-Beacon" in text:
        return "beacon"
    if "Ethereum-POS-Validator" in text:
        return "validator"
    if any(token in text for token in ("Faucet", "UtilityServer", "EthUtilityServer")):
        return "tools"
    if name in {"clickhouse", "postgres", "alloy", "redis", "littlebigtable", "rawbigtable"}:
        return "explorer_db"
    if name == "seedemu-ethexplorer-backend":
        return "explorer_backend"
    if name == "seedemu-ethexplorer-web":
        return "explorer_web"
    return "other"

for name, service in services.items():
    print(f"{category_for(name, service)}\t{name}")
PY
}

for phase in dummy "${PHASES[@]}"; do
    : > "$LOG_DIR/service-lists/$phase.services"
done

while IFS=$'\t' read -r category service; do
    [[ -z "$service" ]] && continue
    if [[ -n "$SERVICES_REGEX" && ! "$service" =~ $SERVICES_REGEX ]]; then
        continue
    fi
    printf '%s\n' "$service" >> "$LOG_DIR/service-lists/$category.services"
done < <(parse_services)

UP_ARGS=(-d)
if [[ "$NO_BUILD" -eq 1 ]]; then
    UP_ARGS+=(--no-build)
fi
if [[ "$NO_DEPS" -eq 1 ]]; then
    UP_ARGS+=(--no-deps)
fi
UP_ARGS+=("${EXTRA_UP_ARGS[@]}")

FAILED_BATCHES=()
ACTIVE_PIDS=()
ACTIVE_BATCHES=()
ACTIVE_PHASES=()

run_group() {
    local phase="$1"
    local batch_no="$2"
    shift 2
    local services=("$@")
    local batch_log="$LOG_DIR/${phase}-batch-$(printf '%04d' "$batch_no").log"
    local attempt=1
    local max_attempts=$((RETRY + 1))
    local status=0

    {
        echo "Phase: $phase"
        echo "Batch: $batch_no"
        echo "Start: $(date)"
        echo "Compose file: $COMPOSE_FILE"
        echo "COMPOSE_PARALLEL_LIMIT=$COMPOSE_PARALLEL_LIMIT"
        echo "Up args: ${UP_ARGS[*]:-<none>}"
        echo "Services (${#services[@]}):"
        printf '  %s\n' "${services[@]}"
        echo

        while [[ "$attempt" -le "$max_attempts" ]]; do
            echo "Attempt $attempt/$max_attempts: docker compose up"
            if COMPOSE_PARALLEL_LIMIT="$COMPOSE_PARALLEL_LIMIT" \
                docker compose -f "$COMPOSE_FILE" up "${UP_ARGS[@]}" "${services[@]}"; then
                echo "Phase $phase batch $batch_no succeeded on attempt $attempt."
                echo "End: $(date)"
                return 0
            else
                status=$?
                echo "Phase $phase batch $batch_no failed on attempt $attempt with status $status."
                if [[ "$attempt" -lt "$max_attempts" ]]; then
                    sleep 3
                fi
            fi
            attempt=$((attempt + 1))
        done

        echo "Phase $phase batch $batch_no failed after $max_attempts attempt(s)."
        echo "End: $(date)"
        return "$status"
    } > "$batch_log" 2>&1
}

wait_wave() {
    local index
    local pid
    local phase
    local batch_no
    local status=0

    for index in "${!ACTIVE_PIDS[@]}"; do
        pid="${ACTIVE_PIDS[$index]}"
        phase="${ACTIVE_PHASES[$index]}"
        batch_no="${ACTIVE_BATCHES[$index]}"
        if wait "$pid"; then
            log "phase=$phase batch=$batch_no succeeded"
        else
            status=$?
            FAILED_BATCHES+=("$phase:$batch_no")
            log "phase=$phase batch=$batch_no failed with status $status"
        fi
    done

    ACTIVE_PIDS=()
    ACTIVE_PHASES=()
    ACTIVE_BATCHES=()
}

count_file_lines() {
    local file="$1"
    if [[ -s "$file" ]]; then
        wc -l < "$file" | tr -d " "
    else
        printf '0'
    fi
}

run_phase() {
    local phase="$1"
    local file="$LOG_DIR/service-lists/$phase.services"
    local total
    local total_batches
    local offset
    local batch_no
    local services
    local batch_services

    mapfile -t services < "$file"
    total=${#services[@]}
    if [[ "$total" -eq 0 ]]; then
        log "phase=$phase skipped, no services"
        return
    fi

    total_batches=$(((total + BATCH_SIZE - 1) / BATCH_SIZE))
    log "phase=$phase starting services=$total batches=$total_batches batch_size=$BATCH_SIZE jobs=$JOBS"

    for ((offset = 0, batch_no = 1; offset < total; offset += BATCH_SIZE, batch_no++)); do
        batch_services=("${services[@]:offset:BATCH_SIZE}")

        if [[ "$batch_no" -lt "$START_BATCH" ]]; then
            log "phase=$phase skipping batch=$batch_no before start_batch=$START_BATCH"
            continue
        fi

        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "Phase $phase batch $batch_no/$total_batches:"
            printf '  %s\n' "${batch_services[@]}"
            continue
        fi

        log "phase=$phase launching batch=$batch_no/$total_batches services=${#batch_services[@]}"
        run_group "$phase" "$batch_no" "${batch_services[@]}" &
        ACTIVE_PIDS+=("$!")
        ACTIVE_PHASES+=("$phase")
        ACTIVE_BATCHES+=("$batch_no")

        if [[ "${#ACTIVE_PIDS[@]}" -ge "$JOBS" ]]; then
            wait_wave
        fi

        if [[ "$BATCH_DELAY" -gt 0 ]]; then
            sleep "$BATCH_DELAY"
        fi
    done

    if [[ "$DRY_RUN" -eq 0 && "${#ACTIVE_PIDS[@]}" -gt 0 ]]; then
        wait_wave
    fi
}

log "batch docker compose up started"
log "compose_file=$COMPOSE_FILE preset=$UP_PRESET batch_size=$BATCH_SIZE jobs=$JOBS retry=$RETRY phase_delay=$PHASE_DELAY batch_delay=$BATCH_DELAY compose_parallel_limit=$COMPOSE_PARALLEL_LIMIT"
log "up_args=${UP_ARGS[*]}"
if [[ -n "$SERVICES_REGEX" ]]; then
    log "services_regex=$SERVICES_REGEX"
fi

for phase in dummy "${PHASES[@]}"; do
    log "services[$phase]=$(count_file_lines "$LOG_DIR/service-lists/$phase.services")"
done

start_seen=0
for phase in "${PHASES[@]}"; do
    if [[ -n "$ONLY_PHASE" && "$phase" != "$ONLY_PHASE" ]]; then
        continue
    fi

    if [[ -z "$ONLY_PHASE" ]]; then
        if [[ "$phase" == "$START_PHASE" ]]; then
            start_seen=1
        fi
        if [[ "$start_seen" -eq 0 ]]; then
            log "phase=$phase skipped before start_phase=$START_PHASE"
            continue
        fi
    fi

    run_phase "$phase"

    if [[ "$DRY_RUN" -eq 0 && "$PHASE_DELAY" -gt 0 ]]; then
        sleep "$PHASE_DELAY"
    fi
done

if [[ "$DRY_RUN" -eq 1 ]]; then
    log "dry run completed"
    exit 0
fi

if [[ "${#FAILED_BATCHES[@]}" -gt 0 ]]; then
    log "up completed with failed batches: ${FAILED_BATCHES[*]}"
    log "inspect logs under $LOG_DIR"
    exit 1
fi

log "all requested phases started successfully"
log "check status with: docker compose -f \"$COMPOSE_FILE\" ps"
