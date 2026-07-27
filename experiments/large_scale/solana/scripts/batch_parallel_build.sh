#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "batch_parallel_build.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOLANA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
D61_DIR="$(cd "$SOLANA_DIR/.." && pwd)"

export COMPOSE_FILE="${COMPOSE_FILE:-$SOLANA_DIR/output/docker-compose.yml}"
export LOG_DIR="${LOG_DIR:-$SOLANA_DIR/build-logs}"

exec "$D61_DIR/ethereum/scripts/batch_parallel_build.sh" "$@"
