#!/usr/bin/env bash
# Apply runtime host limits explicitly; restore the recorded values when finished.
set -Eeuo pipefail
STATE_FILE="${STATE_FILE:-/run/chainseed-solana-sysctl}"
PARAMS=(net.core.rmem_max net.core.wmem_max net.core.rmem_default net.core.wmem_default
    net.core.netdev_max_backlog fs.nr_open fs.inotify.max_user_instances)
TARGETS=(134217728 134217728 134217728 134217728 250000 1000000 8192)
case "${1:-}" in
    show) sysctl "${PARAMS[@]}" ;;
    apply)
        [[ "$EUID" == 0 ]] || { echo 'Run apply with sudo.' >&2; exit 2; }
        if [[ ! -e "$STATE_FILE" ]]; then
            umask 077
            for key in "${PARAMS[@]}"; do printf '%s=%s\n' "$key" "$(sysctl -n "$key")"; done > "$STATE_FILE"
        fi
        for i in "${!PARAMS[@]}"; do
            current="$(sysctl -n "${PARAMS[$i]}")"
            if (( current < TARGETS[i] )); then sysctl -w "${PARAMS[$i]}=${TARGETS[$i]}"; fi
        done ;;
    restore)
        [[ "$EUID" == 0 ]] || { echo 'Run restore with sudo.' >&2; exit 2; }
        while IFS='=' read -r key value; do
            [[ " ${PARAMS[*]} " == *" $key "* && "$value" =~ ^[0-9]+$ ]] || exit 2
            sysctl -w "$key=$value"
        done < "$STATE_FILE"
        rm -f -- "$STATE_FILE" ;;
    *) echo 'Usage: host_tuning.sh show|apply|restore' >&2; exit 2 ;;
esac
