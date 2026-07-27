#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_processor_scale_limit_sweep.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

LEVELS="${LEVELS:-300 400 500 600 700 800}"
DURATION_SECONDS="${DURATION_SECONDS:-36000}"
TX_RATE="${TX_RATE:-1}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-1}"
SWEEP_ID="${SWEEP_ID:-$(date +%Y%m%d-%H%M%S)-processor-scale-limit}"
SWEEP_ROOT="${SWEEP_ROOT:-$ETHEREUM_DIR/experiments/processor-scale-limit}"
SWEEP_DIR="$SWEEP_ROOT/$SWEEP_ID"
RUNS_DIR="$SWEEP_DIR/runs"
SUMMARY_CSV="$SWEEP_DIR/summary.csv"
LOG_FILE="$SWEEP_DIR/sweep.log"
DRY_RUN=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Run a serial scale-limit sweep for the Lighthouse processor-only optimization.

Defaults:
  LEVELS="$LEVELS"
  DURATION_SECONDS=$DURATION_SECONDS
  TX_RATE=$TX_RATE
  STOP_ON_FAILURE=$STOP_ON_FAILURE

Options:
  --levels LIST              Space-separated beacon counts.
  --duration SEC             Sampling duration after health passes.
  --tx-rate RATE             Continuous transaction send rate.
  --continue-on-failure      Continue after a failed scale level.
  --dry-run                  Print actions without running them.
  -h, --help                 Show this help.

Important environment variables:
  LEVELS, DURATION_SECONDS, TX_RATE, STOP_ON_FAILURE, SWEEP_ID, SWEEP_ROOT,
  PYTHON_BIN, HEALTH_TIMEOUT, HEALTH_INTERVAL, MEMORY_INTERVAL,
  CONSENSUS_BEACON_SAMPLE, UP_PRESET, BUILD_MODE, LIGHTHOUSE_COMPONENT_IMAGE
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --levels)
            LEVELS="$2"
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

if [[ ! "$DURATION_SECONDS" =~ ^[0-9]+$ ]]; then
    echo "DURATION_SECONDS must be a non-negative integer." >&2
    exit 2
fi
if [[ ! "$STOP_ON_FAILURE" =~ ^[01]$ ]]; then
    echo "STOP_ON_FAILURE must be 0 or 1." >&2
    exit 2
fi

log() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '%s %s\n' "$(date '+%F %T')" "$*"
    else
        printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
    fi
}

auto_stub_as_count() {
    local beacons="$1"
    local needed=$(((beacons + 24) / 25))
    if (( needed < 64 )); then
        needed=64
    fi
    echo "$needed"
}

write_summary_header() {
    if [[ -f "$SUMMARY_CSV" ]]; then
        return
    fi
    cat > "$SUMMARY_CSV" <<EOF
beacons,stub_as_count,status,exit_code,run_id,data_dir,duration_seconds,tx_rate,mem_avg_gib,mem_p95_gib,mem_growth_gib,cpu_p95_percent,block_prop_p95_sec,attest_incl_p95_slots,finality_lag_p95_epochs,tx_send_success_pct,tx_commit_p95_sec,reorg_rate_per_h
EOF
}

append_summary_row() {
    local data_dir="$1"
    local beacons="$2"
    local stub_as_count="$3"
    local status="$4"
    local exit_code="$5"
    local run_id="$6"

    "$PYTHON_BIN" - "$data_dir" "$beacons" "$stub_as_count" "$status" "$exit_code" "$run_id" "$DURATION_SECONDS" "$TX_RATE" "$SUMMARY_CSV" <<'PY'
import csv
import math
import sys
from pathlib import Path

data_dir = Path(sys.argv[1])
beacons = sys.argv[2]
stub_as_count = sys.argv[3]
status = sys.argv[4]
exit_code = sys.argv[5]
run_id = sys.argv[6]
duration_seconds = sys.argv[7]
tx_rate = sys.argv[8]
summary_csv = Path(sys.argv[9])

def percentile(values, pct):
    values = sorted(v for v in values if v is not None and not math.isnan(v))
    if not values:
        return None
    k = (len(values) - 1) * pct / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    return values[f] * (c - k) + values[c] * (k - f)

def read_rows(path):
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))

def fmt(value, digits=2):
    if value is None:
        return ""
    return f"{value:.{digits}f}"

mem = []
cpu = []
for row in read_rows(data_dir / "memory_raw.csv"):
    if row.get("node_type") != "beacon":
        continue
    try:
        mem.append(float(row["mem_usage_bytes"]) / (1024 ** 3))
    except Exception:
        pass
    try:
        cpu.append(float(row["cpu_percent"]))
    except Exception:
        pass

steady = []
for row in read_rows(data_dir / "memory_by_type.csv"):
    if row.get("node_type") != "beacon":
        continue
    try:
        elapsed = float(row.get("elapsed_sec") or 0)
        if elapsed >= 2400:
            steady.append((elapsed, float(row["mem_avg_bytes"]) / (1024 ** 3)))
    except Exception:
        pass
mem_growth = steady[-1][1] - steady[0][1] if steady else None

groups = {}
for row in read_rows(data_dir / "consensus" / "block_first_seen.csv"):
    key = (row.get("head_slot"), row.get("head_root"))
    try:
        elapsed = float(row["elapsed_sec"])
    except Exception:
        continue
    groups.setdefault(key, []).append(elapsed)
block_spreads = [max(v) - min(v) for v in groups.values() if len(v) > 1]

att = []
for row in read_rows(data_dir / "consensus" / "attestation_inclusions.csv"):
    try:
        att.append(float(row["inclusion_delay_slots"]))
    except Exception:
        pass

fin = []
for row in read_rows(data_dir / "consensus" / "finality_samples.csv"):
    try:
        fin.append(float(row["finality_lag"]))
    except Exception:
        pass

tx_total = 0
tx_sent = 0
for row in read_rows(data_dir / "tx-load" / "tx_sender.csv"):
    tx_total += 1
    if row.get("status") == "sent":
        tx_sent += 1
tx_success = (100.0 * tx_sent / tx_total) if tx_total else None

tx_commit = []
for row in read_rows(data_dir / "tx_receipts.csv"):
    value = row.get("commit_latency_observed_sec", "")
    if not value:
        continue
    try:
        tx_commit.append(float(value))
    except Exception:
        pass

reorg_events = read_rows(data_dir / "consensus" / "reorg_events.csv")
try:
    duration_hours = float(duration_seconds) / 3600.0
except Exception:
    duration_hours = 0.0
reorg_rate = (len(reorg_events) / duration_hours) if duration_hours > 0 else None

fieldnames = [
    "beacons",
    "stub_as_count",
    "status",
    "exit_code",
    "run_id",
    "data_dir",
    "duration_seconds",
    "tx_rate",
    "mem_avg_gib",
    "mem_p95_gib",
    "mem_growth_gib",
    "cpu_p95_percent",
    "block_prop_p95_sec",
    "attest_incl_p95_slots",
    "finality_lag_p95_epochs",
    "tx_send_success_pct",
    "tx_commit_p95_sec",
    "reorg_rate_per_h",
]
row = {
    "beacons": beacons,
    "stub_as_count": stub_as_count,
    "status": status,
    "exit_code": exit_code,
    "run_id": run_id,
    "data_dir": str(data_dir),
    "duration_seconds": duration_seconds,
    "tx_rate": tx_rate,
    "mem_avg_gib": fmt(sum(mem) / len(mem), 2) if mem else "",
    "mem_p95_gib": fmt(percentile(mem, 95), 2),
    "mem_growth_gib": fmt(mem_growth, 2),
    "cpu_p95_percent": fmt(percentile(cpu, 95), 1),
    "block_prop_p95_sec": fmt(percentile(block_spreads, 95), 2),
    "attest_incl_p95_slots": fmt(percentile(att, 95), 0),
    "finality_lag_p95_epochs": fmt(percentile(fin, 95), 0),
    "tx_send_success_pct": fmt(tx_success, 1),
    "tx_commit_p95_sec": fmt(percentile(tx_commit, 95), 1),
    "reorg_rate_per_h": fmt(reorg_rate, 2),
}

with summary_csv.open("a", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writerow(row)
PY
}

if [[ "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$RUNS_DIR"
    write_summary_header
fi

read -r -a LEVEL_ARRAY <<< "$LEVELS"
if [[ "${#LEVEL_ARRAY[@]}" -eq 0 ]]; then
    echo "LEVELS is empty." >&2
    exit 2
fi

log "processor scale-limit sweep started"
log "sweep_dir=$SWEEP_DIR"
log "levels=[$LEVELS] duration=${DURATION_SECONDS}s tx_rate=$TX_RATE stop_on_failure=$STOP_ON_FAILURE"

for beacons in "${LEVEL_ARRAY[@]}"; do
    if [[ ! "$beacons" =~ ^[0-9]+$ ]] || (( beacons < 1 )); then
        echo "invalid beacon count: $beacons" >&2
        exit 2
    fi

    stub_as_count="${STUB_AS_COUNT:-$(auto_stub_as_count "$beacons")}"
    if (( stub_as_count > 100 )); then
        log "beacons=$beacons requires stub_as_count=$stub_as_count > 100; marking impossible"
        run_id="b${beacons}-processor"
        data_dir="$RUNS_DIR/$run_id/fork_processor_only"
        mkdir -p "$data_dir"
        append_summary_row "$data_dir" "$beacons" "$stub_as_count" "impossible_stub_as_count" "2" "$run_id"
        if [[ "$STOP_ON_FAILURE" -eq 1 ]]; then
            log "stopping sweep after impossible level beacons=$beacons"
            break
        fi
        continue
    fi

    run_id="b${beacons}-processor"
    data_dir="$RUNS_DIR/$run_id/fork_processor_only"
    log "starting beacons=$beacons stub_as_count=$stub_as_count run_id=$run_id"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "dry-run: would run fork_processor_only at beacons=$beacons"
        continue
    fi

    set +e
    env \
        RUN_ROOT="$RUNS_DIR" \
        RUN_ID="$run_id" \
        BEACONS="$beacons" \
        STUB_AS_COUNT="$stub_as_count" \
        VARIANTS="fork_processor_only" \
        DURATION_SECONDS="$DURATION_SECONDS" \
        TX_RATE="$TX_RATE" \
        PYTHON_BIN="$PYTHON_BIN" \
        KEEP_LAST=0 \
        "$SCRIPT_DIR/run_300_fork_component_benchmark.sh"
    exit_code=$?
    set -e

    if [[ "$exit_code" -eq 0 ]]; then
        status="success"
        log "finished beacons=$beacons status=success"
    else
        status="failed"
        log "finished beacons=$beacons status=failed exit_code=$exit_code"
    fi

    append_summary_row "$data_dir" "$beacons" "$stub_as_count" "$status" "$exit_code" "$run_id"

    if [[ "$exit_code" -ne 0 && "$STOP_ON_FAILURE" -eq 1 ]]; then
        log "stopping sweep after first failed level; set STOP_ON_FAILURE=0 or --continue-on-failure to continue"
        break
    fi
done

log "processor scale-limit sweep completed summary=$SUMMARY_CSV"
