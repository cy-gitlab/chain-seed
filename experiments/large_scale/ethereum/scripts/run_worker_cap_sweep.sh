#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_worker_cap_sweep.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

WORKER_CAPS="${WORKER_CAPS:-1 2 4 8 16}"
BEACONS="${BEACONS:-300}"
STUB_AS_COUNT="${STUB_AS_COUNT:-64}"
DURATION_SECONDS="${DURATION_SECONDS:-36000}"
TX_RATE="${TX_RATE:-1}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-1}"
SWEEP_ID="${SWEEP_ID:-$(date +%Y%m%d-%H%M%S)-worker-cap-sweep}"
SWEEP_ROOT="${SWEEP_ROOT:-$ETHEREUM_DIR/experiments/worker-cap-sweep}"
SWEEP_DIR="$SWEEP_ROOT/$SWEEP_ID"
RUNS_DIR="$SWEEP_DIR/runs"
SUMMARY_CSV="$SWEEP_DIR/summary.csv"
LOG_FILE="$SWEEP_DIR/sweep.log"
LIGHTHOUSE_OFFICIAL_IMAGE="${LIGHTHOUSE_OFFICIAL_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
LIGHTHOUSE_COMPONENT_IMAGE="${LIGHTHOUSE_COMPONENT_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-worker-cap}"
BUILD_FORK_IMAGE="${BUILD_FORK_IMAGE:-0}"
DRY_RUN=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Run a serial 300-beacon worker-cap sweep for the Lighthouse processor-worker optimization.

Defaults:
  WORKER_CAPS="$WORKER_CAPS"
  BEACONS=$BEACONS
  STUB_AS_COUNT=$STUB_AS_COUNT
  DURATION_SECONDS=$DURATION_SECONDS
  TX_RATE=$TX_RATE
  STOP_ON_FAILURE=$STOP_ON_FAILURE
  LIGHTHOUSE_COMPONENT_IMAGE=$LIGHTHOUSE_COMPONENT_IMAGE

Options:
  --worker-caps LIST         Space-separated worker caps, e.g. "1 2 4 8 16".
  --beacons N                Beacon node count. Default: $BEACONS.
  --stub-as-count N          Stub AS count. Default: $STUB_AS_COUNT.
  --duration SEC             Sampling duration after health passes.
  --tx-rate RATE             Continuous transaction send rate.
  --build-fork-image         Build the configurable Lighthouse fork image once first.
  --continue-on-failure      Continue after a failed worker cap.
  --dry-run                  Print actions without running them.
  -h, --help                 Show this help.

Important environment variables:
  WORKER_CAPS, BEACONS, STUB_AS_COUNT, DURATION_SECONDS, TX_RATE,
  STOP_ON_FAILURE, SWEEP_ID, SWEEP_ROOT, PYTHON_BIN, HEALTH_TIMEOUT,
  HEALTH_INTERVAL, MEMORY_INTERVAL, CONSENSUS_BEACON_SAMPLE, UP_PRESET,
  BUILD_MODE, LIGHTHOUSE_OFFICIAL_IMAGE, LIGHTHOUSE_COMPONENT_IMAGE,
  BUILD_FORK_IMAGE
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
        --duration)
            DURATION_SECONDS="$2"
            shift 2
            ;;
        --tx-rate)
            TX_RATE="$2"
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
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for value_name in BEACONS STUB_AS_COUNT DURATION_SECONDS STOP_ON_FAILURE BUILD_FORK_IMAGE; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done
if [[ ! "$STOP_ON_FAILURE" =~ ^[01]$ ]]; then
    echo "STOP_ON_FAILURE must be 0 or 1." >&2
    exit 2
fi
if [[ ! "$BUILD_FORK_IMAGE" =~ ^[01]$ ]]; then
    echo "BUILD_FORK_IMAGE must be 0 or 1." >&2
    exit 2
fi

log() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '%s %s\n' "$(date '+%F %T')" "$*"
    else
        printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
    fi
}

write_summary_header() {
    if [[ -f "$SUMMARY_CSV" ]]; then
        return
    fi
    cat > "$SUMMARY_CSV" <<EOF
worker_cap,beacons,stub_as_count,status,exit_code,run_id,data_dir,duration_seconds,tx_rate,mem_avg_gib,mem_p95_gib,mem_growth_gib,cpu_p95_percent,block_prop_p95_sec,attest_incl_p95_slots,finality_lag_p95_epochs,tx_total,tx_sent,tx_committed,tx_pending,tx_send_success_pct,committed_tps,tx_commit_p95_sec,reorg_rate_per_h
EOF
}

append_summary_row() {
    local data_dir="$1"
    local worker_cap="$2"
    local status="$3"
    local exit_code="$4"
    local run_id="$5"

    "$PYTHON_BIN" - "$data_dir" "$worker_cap" "$BEACONS" "$STUB_AS_COUNT" "$status" "$exit_code" "$run_id" "$DURATION_SECONDS" "$TX_RATE" "$SUMMARY_CSV" <<'PY'
import csv
import json
import math
import sys
from pathlib import Path

data_dir = Path(sys.argv[1])
worker_cap = sys.argv[2]
beacons = sys.argv[3]
stub_as_count = sys.argv[4]
status = sys.argv[5]
exit_code = sys.argv[6]
run_id = sys.argv[7]
duration_seconds = sys.argv[8]
tx_rate = sys.argv[9]
summary_csv = Path(sys.argv[10])

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

def read_json(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}

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

run_info = read_json(data_dir / "tx_receipts_run_info.json")
tx_committed = run_info.get("committed_transactions")
tx_pending = run_info.get("pending_transactions")
if tx_committed is None:
    tx_committed = len(tx_commit)
try:
    committed_tps = float(tx_committed) / float(duration_seconds)
except Exception:
    committed_tps = None

reorg_events = read_rows(data_dir / "consensus" / "reorg_events.csv")
try:
    duration_hours = float(duration_seconds) / 3600.0
except Exception:
    duration_hours = 0.0
reorg_rate = (len(reorg_events) / duration_hours) if duration_hours > 0 else None

fieldnames = [
    "worker_cap",
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
    "tx_total",
    "tx_sent",
    "tx_committed",
    "tx_pending",
    "tx_send_success_pct",
    "committed_tps",
    "tx_commit_p95_sec",
    "reorg_rate_per_h",
]
row = {
    "worker_cap": worker_cap,
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
    "tx_total": tx_total,
    "tx_sent": tx_sent,
    "tx_committed": tx_committed if tx_committed is not None else "",
    "tx_pending": tx_pending if tx_pending is not None else "",
    "tx_send_success_pct": fmt(tx_success, 1),
    "committed_tps": fmt(committed_tps, 3),
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

read -r -a CAP_ARRAY <<< "$WORKER_CAPS"
if [[ "${#CAP_ARRAY[@]}" -eq 0 ]]; then
    echo "WORKER_CAPS is empty." >&2
    exit 2
fi

log "worker-cap sweep started"
log "sweep_dir=$SWEEP_DIR"
log "caps=[$WORKER_CAPS] beacons=$BEACONS duration=${DURATION_SECONDS}s tx_rate=$TX_RATE stop_on_failure=$STOP_ON_FAILURE"

if [[ "$DRY_RUN" -eq 0 && "$BUILD_FORK_IMAGE" -eq 1 ]]; then
    log "building configurable Lighthouse fork image=$LIGHTHOUSE_COMPONENT_IMAGE"
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

    run_id="b${BEACONS}-workers${worker_cap}"
    data_dir="$RUNS_DIR/$run_id/fork_processor_workers_only"
    log "starting worker_cap=$worker_cap run_id=$run_id"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "dry-run: would run worker_cap=$worker_cap"
        continue
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
        DURATION_SECONDS="$DURATION_SECONDS" \
        TX_RATE="$TX_RATE" \
        PYTHON_BIN="$PYTHON_BIN" \
        BUILD_FORK_IMAGE=0 \
        KEEP_LAST=0 \
        "$SCRIPT_DIR/run_300_fork_component_benchmark.sh"
    exit_code=$?
    set -e

    if [[ "$exit_code" -eq 0 ]]; then
        status="success"
        log "finished worker_cap=$worker_cap status=success"
    else
        status="failed"
        log "finished worker_cap=$worker_cap status=failed exit_code=$exit_code"
    fi

    append_summary_row "$data_dir" "$worker_cap" "$status" "$exit_code" "$run_id"

    if [[ "$exit_code" -ne 0 && "$STOP_ON_FAILURE" -eq 1 ]]; then
        log "stopping sweep after first failed worker cap; set STOP_ON_FAILURE=0 or --continue-on-failure to continue"
        break
    fi
done

log "worker-cap sweep completed summary=$SUMMARY_CSV"
