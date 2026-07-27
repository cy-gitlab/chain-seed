#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "run_solana_stability_sweep.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOLANA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-/home/bruce/miniconda3/envs/seedpy310/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

VALIDATOR_LEVELS="${VALIDATOR_LEVELS:-50 75 100 125 150 175 200 250 300}"
STAKED_VALIDATORS="${STAKED_VALIDATORS:-20}"
STUB_AS_COUNT="${STUB_AS_COUNT:-64}"
DURATION_SECONDS="${DURATION_SECONDS:-1800}"
TX_RATE="${TX_RATE:-1}"
UP_PRESET="${UP_PRESET:-conservative}"
ACTIVE_RATIO="${ACTIVE_RATIO:-90}"
ACTIVE_WAIT="${ACTIVE_WAIT:-300}"
SLOT_STALL_TIMEOUT="${SLOT_STALL_TIMEOUT:-300}"
SLOT_STALL_GRACE="${SLOT_STALL_GRACE:-300}"
SLOT_STALL_CHECK_INTERVAL="${SLOT_STALL_CHECK_INTERVAL:-60}"
REQUIRE_TX_HEALTH="${REQUIRE_TX_HEALTH:-1}"
SKIP_BASE_BUILD="${SKIP_BASE_BUILD:-1}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-1}"
RUN_PREFIX_BASE="${RUN_PREFIX_BASE:-solana-stability-$(date +%Y%m%d-%H%M%S)}"
SUMMARY_CSV="${SUMMARY_CSV:-$SOLANA_DIR/experiments/${RUN_PREFIX_BASE}-summary.csv}"

usage() {
    cat <<EOF
Usage: $(basename "$0")

Environment overrides:
  VALIDATOR_LEVELS="$VALIDATOR_LEVELS"
  STAKED_VALIDATORS=$STAKED_VALIDATORS
  STUB_AS_COUNT=$STUB_AS_COUNT
  DURATION_SECONDS=$DURATION_SECONDS
  TX_RATE=$TX_RATE
  UP_PRESET=$UP_PRESET
  ACTIVE_RATIO=$ACTIVE_RATIO
  ACTIVE_WAIT=$ACTIVE_WAIT
  SLOT_STALL_TIMEOUT=$SLOT_STALL_TIMEOUT
  SLOT_STALL_GRACE=$SLOT_STALL_GRACE
  SLOT_STALL_CHECK_INTERVAL=$SLOT_STALL_CHECK_INTERVAL
  REQUIRE_TX_HEALTH=$REQUIRE_TX_HEALTH
  SKIP_BASE_BUILD=$SKIP_BASE_BUILD
  STOP_ON_FAILURE=$STOP_ON_FAILURE
  RUN_PREFIX_BASE=$RUN_PREFIX_BASE
  SUMMARY_CSV=$SUMMARY_CSV
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

for value_name in STUB_AS_COUNT DURATION_SECONDS ACTIVE_RATIO ACTIVE_WAIT SLOT_STALL_TIMEOUT SLOT_STALL_GRACE SLOT_STALL_CHECK_INTERVAL STOP_ON_FAILURE; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "$value_name must be a non-negative integer." >&2
        exit 2
    fi
done
if [[ ! "$STAKED_VALIDATORS" =~ ^-?[0-9]+$ ]] || (( STAKED_VALIDATORS < -1 )); then
    echo "STAKED_VALIDATORS must be -1 or a non-negative integer." >&2
    exit 2
fi

mkdir -p "$(dirname "$SUMMARY_CSV")"

append_summary() {
    local summary_csv="$1"
    local run_prefix="$2"
    local validators="$3"
    local staked="$4"
    local exit_status="$5"
    local data_dir="$6"

    "$PYTHON_BIN" - "$summary_csv" "$run_prefix" "$validators" "$staked" "$exit_status" "$data_dir" <<'PY'
import csv
import sys
from pathlib import Path

summary = Path(sys.argv[1])
run_prefix = sys.argv[2]
validators = int(sys.argv[3])
staked = sys.argv[4]
exit_status = int(sys.argv[5])
data_dir = Path(sys.argv[6])

fields = [
    "run_prefix",
    "validators",
    "staked_validators",
    "exit_status",
    "data_dir",
    "samples",
    "elapsed_sec",
    "slot_delta",
    "block_height_delta",
    "tx_count_delta",
    "slot_changes",
    "last_voting_validators",
    "tx_rows",
    "tx_sent",
    "tx_failed",
    "last_active_validators",
    "max_validator_gib",
    "max_bootstrap_gib",
    "stall_detected",
]

def read_rows(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))

def to_int(value):
    try:
        return int(str(value).strip())
    except Exception:
        return None

def to_float(value):
    try:
        return float(str(value).strip())
    except Exception:
        return None

block_rows = [row for row in read_rows(data_dir / "block_metrics.csv") if row.get("slot")]
samples = len(block_rows)
elapsed = ""
slot_delta = ""
height_delta = ""
tx_delta = ""
slot_changes = 0
last_voting = ""
if block_rows:
    first = block_rows[0]
    last = block_rows[-1]
    elapsed = last.get("elapsed_sec", "")
    first_slot = to_int(first.get("slot"))
    last_slot = to_int(last.get("slot"))
    first_height = to_int(first.get("block_height"))
    last_height = to_int(last.get("block_height"))
    first_tx = to_int(first.get("transaction_count"))
    last_tx = to_int(last.get("transaction_count"))
    if first_slot is not None and last_slot is not None:
        slot_delta = last_slot - first_slot
    if first_height is not None and last_height is not None:
        height_delta = last_height - first_height
    if first_tx is not None and last_tx is not None:
        tx_delta = last_tx - first_tx
    prev_slot = first_slot
    for row in block_rows[1:]:
        slot = to_int(row.get("slot"))
        if slot is not None and prev_slot is not None and slot != prev_slot:
            slot_changes += 1
        if slot is not None:
            prev_slot = slot
    last_voting = last.get("voting_validators", "")

tx_rows = read_rows(data_dir / "tx-load" / "tx_sender.csv")
tx_sent = sum(1 for row in tx_rows if row.get("status") == "sent")
tx_failed = sum(1 for row in tx_rows if row.get("status") and row.get("status") != "sent")

last_active = ""
max_validator_gib = ""
max_bootstrap_gib = ""
memory_rows = read_rows(data_dir / "memory_by_type.csv")
validator_rows = [row for row in memory_rows if row.get("node_type") == "solana_validator"]
bootstrap_rows = [row for row in memory_rows if row.get("node_type") == "solana_bootstrap"]
if validator_rows:
    last_active = validator_rows[-1].get("containers", "")
    max_bytes = max((to_int(row.get("mem_total_bytes")) or 0) for row in validator_rows)
    max_validator_gib = round(max_bytes / (1024 ** 3), 3)
if bootstrap_rows:
    max_bytes = max((to_int(row.get("mem_total_bytes")) or 0) for row in bootstrap_rows)
    max_bootstrap_gib = round(max_bytes / (1024 ** 3), 3)

row = {
    "run_prefix": run_prefix,
    "validators": validators,
    "staked_validators": staked,
    "exit_status": exit_status,
    "data_dir": str(data_dir),
    "samples": samples,
    "elapsed_sec": elapsed,
    "slot_delta": slot_delta,
    "block_height_delta": height_delta,
    "tx_count_delta": tx_delta,
    "slot_changes": slot_changes,
    "last_voting_validators": last_voting,
    "tx_rows": len(tx_rows),
    "tx_sent": tx_sent,
    "tx_failed": tx_failed,
    "last_active_validators": last_active,
    "max_validator_gib": max_validator_gib,
    "max_bootstrap_gib": max_bootstrap_gib,
    "stall_detected": int((data_dir / "stall-detected.log").exists()),
}

needs_header = not summary.exists() or summary.stat().st_size == 0
with summary.open("a", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    if needs_header:
        writer.writeheader()
    writer.writerow(row)
PY
}

printf 'summary=%s\n' "$SUMMARY_CSV"
read -r -a validator_levels <<< "$VALIDATOR_LEVELS"

for validators in "${validator_levels[@]}"; do
    if [[ ! "$validators" =~ ^[0-9]+$ ]] || (( validators < 1 )); then
        echo "invalid validator count in VALIDATOR_LEVELS: $validators" >&2
        exit 2
    fi

    staked="$STAKED_VALIDATORS"
    if (( staked > validators - 1 )); then
        staked=$((validators - 1))
    fi
    if (( staked < 0 )); then
        stake_label="all"
    else
        stake_label="$staked"
    fi

    min_active=$(((validators * ACTIVE_RATIO + 99) / 100))
    run_prefix="${RUN_PREFIX_BASE}-v${validators}-stake${stake_label}"
    data_dir="$SOLANA_DIR/experiments/$run_prefix/solana-large"

    printf '%s running validators=%s staked=%s min_active=%s duration=%ss\n' \
        "$(date '+%F %T')" "$validators" "$staked" "$min_active" "$DURATION_SECONDS"

    if env \
        RUN_PREFIX="$run_prefix" \
        VALIDATORS="$validators" \
        STUB_AS_COUNT="$STUB_AS_COUNT" \
        SOLANA_STAKED_VALIDATORS="$staked" \
        DURATION_SECONDS="$DURATION_SECONDS" \
        TX_RATE="$TX_RATE" \
        UP_PRESET="$UP_PRESET" \
        MIN_ACTIVE_SOLANA="$min_active" \
        ACTIVE_WAIT="$ACTIVE_WAIT" \
        SLOT_STALL_TIMEOUT="$SLOT_STALL_TIMEOUT" \
        SLOT_STALL_GRACE="$SLOT_STALL_GRACE" \
        SLOT_STALL_CHECK_INTERVAL="$SLOT_STALL_CHECK_INTERVAL" \
        REQUIRE_TX_HEALTH="$REQUIRE_TX_HEALTH" \
        SKIP_BASE_BUILD="$SKIP_BASE_BUILD" \
        PYTHON_BIN="$PYTHON_BIN" \
        "$SCRIPT_DIR/run_solana_large_experiment.sh"; then
        status=0
    else
        status=$?
    fi

    append_summary "$SUMMARY_CSV" "$run_prefix" "$validators" "$staked" "$status" "$data_dir"
    printf '%s finished validators=%s status=%s summary=%s\n' \
        "$(date '+%F %T')" "$validators" "$status" "$SUMMARY_CSV"

    if (( status != 0 && STOP_ON_FAILURE == 1 )); then
        echo "stopping sweep after first failed level: validators=$validators status=$status; set STOP_ON_FAILURE=0 to continue." >&2
        exit "$status"
    fi
done
