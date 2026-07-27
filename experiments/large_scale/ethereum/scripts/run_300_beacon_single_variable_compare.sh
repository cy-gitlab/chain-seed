#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_300_beacon_single_variable_compare.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

# Large-scale single-variable defaults. Override through environment variables
# only when intentionally changing the experiment design.
export BEACONS="${BEACONS:-300}"
export STUB_AS_COUNT="${STUB_AS_COUNT:-64}"
export DURATION_SECONDS="${DURATION_SECONDS:-36000}"
export MEMORY_INTERVAL="${MEMORY_INTERVAL:-60}"
export BLOCK_INTERVAL="${BLOCK_INTERVAL:-12}"
export TX_RATE="${TX_RATE:-1}"
export VARIANTS="${VARIANTS:-baseline_all no_historic low_cache_only low_peers_only baseline_all_fork_lite}"
export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-0}"
export BUILD_BATCH_SIZE="${BUILD_BATCH_SIZE:-50}"
export BUILD_JOBS="${BUILD_JOBS:-4}"
export BUILD_RETRY="${BUILD_RETRY:-1}"
export BUILD_MODE="${BUILD_MODE:-nobuild-mount}"
export UP_PRESET="${UP_PRESET:-fast}"
export MEMORY_BACKEND="${MEMORY_BACKEND:-cgroup-scan}"
export ETHER_VIEW="${ETHER_VIEW:-1}"
export INTERNET_MAP="${INTERNET_MAP:-1}"
export INCLUDE_TOOLS="${INCLUDE_TOOLS:-0}"
export LOW_PEER_NORMAL="${LOW_PEER_NORMAL:-6}"
export LOW_PEER_BOOTNODE="${LOW_PEER_BOOTNODE:-24}"
export LOW_PEER_BOOTNODES="${LOW_PEER_BOOTNODES:-6}"
export PYTHON_BIN

if [[ -z "${RUN_PREFIX:-}" ]]; then
    export RUN_PREFIX
    RUN_PREFIX="$(date +%Y%m%d-%H%M%S)-b${BEACONS}-single-var-10h"
fi

RUN_DIR="$ETHEREUM_DIR/experiments/$RUN_PREFIX"

echo "large single-variable experiment"
echo "run_dir=$RUN_DIR"
echo "variants=[$VARIANTS]"
echo "beacons=$BEACONS geth=$BEACONS validators=$((BEACONS * 3))"
echo "duration_seconds=$DURATION_SECONDS tx_rate=$TX_RATE build_mode=$BUILD_MODE docker_buildkit=$DOCKER_BUILDKIT"

"$SCRIPT_DIR/run_beacon_memory_optimization_compare.sh" "$@"

if [[ -f "$RUN_DIR/summary.csv" ]]; then
    "$PYTHON_BIN" "$SCRIPT_DIR/summarize_single_variable_effects.py" "$RUN_DIR"
else
    echo "summary.csv not found; skipping single-variable effect summary"
fi

echo "single-variable effect summary:"
echo "  $RUN_DIR/single_variable_effects.csv"
echo "  $RUN_DIR/single_variable_effects.md"
