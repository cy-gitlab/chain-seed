#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_10_beacon_single_variable_compare.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

export BEACONS="${BEACONS:-10}"
export STUB_AS_COUNT="${STUB_AS_COUNT:-10}"
export DURATION_SECONDS="${DURATION_SECONDS:-1800}"
export MEMORY_INTERVAL="${MEMORY_INTERVAL:-60}"
export BLOCK_INTERVAL="${BLOCK_INTERVAL:-12}"
export TX_RATE="${TX_RATE:-1}"
export VARIANTS="${VARIANTS:-baseline_all no_historic low_cache low_peers fork_lite}"
export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-0}"
export BUILD_MODE="${BUILD_MODE:-nobuild-mount}"
export UP_PRESET="${UP_PRESET:-fast}"
export MEMORY_BACKEND="${MEMORY_BACKEND:-cgroup-scan}"
export ETHER_VIEW="${ETHER_VIEW:-1}"
export INTERNET_MAP="${INTERNET_MAP:-1}"
export INCLUDE_TOOLS="${INCLUDE_TOOLS:-0}"
export PYTHON_BIN

if [[ -z "${RUN_PREFIX:-}" ]]; then
    export RUN_PREFIX
    RUN_PREFIX="$(date +%Y%m%d-%H%M%S)-b${BEACONS}-single-var"
fi

RUN_DIR="$ETHEREUM_DIR/experiments/$RUN_PREFIX"

echo "10-beacon single-variable experiment"
echo "run_dir=$RUN_DIR"
echo "variants=[$VARIANTS]"
echo "beacons=$BEACONS geth=$BEACONS validators=$((BEACONS * 3))"
echo "duration_seconds=$DURATION_SECONDS tx_rate=$TX_RATE build_mode=$BUILD_MODE"

"$SCRIPT_DIR/run_beacon_memory_optimization_compare.sh" "$@"
