#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "batch_parallel_up.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOLANA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILE="${COMPOSE_FILE:-$SOLANA_DIR/output/docker-compose.yml}"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
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
LOG_DIR="${LOG_DIR:-$SOLANA_DIR/up-logs}"
SERVICES_REGEX="${SERVICES_REGEX:-}"
START_PHASE="${START_PHASE:-infra}"
START_BATCH="${START_BATCH:-1}"
ONLY_PHASE="${ONLY_PHASE:-}"
PHASE_DELAY="${PHASE_DELAY:-20}"
BATCH_DELAY="${BATCH_DELAY:-1}"
COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-8}"
UP_PRESET="${UP_PRESET:-conservative}"
SOLANA_STAKED_VALIDATORS="${SOLANA_STAKED_VALIDATORS:--1}"
SOLANA_SPLIT_VALIDATOR_PHASES="${SOLANA_SPLIT_VALIDATOR_PHASES:-0}"
SOLANA_COMBINED_STARTUP="${SOLANA_COMBINED_STARTUP:-0}"
SOLANA_OBSERVER_BATCH_SIZE="${SOLANA_OBSERVER_BATCH_SIZE:-10}"
SOLANA_OBSERVER_JOBS="${SOLANA_OBSERVER_JOBS:-1}"
SOLANA_OBSERVER_BATCH_DELAY="${SOLANA_OBSERVER_BATCH_DELAY:-60}"
SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT="${SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT:-4}"
NO_BUILD=1
NO_DEPS=1
DRY_RUN=0
EXTRA_UP_ARGS=()

CLI_BATCH_SIZE_SET=0
CLI_JOBS_SET=0
CLI_PHASE_DELAY_SET=0
CLI_BATCH_DELAY_SET=0
CLI_COMPOSE_PARALLEL_LIMIT_SET=0

PHASES=(infra solana_bootstrap solana_staked_validator solana_observer_validator solana_validator internet_map other)

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
      --start-batch N       Skip batches before N
      --phase-delay SEC     Sleep between phases. Default: $PHASE_DELAY
      --batch-delay SEC     Sleep between launching batches. Default: $BATCH_DELAY
      --compose-parallel N  COMPOSE_PARALLEL_LIMIT. Default: $COMPOSE_PARALLEL_LIMIT
      --preset NAME         conservative, balanced, fast, aggressive. Default: $UP_PRESET
      --with-build          Do not pass --no-build
      --with-deps           Do not pass --no-deps
      --dry-run             Print planned batches without starting containers
  -h, --help                Show this help

Phases:
  infra, solana_bootstrap, solana_staked_validator, solana_observer_validator,
  solana_validator, internet_map, other

Environment:
  SOLANA_COMBINED_STARTUP=1 puts bootstrap and validators in one Solana phase.
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
            COMPOSE_FILE="$2"; shift 2 ;;
        -b|--batch-size)
            BATCH_SIZE="$2"; CLI_BATCH_SIZE_SET=1; shift 2 ;;
        -j|--jobs)
            JOBS="$2"; CLI_JOBS_SET=1; shift 2 ;;
        --retry)
            RETRY="$2"; shift 2 ;;
        --log-dir)
            LOG_DIR="$2"; shift 2 ;;
        --services-regex)
            SERVICES_REGEX="$2"; shift 2 ;;
        --phase)
            ONLY_PHASE="$2"; shift 2 ;;
        --start-phase)
            START_PHASE="$2"; shift 2 ;;
        --start-batch)
            START_BATCH="$2"; shift 2 ;;
        --phase-delay)
            PHASE_DELAY="$2"; CLI_PHASE_DELAY_SET=1; shift 2 ;;
        --batch-delay)
            BATCH_DELAY="$2"; CLI_BATCH_DELAY_SET=1; shift 2 ;;
        --compose-parallel)
            COMPOSE_PARALLEL_LIMIT="$2"; CLI_COMPOSE_PARALLEL_LIMIT_SET=1; shift 2 ;;
        --preset)
            UP_PRESET="$2"; shift 2 ;;
        --with-build)
            NO_BUILD=0; shift ;;
        --with-deps)
            NO_DEPS=0; shift ;;
        --dry-run)
            DRY_RUN=1; shift ;;
        -h|--help)
            usage; exit 0 ;;
        --)
            shift; EXTRA_UP_ARGS+=("$@"); break ;;
        *)
            echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
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

for value_name in BATCH_SIZE JOBS RETRY START_BATCH PHASE_DELAY BATCH_DELAY COMPOSE_PARALLEL_LIMIT SOLANA_OBSERVER_BATCH_SIZE SOLANA_OBSERVER_JOBS SOLANA_OBSERVER_BATCH_DELAY SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done

if [[ ! "$SOLANA_STAKED_VALIDATORS" =~ ^-?[0-9]+$ ]] || (( SOLANA_STAKED_VALIDATORS < -1 )); then
    echo "SOLANA_STAKED_VALIDATORS must be -1 or a non-negative integer." >&2
    exit 2
fi
if [[ "$SOLANA_SPLIT_VALIDATOR_PHASES" != "0" && "$SOLANA_SPLIT_VALIDATOR_PHASES" != "1" ]]; then
    echo "SOLANA_SPLIT_VALIDATOR_PHASES must be 0 or 1." >&2
    exit 2
fi
if [[ "$SOLANA_COMBINED_STARTUP" != "0" && "$SOLANA_COMBINED_STARTUP" != "1" ]]; then
    echo "SOLANA_COMBINED_STARTUP must be 0 or 1." >&2
    exit 2
fi

if [[ "$BATCH_SIZE" -lt 1 || "$JOBS" -lt 1 || "$START_BATCH" -lt 1 || "$COMPOSE_PARALLEL_LIMIT" -lt 1 || "$SOLANA_OBSERVER_BATCH_SIZE" -lt 1 || "$SOLANA_OBSERVER_JOBS" -lt 1 || "$SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT" -lt 1 ]]; then
    echo "BATCH_SIZE, JOBS, START_BATCH, COMPOSE_PARALLEL_LIMIT, SOLANA_OBSERVER_BATCH_SIZE, SOLANA_OBSERVER_JOBS, and SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT must be positive." >&2
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
    printf '%s %s\n' "$(date '+%F %T')" "$1" | tee -a "$SUMMARY_LOG"
}

parse_services() {
    "$PYTHON_BIN" - "$COMPOSE_FILE" <<'PY'
import re
import sys
from pathlib import Path

import os

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required; set PYTHON_BIN to seedpy310.") from exc

compose_file = Path(sys.argv[1])
data = yaml.safe_load(compose_file.read_text())
services = data.get("services", {}) if isinstance(data, dict) else {}
hex32 = re.compile(r"^[0-9a-f]{32}$")
split_validator_phases = os.environ.get("SOLANA_SPLIT_VALIDATOR_PHASES", "0") == "1"
try:
    staked_validators = int(os.environ.get("SOLANA_STAKED_VALIDATORS", "-1"))
except ValueError:
    staked_validators = -1

def labels_text(labels):
    if isinstance(labels, dict):
        return " ".join(str(value) for value in labels.values())
    if isinstance(labels, list):
        return " ".join(str(value) for value in labels)
    return str(labels or "")

def service_text(name, service):
    if not isinstance(service, dict):
        return name
    return " ".join([
        name,
        str(service.get("container_name", "")),
        labels_text(service.get("labels")),
    ])

def category_for(name, service):
    text = service_text(name, service)
    if hex32.match(name):
        return "dummy"
    if name.startswith("brdnode_") or name.startswith("rs_ix_"):
        return "infra"
    if "Solana-Bootstrap" in text:
        if os.environ.get("SOLANA_COMBINED_STARTUP", "0") == "1":
            return "solana_validator"
        return "solana_bootstrap"
    if "Solana-Validator" in text:
        match = re.search(r"Solana-Validator-(\d+)", text)
        if split_validator_phases and match is not None:
            index = int(match.group(1))
            if staked_validators < 0 or index <= staked_validators:
                return "solana_staked_validator"
            return "solana_observer_validator"
        return "solana_validator"
    if name == "seedemu_internet_map" or "seedemu_internet_map" in text:
        return "internet_map"
    return "other"

for name, service in services.items():
    category = category_for(name, service)
    if category != "dummy":
        print(f"{category}\t{name}")
PY
}

for phase in "${PHASES[@]}"; do
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

terminate_active_jobs() {
    local pid
    for pid in "${ACTIVE_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            pkill -TERM -P "$pid" 2>/dev/null || true
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    for pid in "${ACTIVE_PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
    ACTIVE_PIDS=()
    ACTIVE_PHASES=()
    ACTIVE_BATCHES=()
}

abort_handler() {
    log "received termination; stopping active batch jobs"
    terminate_active_jobs
    exit 130
}

trap abort_handler INT TERM

run_group() {
    local phase="$1"
    local batch_no="$2"
    shift 2
    local services=("$@")
    local batch_log="$LOG_DIR/${phase}-batch-$(printf '%04d' "$batch_no").log"
    local attempt=1
    local max_attempts=$((RETRY + 1))
    local status=0
    local compose_pid=""

    cleanup_compose_child() {
        if [[ -n "$compose_pid" ]] && kill -0 "$compose_pid" 2>/dev/null; then
            kill -TERM "$compose_pid" 2>/dev/null || true
            wait "$compose_pid" 2>/dev/null || true
        fi
    }

    {
        trap cleanup_compose_child INT TERM
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
            COMPOSE_PARALLEL_LIMIT="$COMPOSE_PARALLEL_LIMIT" \
                docker compose -f "$COMPOSE_FILE" up "${UP_ARGS[@]}" "${services[@]}" &
            compose_pid="$!"
            if wait "$compose_pid"; then
                compose_pid=""
                echo "Phase $phase batch $batch_no succeeded on attempt $attempt."
                echo "End: $(date)"
                return 0
            else
                status=$?
                compose_pid=""
            fi
            echo "Phase $phase batch $batch_no failed on attempt $attempt with status $status."
            if [[ "$attempt" -lt "$max_attempts" ]]; then
                sleep 3
            fi
            attempt=$((attempt + 1))
        done

        echo "Phase $phase batch $batch_no failed after $max_attempts attempt(s)."
        echo "End: $(date)"
        return "$status"
    } > "$batch_log" 2>&1
}

wait_wave() {
    local index pid phase batch_no status=0
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
    local services total total_batches offset batch_no batch_services
    local phase_batch_size="$BATCH_SIZE"
    local phase_jobs="$JOBS"
    local phase_batch_delay="$BATCH_DELAY"
    local phase_compose_parallel_limit="$COMPOSE_PARALLEL_LIMIT"

    if [[ "$phase" == "solana_observer_validator" ]]; then
        phase_batch_size="$SOLANA_OBSERVER_BATCH_SIZE"
        phase_jobs="$SOLANA_OBSERVER_JOBS"
        phase_batch_delay="$SOLANA_OBSERVER_BATCH_DELAY"
        phase_compose_parallel_limit="$SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT"
    fi

    mapfile -t services < "$file"
    total=${#services[@]}
    if [[ "$total" -eq 0 ]]; then
        log "phase=$phase skipped, no services"
        return
    fi

    total_batches=$(((total + phase_batch_size - 1) / phase_batch_size))
    log "phase=$phase starting services=$total batches=$total_batches batch_size=$phase_batch_size jobs=$phase_jobs compose_parallel_limit=$phase_compose_parallel_limit"

    for ((offset = 0, batch_no = 1; offset < total; offset += phase_batch_size, batch_no++)); do
        batch_services=("${services[@]:offset:phase_batch_size}")
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
        COMPOSE_PARALLEL_LIMIT="$phase_compose_parallel_limit" run_group "$phase" "$batch_no" "${batch_services[@]}" &
        ACTIVE_PIDS+=("$!")
        ACTIVE_PHASES+=("$phase")
        ACTIVE_BATCHES+=("$batch_no")
        if [[ "${#ACTIVE_PIDS[@]}" -ge "$phase_jobs" ]]; then
            wait_wave
        fi
        if [[ "$phase_batch_delay" -gt 0 ]]; then
            sleep "$phase_batch_delay"
        fi
    done

    if [[ "$DRY_RUN" -eq 0 && "${#ACTIVE_PIDS[@]}" -gt 0 ]]; then
        wait_wave
    fi
}

log "batch docker compose up started"
log "compose_file=$COMPOSE_FILE preset=$UP_PRESET batch_size=$BATCH_SIZE jobs=$JOBS retry=$RETRY phase_delay=$PHASE_DELAY batch_delay=$BATCH_DELAY compose_parallel_limit=$COMPOSE_PARALLEL_LIMIT"
log "up_args=${UP_ARGS[*]}"

for phase in "${PHASES[@]}"; do
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
