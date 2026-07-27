#!/usr/bin/env bash

set -Eeuo pipefail

OUTPUT=""
INTERVAL=60
THRESHOLD_GIB=0
TRIGGER_OUTPUT=""
EVENT_LOG=""
PARENT_PID=""
WHILE_PID=""

usage() {
    cat <<'EOF'
Usage: collect_host_memory.sh --output PATH [options]

Options:
  --interval SEC          Sampling interval. Default: 60.
  --threshold-gib GIB     Trigger when MemAvailable is below this value. 0 disables triggering.
  --trigger-output PATH   File written when the threshold triggers.
  --event-log PATH        Human-readable trigger log.
  --parent-pid PID        PID to TERM when threshold triggers.
  --while-pid PID         Stop sampling when this PID no longer exists.
EOF
}

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        --threshold-gib)
            THRESHOLD_GIB="$2"
            shift 2
            ;;
        --trigger-output)
            TRIGGER_OUTPUT="$2"
            shift 2
            ;;
        --event-log)
            EVENT_LOG="$2"
            shift 2
            ;;
        --parent-pid)
            PARENT_PID="$2"
            shift 2
            ;;
        --while-pid)
            WHILE_PID="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$OUTPUT" ]]; then
    echo "--output is required" >&2
    exit 2
fi
if [[ ! "$INTERVAL" =~ ^[0-9]+$ ]] || [[ "$INTERVAL" -lt 1 ]]; then
    echo "--interval must be a positive integer" >&2
    exit 2
fi
if [[ ! "$THRESHOLD_GIB" =~ ^[0-9]+$ ]]; then
    echo "--threshold-gib must be a non-negative integer" >&2
    exit 2
fi
if [[ -n "$WHILE_PID" && ! "$WHILE_PID" =~ ^[0-9]+$ ]]; then
    echo "--while-pid must be a PID" >&2
    exit 2
fi

mkdir -p "$(dirname "$OUTPUT")"
if [[ ! -s "$OUTPUT" ]]; then
    printf 'timestamp,mem_total_kib,mem_free_kib,mem_available_kib,swap_total_kib,swap_free_kib,load1,load5,load15\n' > "$OUTPUT"
fi

threshold_kib=$((THRESHOLD_GIB * 1024 * 1024))

while true; do
    if [[ -n "$WHILE_PID" ]] && ! kill -0 "$WHILE_PID" 2>/dev/null; then
        exit 0
    fi

    read -r mem_total_kib mem_free_kib available_kib swap_total_kib swap_free_kib < <(
        awk '
            /^MemTotal:/ {mt=$2}
            /^MemFree:/ {mf=$2}
            /^MemAvailable:/ {ma=$2}
            /^SwapTotal:/ {st=$2}
            /^SwapFree:/ {sf=$2}
            END {print mt, mf, ma, st, sf}
        ' /proc/meminfo
    )
    read -r load1 load5 load15 _ < /proc/loadavg || true
    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "$(date -Is)" \
        "$mem_total_kib" \
        "$mem_free_kib" \
        "$available_kib" \
        "$swap_total_kib" \
        "$swap_free_kib" \
        "$load1" \
        "$load5" \
        "$load15" \
        >> "$OUTPUT"

    if [[ "$THRESHOLD_GIB" -gt 0 && "$available_kib" =~ ^[0-9]+$ ]] && (( available_kib < threshold_kib )); then
        if [[ -n "$TRIGGER_OUTPUT" ]]; then
            {
                printf 'timestamp=%s\n' "$(date -Is)"
                printf 'mem_available_kib=%s\n' "$available_kib"
                printf 'threshold_kib=%s\n' "$threshold_kib"
                printf 'threshold_gib=%s\n' "$THRESHOLD_GIB"
            } > "$TRIGGER_OUTPUT"
        fi
        if [[ -n "$EVENT_LOG" ]]; then
            printf '%s host memory watchdog triggered: MemAvailable=%sKiB threshold=%sKiB\n' \
                "$(date '+%Y-%m-%d %H:%M:%S')" "$available_kib" "$threshold_kib" \
                >> "$EVENT_LOG"
        fi
        if [[ -n "$PARENT_PID" ]]; then
            kill -TERM "$PARENT_PID" 2>/dev/null || true
        fi
        exit 0
    fi

    sleep "$INTERVAL"
done
