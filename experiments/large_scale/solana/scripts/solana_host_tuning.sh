#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "solana_host_tuning.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

STATE_FILE="${STATE_FILE:-/tmp/seedemu-solana-host-tuning.env}"

declare -A TARGETS=(
    [net.core.rmem_max]=134217728
    [net.core.wmem_max]=134217728
    [net.core.rmem_default]=134217728
    [net.core.wmem_default]=134217728
    [net.core.netdev_max_backlog]=250000
)

PARAMS=(
    net.core.rmem_max
    net.core.wmem_max
    net.core.rmem_default
    net.core.wmem_default
    net.core.netdev_max_backlog
    net.ipv4.udp_rmem_min
    net.ipv4.udp_wmem_min
)

usage() {
    cat <<EOF
Usage: $(basename "$0") show|apply|restore

Runtime-only host network tuning for large Solana/Agave experiments.
Run with sudo for apply/restore. The original values are saved to:
  $STATE_FILE
EOF
}

show_values() {
    sysctl "${PARAMS[@]}" 2>/dev/null || true
}

save_state() {
    umask 077
    : > "$STATE_FILE"
    for key in "${PARAMS[@]}"; do
        value="$(sysctl -n "$key" 2>/dev/null || true)"
        if [[ -n "$value" ]]; then
            printf '%s=%q\n' "${key//./_}" "$value" >> "$STATE_FILE"
        fi
    done
}

apply_tuning() {
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        echo "apply requires root; rerun with sudo." >&2
        exit 1
    fi
    if [[ ! -f "$STATE_FILE" ]]; then
        save_state
    fi
    for key in "${!TARGETS[@]}"; do
        sysctl -w "$key=${TARGETS[$key]}"
    done
}

restore_tuning() {
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        echo "restore requires root; rerun with sudo." >&2
        exit 1
    fi
    if [[ ! -f "$STATE_FILE" ]]; then
        echo "state file not found: $STATE_FILE" >&2
        exit 1
    fi
    # shellcheck disable=SC1090
    source "$STATE_FILE"
    for key in "${PARAMS[@]}"; do
        var="${key//./_}"
        if [[ -n "${!var:-}" ]]; then
            sysctl -w "$key=${!var}"
        fi
    done
}

action="${1:-show}"
case "$action" in
    show)
        show_values
        ;;
    apply)
        apply_tuning
        show_values
        ;;
    restore)
        restore_tuning
        show_values
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
