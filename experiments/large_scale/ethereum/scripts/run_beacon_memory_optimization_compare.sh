#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_beacon_memory_optimization_compare.sh requires bash." >&2
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

BEACONS="${BEACONS:-10}"
STUB_AS_COUNT="${STUB_AS_COUNT:-}"
PLATFORM="${PLATFORM:-amd}"
ETHER_VIEW="${ETHER_VIEW:-1}"
INTERNET_MAP="${INTERNET_MAP:-1}"
INCLUDE_TOOLS="${INCLUDE_TOOLS:-0}"
VARIANTS="${VARIANTS:-baseline_all no_historic low_cache low_peers fork_lite}"
DURATION_SECONDS="${DURATION_SECONDS:-1800}"
MEMORY_INTERVAL="${MEMORY_INTERVAL:-60}"
BLOCK_INTERVAL="${BLOCK_INTERVAL:-12}"
TX_RATE="${TX_RATE:-1}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-3600}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-60}"
BUILD_KIT="${DOCKER_BUILDKIT:-0}"
BUILD_BATCH_SIZE="${BUILD_BATCH_SIZE:-50}"
BUILD_JOBS="${BUILD_JOBS:-4}"
BUILD_RETRY="${BUILD_RETRY:-1}"
BUILD_MODE="${BUILD_MODE:-docker-build}"
BUILD_ONLY="${BUILD_ONLY:-0}"
UP_PRESET="${UP_PRESET:-fast}"
MEMORY_BACKEND="${MEMORY_BACKEND:-cgroup-scan}"
SKIP_BUILD="${SKIP_BUILD:-0}"
KEEP_LAST="${KEEP_LAST:-0}"
LOW_PEER_NORMAL="${LOW_PEER_NORMAL:-6}"
LOW_PEER_BOOTNODE="${LOW_PEER_BOOTNODE:-24}"
LOW_PEER_BOOTNODES="${LOW_PEER_BOOTNODES:-6}"
LIGHTHOUSE_OFFICIAL_IMAGE="${LIGHTHOUSE_OFFICIAL_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
LIGHTHOUSE_FORK_IMAGE="${LIGHTHOUSE_FORK_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-seedemu-lite}"
LIGHTHOUSE_FORK_SCOPE="${LIGHTHOUSE_FORK_SCOPE:-non-historic}"
LIGHTHOUSE_RAYON_THREADS="${LIGHTHOUSE_RAYON_THREADS:-2}"
RUN_PREFIX="${RUN_PREFIX:-}"
DRY_RUN=0

CURRENT_COLLECTOR_PID=""
CURRENT_BLOCK_PID=""
CURRENT_TX_PID=""
COMPOSE_DOWN_ON_EXIT=1

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Re-run D61 beacon-memory optimization experiments from regenerated output.
Default scale: 10 beacon, 10 geth, 30 validator.

Variants:
  baseline_all  historic states on every beacon, default cache, default peers
  no_historic   historic states only for etherview CL_HOST, default cache
  low_cache     no_historic plus D61 low Lighthouse cache flags
  low_peers     no_historic plus lower normal beacon peers and higher bootnodes
  low_cache_only historic states on every beacon plus low Lighthouse cache flags
  low_peers_only historic states on every beacon plus lower normal beacon peers
  fork_lite     no_historic plus forked Lighthouse SEED low-memory source profile
  baseline_all_fork_lite
                baseline_all plus forked Lighthouse SEED low-memory source profile

Options:
  --beacons N          Beacon count. Geth=N and validators=3*N. Default: $BEACONS
  --stub-as-count N    Stub AS count. Default: min(beacons, 64)
  --duration SEC       Sampling duration after health passes. Default: $DURATION_SECONDS
  --interval SEC       Memory sampling interval. Default: $MEMORY_INTERVAL
  --tx-rate N          Continuous transaction target rate per second. Default: $TX_RATE
  --variants LIST      Space-separated variants. Default: "$VARIANTS"
  --skip-build         Reuse existing images after regenerating output
  --build-mode MODE    docker-build or nobuild-mount. Default: $BUILD_MODE
  --build-only         Stop after generate/patch/build; do not up or sample
  --keep-last          Do not compose down after the final variant
  --lighthouse-fork-image IMAGE
                       Fork image used by fork_lite variants. Default: $LIGHTHOUSE_FORK_IMAGE
  --lighthouse-rayon-threads N
                       Rayon thread cap for fork_lite variants. Default: $LIGHTHOUSE_RAYON_THREADS
  --dry-run            Print actions without running them
  -h, --help           Show this help

Environment overrides:
  BEACONS, STUB_AS_COUNT, PLATFORM, ETHER_VIEW, INTERNET_MAP, INCLUDE_TOOLS, VARIANTS,
  DURATION_SECONDS, MEMORY_INTERVAL, BLOCK_INTERVAL, TX_RATE, HEALTH_TIMEOUT,
  HEALTH_INTERVAL, DOCKER_BUILDKIT, BUILD_BATCH_SIZE, BUILD_JOBS, BUILD_RETRY,
  BUILD_MODE, BUILD_ONLY, UP_PRESET, MEMORY_BACKEND, SKIP_BUILD, KEEP_LAST, LOW_PEER_NORMAL,
  LOW_PEER_BOOTNODE, LOW_PEER_BOOTNODES, LIGHTHOUSE_OFFICIAL_IMAGE, LIGHTHOUSE_FORK_IMAGE,
  LIGHTHOUSE_FORK_SCOPE, LIGHTHOUSE_RAYON_THREADS, RUN_PREFIX, PYTHON_BIN

Examples:
  ./run_beacon_memory_optimization_compare.sh
  ./run_beacon_memory_optimization_compare.sh --beacons 10 --duration 1800
  DURATION_SECONDS=900 VARIANTS="baseline_all fork_lite" ./run_beacon_memory_optimization_compare.sh
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
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
        --interval)
            MEMORY_INTERVAL="$2"
            shift 2
            ;;
        --tx-rate)
            TX_RATE="$2"
            shift 2
            ;;
        --variants)
            VARIANTS="$2"
            shift 2
            ;;
        --skip-build)
            SKIP_BUILD=1
            shift
            ;;
        --build-mode)
            BUILD_MODE="$2"
            shift 2
            ;;
        --build-only)
            BUILD_ONLY=1
            shift
            ;;
        --keep-last)
            KEEP_LAST=1
            shift
            ;;
        --lighthouse-fork-image)
            LIGHTHOUSE_FORK_IMAGE="$2"
            shift 2
            ;;
        --lighthouse-rayon-threads)
            LIGHTHOUSE_RAYON_THREADS="$2"
            shift 2
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

for value_name in BEACONS DURATION_SECONDS MEMORY_INTERVAL BLOCK_INTERVAL HEALTH_TIMEOUT HEALTH_INTERVAL BUILD_BATCH_SIZE BUILD_JOBS BUILD_RETRY LOW_PEER_NORMAL LOW_PEER_BOOTNODE LOW_PEER_BOOTNODES LIGHTHOUSE_RAYON_THREADS; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done

if [[ "$BEACONS" -lt 1 || "$MEMORY_INTERVAL" -lt 1 || "$BLOCK_INTERVAL" -lt 1 || "$BUILD_BATCH_SIZE" -lt 1 || "$BUILD_JOBS" -lt 1 ]]; then
    echo "BEACONS, MEMORY_INTERVAL, BLOCK_INTERVAL, BUILD_BATCH_SIZE, and BUILD_JOBS must be positive." >&2
    exit 2
fi

if [[ "$BUILD_KIT" != "0" && "$BUILD_KIT" != "1" ]]; then
    echo "DOCKER_BUILDKIT must be 0 or 1." >&2
    exit 2
fi

case "$BUILD_MODE" in
    docker-build|nobuild-mount)
        ;;
    *)
        echo "BUILD_MODE must be docker-build or nobuild-mount." >&2
        exit 2
        ;;
esac

case "$LIGHTHOUSE_FORK_SCOPE" in
    all|non-historic)
        ;;
    *)
        echo "LIGHTHOUSE_FORK_SCOPE must be all or non-historic." >&2
        exit 2
        ;;
esac

if [[ "$BUILD_ONLY" != "0" && "$BUILD_ONLY" != "1" ]]; then
    echo "BUILD_ONLY must be 0 or 1." >&2
    exit 2
fi

if [[ -z "$STUB_AS_COUNT" ]]; then
    STUB_AS_COUNT="$BEACONS"
    if [[ "$STUB_AS_COUNT" -gt 64 ]]; then
        STUB_AS_COUNT=64
    fi
fi

if [[ ! "$STUB_AS_COUNT" =~ ^[0-9]+$ || "$STUB_AS_COUNT" -lt 1 ]]; then
    echo "STUB_AS_COUNT must be a positive integer." >&2
    exit 2
fi

if [[ -z "$RUN_PREFIX" ]]; then
    RUN_PREFIX="$(date +%Y%m%d-%H%M%S)-b${BEACONS}-beacon-opt"
fi

RUN_DIR="$ETHEREUM_DIR/experiments/$RUN_PREFIX"
SUMMARY_CSV="$RUN_DIR/summary.csv"
SUMMARY_MD="$RUN_DIR/summary.md"
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

stop_pid() {
    local pid="$1"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
}

cleanup_bg() {
    stop_pid "$CURRENT_TX_PID"
    CURRENT_TX_PID=""
    stop_pid "$CURRENT_BLOCK_PID"
    CURRENT_BLOCK_PID=""
    stop_pid "$CURRENT_COLLECTOR_PID"
    CURRENT_COLLECTOR_PID=""
}

compose_down() {
    if [[ -f "$COMPOSE_FILE" ]]; then
        run_cmd docker compose -f "$COMPOSE_FILE" down -v --remove-orphans
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
    cleanup_bg || true
    compose_down || true
    COMPOSE_DOWN_ON_EXIT=0
    exit 130
}

trap on_exit EXIT
trap on_signal INT TERM

wait_for_health() {
    local data_dir="$1"
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    local latest_log="$data_dir/health-latest.log"

    while (( SECONDS < deadline )); do
        log "running check_pos_health.sh"
        if [[ "$DRY_RUN" -eq 1 ]]; then
            return 0
        fi
        if "$SCRIPT_DIR/check_pos_health.sh" > "$latest_log" 2>&1; then
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

variant_config() {
    local variant="$1"

    CFG_HISTORIC="etherview"
    CFG_CACHE="default"
    CFG_BEACON_PEERS="16"
    CFG_BOOTNODE_PEERS=""
    CFG_BOOTNODES="4"
    CFG_LIGHTHOUSE_SOURCE_PROFILE="official"
    CFG_LIGHTHOUSE_IMAGE=""
    CFG_LIGHTHOUSE_FORK_SCOPE="$LIGHTHOUSE_FORK_SCOPE"
    CFG_LIGHTHOUSE_RAYON_THREADS="$LIGHTHOUSE_RAYON_THREADS"

    case "$variant" in
        baseline_all)
            CFG_HISTORIC="all"
            ;;
        no_historic)
            ;;
        low_cache)
            CFG_CACHE="low"
            ;;
        low_cache_only)
            CFG_HISTORIC="all"
            CFG_CACHE="low"
            ;;
        low_peers)
            CFG_BEACON_PEERS="$LOW_PEER_NORMAL"
            CFG_BOOTNODE_PEERS="$LOW_PEER_BOOTNODE"
            CFG_BOOTNODES="$LOW_PEER_BOOTNODES"
            ;;
        low_peers_only)
            CFG_HISTORIC="all"
            CFG_BEACON_PEERS="$LOW_PEER_NORMAL"
            CFG_BOOTNODE_PEERS="$LOW_PEER_BOOTNODE"
            CFG_BOOTNODES="$LOW_PEER_BOOTNODES"
            ;;
        fork_lite)
            CFG_LIGHTHOUSE_SOURCE_PROFILE="seedemu-lite"
            CFG_LIGHTHOUSE_IMAGE="$LIGHTHOUSE_FORK_IMAGE"
            ;;
        baseline_all_fork_lite)
            CFG_HISTORIC="all"
            CFG_LIGHTHOUSE_SOURCE_PROFILE="seedemu-lite"
            CFG_LIGHTHOUSE_IMAGE="$LIGHTHOUSE_FORK_IMAGE"
            CFG_LIGHTHOUSE_FORK_SCOPE="all"
            ;;
        *)
            echo "invalid variant: $variant" >&2
            exit 2
            ;;
    esac

    if [[ "$CFG_BOOTNODES" -gt "$BEACONS" ]]; then
        CFG_BOOTNODES="$BEACONS"
    fi
}

generate_output() {
    local data_dir="$1"
    local cmd=(
        "$PYTHON_BIN" "$SRC_DIR/ethereum_pos.py"
        --platform "$PLATFORM"
        --beacons "$BEACONS"
        --stub-as-count "$STUB_AS_COUNT"
        --output "$OUTPUT_DIR"
        --beacon-peers "$CFG_BEACON_PEERS"
        --beacon-bootnodes "$CFG_BOOTNODES"
        --beacon-historic-states "$CFG_HISTORIC"
        --lighthouse-cache-profile "$CFG_CACHE"
    )

    if [[ -n "$CFG_BOOTNODE_PEERS" ]]; then
        cmd+=(--beacon-bootnode-peers "$CFG_BOOTNODE_PEERS")
    fi
    if [[ "$ETHER_VIEW" -eq 1 ]]; then
        cmd+=(--ether-view)
    fi
    if [[ "$INCLUDE_TOOLS" -eq 0 ]]; then
        cmd+=(--no-tools)
    fi
    if [[ "$INTERNET_MAP" -eq 0 ]]; then
        cmd+=(--no-internet-map)
    fi

    run_cmd rm -rf "$OUTPUT_DIR"
    run_cmd "${cmd[@]}"
    if [[ "$CFG_LIGHTHOUSE_SOURCE_PROFILE" != "official" ]]; then
        run_cmd "$PYTHON_BIN" "$SCRIPT_DIR/patch_output_lighthouse_fork.py" \
            --output "$OUTPUT_DIR" \
            --official-image "$LIGHTHOUSE_OFFICIAL_IMAGE" \
            --fork-image "$CFG_LIGHTHOUSE_IMAGE" \
            --profile "$CFG_LIGHTHOUSE_SOURCE_PROFILE" \
            --scope "$CFG_LIGHTHOUSE_FORK_SCOPE" \
            --rayon-threads "$CFG_LIGHTHOUSE_RAYON_THREADS"
    fi
    if [[ "$BUILD_MODE" == "nobuild-mount" ]]; then
        run_cmd "$PYTHON_BIN" "$SCRIPT_DIR/patch_output_to_runtime_mounts.py" --output "$OUTPUT_DIR"
    fi
    if [[ "$DRY_RUN" -eq 0 ]]; then
        docker compose -f "$COMPOSE_FILE" config --services > "$data_dir/services.txt"
        cp "$COMPOSE_FILE" "$data_dir/docker-compose.yml"
        if [[ -f "$OUTPUT_DIR/docker-compose.build.yml" ]]; then
            cp "$OUTPUT_DIR/docker-compose.build.yml" "$data_dir/docker-compose.build.yml"
        fi
        if [[ -f "$OUTPUT_DIR/nobuild-mount-summary.json" ]]; then
            cp "$OUTPUT_DIR/nobuild-mount-summary.json" "$data_dir/nobuild-mount-summary.json"
        fi
        if [[ -f "$OUTPUT_DIR/lighthouse-fork-summary.json" ]]; then
            cp "$OUTPUT_DIR/lighthouse-fork-summary.json" "$data_dir/lighthouse-fork-summary.json"
        fi
    fi
}

start_memory_collector() {
    local data_dir="$1"
    log "starting memory collector backend=$MEMORY_BACKEND interval=${MEMORY_INTERVAL}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/collect_node_memory.py" \
        --backend "$MEMORY_BACKEND" \
        --interval "$MEMORY_INTERVAL" \
        --output-dir "$data_dir" \
        > "$data_dir/memory-collector.log" 2>&1 &
    CURRENT_COLLECTOR_PID="$!"
    echo "$CURRENT_COLLECTOR_PID" > "$data_dir/memory-collector.pid"
}

start_block_collector() {
    local data_dir="$1"
    log "starting block metrics collector interval=${BLOCK_INTERVAL}s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/collect_block_metrics.py" \
        --interval "$BLOCK_INTERVAL" \
        --output "$data_dir/block_metrics.csv" \
        > "$data_dir/block-metrics.log" 2>&1 &
    CURRENT_BLOCK_PID="$!"
    echo "$CURRENT_BLOCK_PID" > "$data_dir/block-metrics.pid"
}

start_tx_load() {
    local data_dir="$1"
    if [[ "$TX_RATE" == "0" ]]; then
        log "tx load disabled"
        return 0
    fi
    mkdir -p "$data_dir/tx-load"
    log "starting tx load rate=${TX_RATE}/s"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/send_rand_tx.py" \
        --rate "$TX_RATE" \
        --gas-price-gwei 1 \
        --log-dir "$data_dir/tx-load" \
        > "$data_dir/tx-load/stdout.log" 2>&1 &
    CURRENT_TX_PID="$!"
    echo "$CURRENT_TX_PID" > "$data_dir/tx-load/pid"
}

write_experiment_env() {
    local data_dir="$1"
    local variant="$2"
    {
        echo "run_id=$RUN_PREFIX-$variant"
        echo "variant=$variant"
        echo "beacons=$BEACONS"
        echo "geth=$BEACONS"
        echo "validators=$((BEACONS * 3))"
        echo "stub_as_count=$STUB_AS_COUNT"
        echo "platform=$PLATFORM"
        echo "ether_view=$ETHER_VIEW"
        echo "internet_map=$INTERNET_MAP"
        echo "include_tools=$INCLUDE_TOOLS"
        echo "historic_states=$CFG_HISTORIC"
        echo "cache_profile=$CFG_CACHE"
        echo "beacon_peers=$CFG_BEACON_PEERS"
        echo "beacon_bootnode_peers=${CFG_BOOTNODE_PEERS:-$CFG_BEACON_PEERS}"
        echo "beacon_bootnodes=$CFG_BOOTNODES"
        echo "lighthouse_source_profile=$CFG_LIGHTHOUSE_SOURCE_PROFILE"
        echo "lighthouse_image=${CFG_LIGHTHOUSE_IMAGE:-$LIGHTHOUSE_OFFICIAL_IMAGE}"
        echo "lighthouse_fork_scope=$CFG_LIGHTHOUSE_FORK_SCOPE"
        echo "lighthouse_rayon_threads=$CFG_LIGHTHOUSE_RAYON_THREADS"
        echo "duration_seconds=$DURATION_SECONDS"
        echo "build_mode=$BUILD_MODE"
        echo "build_only=$BUILD_ONLY"
        echo "memory_interval=$MEMORY_INTERVAL"
        echo "block_interval=$BLOCK_INTERVAL"
        echo "tx_rate=$TX_RATE"
        echo "up_preset=$UP_PRESET"
        echo "buildkit=$BUILD_KIT"
        echo "python_bin=$PYTHON_BIN"
        echo "started_at=$(date -Is)"
    } > "$data_dir/experiment.env"
}

capture_final_state() {
    local data_dir="$1"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$SCRIPT_DIR/check_pos_health.sh" > "$data_dir/health-final.log" 2>&1 || true
    docker compose -f "$COMPOSE_FILE" ps > "$data_dir/compose-ps-end.txt" 2>&1 || true
    docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' > "$data_dir/docker-ps-end.txt" 2>&1 || true
}

summarize_results() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$PYTHON_BIN" - "$RUN_DIR" "$SUMMARY_CSV" "$SUMMARY_MD" <<'PY'
import csv
import statistics
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
summary_csv = Path(sys.argv[2])
summary_md = Path(sys.argv[3])

fields = [
    "run_id",
    "variant",
    "beacons",
    "geth",
    "validators",
    "historic_states",
    "cache_profile",
    "beacon_peers",
    "beacon_bootnode_peers",
    "beacon_bootnodes",
    "lighthouse_source_profile",
    "lighthouse_image",
    "lighthouse_fork_scope",
    "lighthouse_rayon_threads",
    "duration_seconds",
    "tx_rate",
    "memory_samples",
    "beacon_containers",
    "beacon_mem_avg_gib_steady",
    "beacon_mem_p95_gib_steady",
    "beacon_mem_avg_gib_last",
    "blocks_seen",
    "non_empty_blocks",
    "tx_in_blocks",
    "tx_sent",
    "tx_failed",
    "health_passed",
]

def read_env(path):
    data = {}
    if not path.exists():
        return data
    for line in path.read_text().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data

def gib(value):
    return float(value) / (1024 ** 3)

def summarize_memory(path):
    if not path.exists():
        return "", "", "", "", ""
    rows = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("node_type") == "beacon":
                rows.append(row)
    if not rows:
        return "", "", "", "", ""
    samples = sorted({int(row["sample"]) for row in rows})
    cutoff = samples[len(samples) // 2] if len(samples) > 1 else samples[0]
    steady = [row for row in rows if int(row["sample"]) >= cutoff]
    last_sample = samples[-1]
    last_rows = [row for row in rows if int(row["sample"]) == last_sample]
    avg_values = [int(row["mem_avg_bytes"]) for row in steady]
    p95_values = [int(row["mem_p95_bytes"]) for row in steady]
    last_avg = int(last_rows[-1]["mem_avg_bytes"]) if last_rows else 0
    containers = int(last_rows[-1]["containers"]) if last_rows else int(rows[-1]["containers"])
    return (
        len(samples),
        containers,
        f"{gib(statistics.mean(avg_values)):.4f}" if avg_values else "",
        f"{gib(statistics.mean(p95_values)):.4f}" if p95_values else "",
        f"{gib(last_avg):.4f}" if last_avg else "",
    )

def summarize_blocks(path):
    blocks = 0
    non_empty = 0
    tx_total = 0
    seen_numbers = set()
    if not path.exists():
        return 0, 0, 0
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("new_block") != "1":
                continue
            number = row.get("block_number")
            if number in seen_numbers:
                continue
            seen_numbers.add(number)
            blocks += 1
            tx_count = int(row.get("tx_count") or 0)
            tx_total += tx_count
            if tx_count > 0:
                non_empty += 1
    return blocks, non_empty, tx_total

def summarize_tx(path):
    sent = 0
    failed = 0
    if not path.exists():
        return 0, 0
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") == "sent":
                sent += 1
            elif row.get("status") == "failed":
                failed += 1
    return sent, failed

summary_rows = []
for data_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
    env = read_env(data_dir / "experiment.env")
    if not env:
        continue
    mem_samples, containers, mem_avg, mem_p95, mem_last = summarize_memory(data_dir / "memory_by_type.csv")
    blocks, non_empty, tx_in_blocks = summarize_blocks(data_dir / "block_metrics.csv")
    tx_sent, tx_failed = summarize_tx(data_dir / "tx-load" / "tx_sender.csv")
    row = {
        "run_id": env.get("run_id", data_dir.name),
        "variant": env.get("variant", data_dir.name),
        "beacons": env.get("beacons", ""),
        "geth": env.get("geth", ""),
        "validators": env.get("validators", ""),
        "historic_states": env.get("historic_states", ""),
        "cache_profile": env.get("cache_profile", ""),
        "beacon_peers": env.get("beacon_peers", ""),
        "beacon_bootnode_peers": env.get("beacon_bootnode_peers", ""),
        "beacon_bootnodes": env.get("beacon_bootnodes", ""),
        "lighthouse_source_profile": env.get("lighthouse_source_profile", ""),
        "lighthouse_image": env.get("lighthouse_image", ""),
        "lighthouse_fork_scope": env.get("lighthouse_fork_scope", ""),
        "lighthouse_rayon_threads": env.get("lighthouse_rayon_threads", ""),
        "duration_seconds": env.get("duration_seconds", ""),
        "tx_rate": env.get("tx_rate", ""),
        "memory_samples": mem_samples,
        "beacon_containers": containers,
        "beacon_mem_avg_gib_steady": mem_avg,
        "beacon_mem_p95_gib_steady": mem_p95,
        "beacon_mem_avg_gib_last": mem_last,
        "blocks_seen": blocks,
        "non_empty_blocks": non_empty,
        "tx_in_blocks": tx_in_blocks,
        "tx_sent": tx_sent,
        "tx_failed": tx_failed,
        "health_passed": int((data_dir / "health-passed.log").exists()),
    }
    summary_rows.append(row)

with summary_csv.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(summary_rows)

md_lines = ["# D61 Beacon Memory Optimization Summary", ""]
if summary_rows:
    cols = [
        "variant",
        "historic_states",
        "cache_profile",
        "beacon_peers",
        "beacon_bootnode_peers",
        "lighthouse_source_profile",
        "beacon_mem_avg_gib_steady",
        "beacon_mem_p95_gib_steady",
        "blocks_seen",
        "non_empty_blocks",
        "tx_sent",
        "health_passed",
    ]
    md_lines.append("| " + " | ".join(cols) + " |")
    md_lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in summary_rows:
        md_lines.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
else:
    md_lines.append("No completed runs found.")
summary_md.write_text("\n".join(md_lines) + "\n")
PY
    log "summary written: $SUMMARY_CSV $SUMMARY_MD"
}

run_variant() {
    local variant="$1"
    local is_last="$2"
    local data_dir="$RUN_DIR/$variant"
    local build_log_dir="$data_dir/build-logs"
    local up_log_dir="$data_dir/up-logs"

    COMPOSE_DOWN_ON_EXIT=1
    variant_config "$variant"
    mkdir -p "$data_dir" "$build_log_dir" "$up_log_dir"
    write_experiment_env "$data_dir" "$variant"

    log "========== variant=$variant =========="
    log "config historic=$CFG_HISTORIC cache=$CFG_CACHE peers=$CFG_BEACON_PEERS bootnode_peers=${CFG_BOOTNODE_PEERS:-$CFG_BEACON_PEERS} bootnodes=$CFG_BOOTNODES lighthouse_profile=$CFG_LIGHTHOUSE_SOURCE_PROFILE lighthouse_image=${CFG_LIGHTHOUSE_IMAGE:-$LIGHTHOUSE_OFFICIAL_IMAGE}"

    cleanup_bg
    compose_down
    generate_output "$data_dir"

    if [[ "$BUILD_MODE" == "nobuild-mount" ]]; then
        log "BUILD_MODE=nobuild-mount: per-node docker compose build skipped"
        if [[ "$DRY_RUN" -eq 0 ]]; then
            {
                echo "build_mode=nobuild-mount"
                echo "docker_build=skipped"
                if [[ -f "$data_dir/nobuild-mount-summary.json" ]]; then
                    "$PYTHON_BIN" - "$data_dir/nobuild-mount-summary.json" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text())
for key in [
    "services_converted",
    "dummy_services_removed",
    "copy_entries",
    "runtime_run_entries",
]:
    print(f"{key}={summary.get(key, '')}")
for image, count in sorted(summary.get("images_used", {}).items()):
    print(f"image[{image}]={count}")
PY
                fi
            } > "$data_dir/build-summary.log"
        fi
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

    if [[ "$BUILD_ONLY" -eq 1 ]]; then
        if [[ -f "$build_log_dir/summary.log" ]]; then
            cp "$build_log_dir/summary.log" "$data_dir/build-summary.log"
        fi
        log "BUILD_ONLY=1: stopping before docker compose up"
        summarize_results
        return 0
    fi

    run_cmd env LOG_DIR="$up_log_dir" "$SCRIPT_DIR/batch_parallel_up.sh" --preset "$UP_PRESET"
    wait_for_health "$data_dir"

    start_block_collector "$data_dir"
    start_tx_load "$data_dir"
    start_memory_collector "$data_dir"

    log "sampling for ${DURATION_SECONDS}s"
    if [[ "$DRY_RUN" -eq 0 ]]; then
        sleep "$DURATION_SECONDS"
    fi

    capture_final_state "$data_dir"
    cleanup_bg

    if [[ -f "$up_log_dir/summary.log" ]]; then
        cp "$up_log_dir/summary.log" "$data_dir/up-summary.log"
    fi
    if [[ -f "$build_log_dir/summary.log" ]]; then
        cp "$build_log_dir/summary.log" "$data_dir/build-summary.log"
    fi

    summarize_results

    if [[ "$KEEP_LAST" -eq 0 || "$is_last" -eq 0 ]]; then
        compose_down
    else
        COMPOSE_DOWN_ON_EXIT=0
    fi
    log "completed variant=$variant"
}

chmod +x "$SCRIPT_DIR/collect_block_metrics.py" "$SCRIPT_DIR/collect_node_memory.py" "$SCRIPT_DIR/send_rand_tx.py" || true

read -r -a VARIANT_ARRAY <<< "$VARIANTS"
if [[ "${#VARIANT_ARRAY[@]}" -eq 0 ]]; then
    echo "No variants specified." >&2
    exit 2
fi

log "beacon optimization comparison started: run_dir=$RUN_DIR variants=[$VARIANTS] beacons=$BEACONS"
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
summarize_results
log "beacon optimization comparison completed"
