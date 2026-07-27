#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "batch_parallel_build.sh requires bash. Run it as: ./batch_parallel_build.sh or bash batch_parallel_build.sh" >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILE="${COMPOSE_FILE:-$ETHEREUM_DIR/output/docker-compose.yml}"
BATCH_SIZE="${BATCH_SIZE:-20}"
JOBS="${JOBS:-4}"
BUILD_KIT="${DOCKER_BUILDKIT:-0}"
RETRY="${RETRY:-1}"
LOG_DIR="${LOG_DIR:-$ETHEREUM_DIR/build-logs}"
SERVICES_REGEX="${SERVICES_REGEX:-}"
SERVICES_FILE="${SERVICES_FILE:-}"
START_BATCH="${START_BATCH:-1}"
DRY_RUN=0
PULL=0
NO_CACHE=0
EXTRA_BUILD_ARGS=()

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] [-- extra docker compose build args]

Options:
  -f, --compose-file FILE   Compose file. Default: $COMPOSE_FILE
  -b, --batch-size N        Services per build command. Default: $BATCH_SIZE
  -j, --jobs N              Parallel build batches. Default: $JOBS
      --buildkit 0|1        DOCKER_BUILDKIT value. Default: $BUILD_KIT
      --retry N             Retries per failed batch. Default: $RETRY
      --log-dir DIR         Directory for logs. Default: $LOG_DIR
      --services-regex REG  Build only services matching REG
      --services-file FILE  Build only services listed in FILE, one per line
      --start-batch N       Skip batches before N. Useful for resume
      --pull                Pass --pull to docker compose build
      --no-cache            Pass --no-cache to docker compose build
      --dry-run             Print batches without building
  -h, --help                Show this help

Environment overrides:
  COMPOSE_FILE, BATCH_SIZE, JOBS, DOCKER_BUILDKIT, RETRY, LOG_DIR,
  SERVICES_REGEX, SERVICES_FILE, START_BATCH

Examples:
  ./batch_parallel_build.sh
  JOBS=6 BATCH_SIZE=16 ./batch_parallel_build.sh
  ./batch_parallel_build.sh --buildkit 1 --jobs 8 --batch-size 12
  ./batch_parallel_build.sh --services-regex '^hnode_' --no-cache
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--compose-file)
            COMPOSE_FILE="$2"
            shift 2
            ;;
        -b|--batch-size)
            BATCH_SIZE="$2"
            shift 2
            ;;
        -j|--jobs)
            JOBS="$2"
            shift 2
            ;;
        --buildkit)
            BUILD_KIT="$2"
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
        --services-file)
            SERVICES_FILE="$2"
            shift 2
            ;;
        --start-batch)
            START_BATCH="$2"
            shift 2
            ;;
        --pull)
            PULL=1
            shift
            ;;
        --no-cache)
            NO_CACHE=1
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
            EXTRA_BUILD_ARGS+=("$@")
            break
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! "$BATCH_SIZE" =~ ^[0-9]+$ || "$BATCH_SIZE" -lt 1 ]]; then
    echo "BATCH_SIZE must be a positive integer." >&2
    exit 2
fi

if [[ ! "$JOBS" =~ ^[0-9]+$ || "$JOBS" -lt 1 ]]; then
    echo "JOBS must be a positive integer." >&2
    exit 2
fi

if [[ ! "$RETRY" =~ ^[0-9]+$ ]]; then
    echo "RETRY must be a non-negative integer." >&2
    exit 2
fi

if [[ ! "$START_BATCH" =~ ^[0-9]+$ || "$START_BATCH" -lt 1 ]]; then
    echo "START_BATCH must be a positive integer." >&2
    exit 2
fi

if [[ "$BUILD_KIT" != "0" && "$BUILD_KIT" != "1" ]]; then
    echo "--buildkit must be 0 or 1." >&2
    exit 2
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "Compose file not found: $COMPOSE_FILE" >&2
    exit 1
fi

if [[ -n "$SERVICES_FILE" && ! -f "$SERVICES_FILE" ]]; then
    echo "Services file not found: $SERVICES_FILE" >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
SUMMARY_LOG="$LOG_DIR/summary.log"
: > "$SUMMARY_LOG"

log() {
    local message="$1"
    printf '%s %s\n' "$(date '+%F %T')" "$message" | tee -a "$SUMMARY_LOG"
}

mapfile -t ALL_SERVICES < <(docker compose -f "$COMPOSE_FILE" config --services)

SELECT_BY_FILE=0
declare -A REQUESTED_SERVICES=()
if [[ -n "$SERVICES_FILE" ]]; then
    SELECT_BY_FILE=1
    while IFS= read -r requested_service; do
        requested_service="${requested_service%%#*}"
        requested_service="${requested_service#"${requested_service%%[![:space:]]*}"}"
        requested_service="${requested_service%"${requested_service##*[![:space:]]}"}"
        [[ -z "$requested_service" ]] && continue
        REQUESTED_SERVICES["$requested_service"]=1
    done < "$SERVICES_FILE"
fi

DUMMY_SERVICES=()
SERVICES=()
for service in "${ALL_SERVICES[@]}"; do
    if [[ "$service" =~ ^[0-9a-f]{32}$ ]]; then
        DUMMY_SERVICES+=("$service")
        continue
    fi

    if [[ "$SELECT_BY_FILE" -eq 1 ]]; then
        if [[ -n "${REQUESTED_SERVICES[$service]:-}" ]]; then
            SERVICES+=("$service")
        fi
    elif [[ -n "$SERVICES_REGEX" ]]; then
        if [[ "$service" =~ $SERVICES_REGEX ]]; then
            SERVICES+=("$service")
        fi
    else
        SERVICES+=("$service")
    fi
done

TOTAL=${#SERVICES[@]}
if [[ "$TOTAL" -eq 0 ]]; then
    echo "No services matched." >&2
    exit 1
fi

BUILD_ARGS=()
if [[ "$PULL" -eq 1 ]]; then
    BUILD_ARGS+=(--pull)
fi
if [[ "$NO_CACHE" -eq 1 ]]; then
    BUILD_ARGS+=(--no-cache)
fi
BUILD_ARGS+=("${EXTRA_BUILD_ARGS[@]}")

TOTAL_BATCHES=$(((TOTAL + BATCH_SIZE - 1) / BATCH_SIZE))
FAILED_BATCHES=()
ACTIVE_PIDS=()
ACTIVE_BATCHES=()

run_services_group() {
    local log_name="$1"
    local heading="$2"
    shift
    shift
    local services=("$@")
    local batch_log="$LOG_DIR/$log_name.log"
    local attempt=1
    local max_attempts=$((RETRY + 1))
    local status=0

    {
        echo "$heading"
        echo "Start: $(date)"
        echo "DOCKER_BUILDKIT=$BUILD_KIT"
        echo "Compose file: $COMPOSE_FILE"
        echo "Build args: ${BUILD_ARGS[*]:-<none>}"
        echo "Services (${#services[@]}):"
        printf '  %s\n' "${services[@]}"
        echo

        while [[ "$attempt" -le "$max_attempts" ]]; do
            echo "Attempt $attempt/$max_attempts: docker compose build"
            if DOCKER_BUILDKIT="$BUILD_KIT" COMPOSE_PROGRESS=plain \
                docker compose -f "$COMPOSE_FILE" build "${BUILD_ARGS[@]}" "${services[@]}"; then
                echo "$heading succeeded on attempt $attempt."
                echo "End: $(date)"
                return 0
            else
                status=$?
                echo "$heading failed on attempt $attempt with status $status."
                if [[ "$attempt" -lt "$max_attempts" ]]; then
                    sleep 3
                fi
            fi
            attempt=$((attempt + 1))
        done

        echo "$heading failed after $max_attempts attempt(s)."
        echo "End: $(date)"
        return "$status"
    } > "$batch_log" 2>&1
}

run_batch() {
    local batch_no="$1"
    shift
    run_services_group \
        "batch-$(printf '%04d' "$batch_no")" \
        "Batch $batch_no/$TOTAL_BATCHES" \
        "$@"
}

wait_wave() {
    local index=0
    local pid
    local batch_no
    local status=0

    for index in "${!ACTIVE_PIDS[@]}"; do
        pid="${ACTIVE_PIDS[$index]}"
        batch_no="${ACTIVE_BATCHES[$index]}"
        if wait "$pid"; then
            log "batch $batch_no succeeded"
        else
            status=$?
            FAILED_BATCHES+=("$batch_no")
            log "batch $batch_no failed with status $status"
        fi
    done

    ACTIVE_PIDS=()
    ACTIVE_BATCHES=()
}

log "parallel docker compose build started"
log "compose_file=$COMPOSE_FILE all_services=${#ALL_SERVICES[@]} dummy_services=${#DUMMY_SERVICES[@]} target_services=$TOTAL batches=$TOTAL_BATCHES batch_size=$BATCH_SIZE jobs=$JOBS buildkit=$BUILD_KIT retry=$RETRY"
if [[ -n "$SERVICES_REGEX" ]]; then
    log "services_regex=$SERVICES_REGEX"
fi
if [[ -n "$SERVICES_FILE" ]]; then
    log "services_file=$SERVICES_FILE requested_services=${#REQUESTED_SERVICES[@]}"
fi
if [[ "${#BUILD_ARGS[@]}" -gt 0 ]]; then
    log "build_args=${BUILD_ARGS[*]}"
fi

if [[ "${#DUMMY_SERVICES[@]}" -gt 0 ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "Base/dummy image services:"
        printf '  %s\n' "${DUMMY_SERVICES[@]}"
    else
        log "building ${#DUMMY_SERVICES[@]} dummy/base image service(s) first"
        if run_services_group \
            "batch-0000-dummies" \
            "Dummy/base image build" \
            "${DUMMY_SERVICES[@]}"; then
            log "dummy/base image build succeeded"
        else
            log "dummy/base image build failed; inspect $LOG_DIR/batch-0000-dummies.log"
            exit 1
        fi
    fi
fi

for ((offset = 0, batch_no = 1; offset < TOTAL; offset += BATCH_SIZE, batch_no++)); do
    batch_services=("${SERVICES[@]:offset:BATCH_SIZE}")

    if [[ "$batch_no" -lt "$START_BATCH" ]]; then
        log "skipping batch $batch_no before start_batch=$START_BATCH"
        continue
    fi

    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "Batch $batch_no/${TOTAL_BATCHES}:"
        printf '  %s\n' "${batch_services[@]}"
        continue
    fi

    log "launching batch $batch_no/${TOTAL_BATCHES} with ${#batch_services[@]} service(s)"
    run_batch "$batch_no" "${batch_services[@]}" &
    ACTIVE_PIDS+=("$!")
    ACTIVE_BATCHES+=("$batch_no")

    if [[ "${#ACTIVE_PIDS[@]}" -ge "$JOBS" ]]; then
        wait_wave
    fi
done

if [[ "$DRY_RUN" -eq 1 ]]; then
    log "dry run completed"
    exit 0
fi

if [[ "${#ACTIVE_PIDS[@]}" -gt 0 ]]; then
    wait_wave
fi

if [[ "${#FAILED_BATCHES[@]}" -gt 0 ]]; then
    log "build completed with failed batches: ${FAILED_BATCHES[*]}"
    log "inspect logs under $LOG_DIR"
    exit 1
fi

log "all batches built successfully"
log "logs: $LOG_DIR"
