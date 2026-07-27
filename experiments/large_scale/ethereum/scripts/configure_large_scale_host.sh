#!/usr/bin/env bash

set -Eeuo pipefail

INOTIFY_INSTANCES=8192
SYSCTL_FILE="/etc/sysctl.d/99-seedemu-ethereum-large-scale.conf"

if [[ ! "$INOTIFY_INSTANCES" =~ ^[0-9]+$ ]] || (( INOTIFY_INSTANCES < 1024 )); then
    echo "INOTIFY_INSTANCES must be an integer of at least 1024." >&2
    exit 2
fi

if (( EUID != 0 )); then
    exec sudo "$0" "$@"
fi

temporary_file="$(mktemp)"
trap 'rm -f "$temporary_file"' EXIT

printf '%s\n' \
    "# SEED Emulator Ethereum large-scale experiment limits." \
    "fs.inotify.max_user_instances = $INOTIFY_INSTANCES" \
    > "$temporary_file"

install -m 0644 "$temporary_file" "$SYSCTL_FILE"
sysctl -p "$SYSCTL_FILE"

actual="$(sysctl -n fs.inotify.max_user_instances)"
if (( actual < INOTIFY_INSTANCES )); then
    echo "failed to apply fs.inotify.max_user_instances=$INOTIFY_INSTANCES" >&2
    exit 1
fi

echo "configured fs.inotify.max_user_instances=$actual"
echo "persistent configuration: $SYSCTL_FILE"
