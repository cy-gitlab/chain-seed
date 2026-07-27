#!/usr/bin/env bash

set -Eeuo pipefail

GETH_INDEX="${GETH_INDEX:-000}"
BEACON_INDEX="${BEACON_INDEX:-000}"
VALIDATOR_INDEX="${VALIDATOR_INDEX:-000}"
EXPECTED_ACTIVE_VALIDATORS="${EXPECTED_ACTIVE_VALIDATORS:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! "$EXPECTED_ACTIVE_VALIDATORS" =~ ^[0-9]+$ ]]; then
    echo "EXPECTED_ACTIVE_VALIDATORS must be a non-negative integer" >&2
    exit 2
fi

find_container() {
    local pattern="$1"
    local container
    container="$(docker ps --format '{{.Names}}' | grep -m 1 "$pattern" || true)"
    if [[ -z "$container" ]]; then
        echo "container matching '$pattern' not found" >&2
        exit 1
    fi
    printf '%s\n' "$container"
}

container_ip() {
    docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$1"
}

hex_to_dec() {
    local value="$1"
    value="${value#0x}"
    printf '%d\n' "0x${value:-0}"
}

GETH_CONTAINER="$(find_container "Ethereum-POS-Geth-${GETH_INDEX}")"
BEACON_CONTAINER="$(find_container "Ethereum-POS-Beacon-${BEACON_INDEX}")"
VALIDATOR_CONTAINER="$(find_container "Ethereum-POS-Validator-${VALIDATOR_INDEX}")"

GETH_IP="$(container_ip "$GETH_CONTAINER")"
BEACON_IP="$(container_ip "$BEACON_CONTAINER")"

echo "geth=$GETH_CONTAINER ip=$GETH_IP"
echo "beacon=$BEACON_CONTAINER ip=$BEACON_IP"
echo "validator=$VALIDATOR_CONTAINER"

echo
echo "[1/5] beacon -> geth engine API"
docker exec "$BEACON_CONTAINER" sh -lc "ping -c 2 -W 2 '$GETH_IP' >/dev/null && nc -vz -w 3 '$GETH_IP' 8551"

echo
echo "[2/5] validator -> beacon HTTP API"
docker exec "$VALIDATOR_CONTAINER" sh -lc "ping -c 2 -W 2 '$BEACON_IP' >/dev/null && nc -vz -w 3 '$BEACON_IP' 8000"

echo
echo "[3/5] geth block number"
BLOCK_HEX="$(
    docker exec "$GETH_CONTAINER" sh -lc \
        "curl -sS --max-time 5 -X POST -H 'Content-Type: application/json' --data '{\"jsonrpc\":\"2.0\",\"method\":\"eth_blockNumber\",\"params\":[],\"id\":1}' http://127.0.0.1:8545" \
        | sed -n 's/.*"result":"\([^"]*\)".*/\1/p'
)"
BLOCK_DEC="$(hex_to_dec "${BLOCK_HEX:-0x0}")"
echo "eth_blockNumber=$BLOCK_HEX ($BLOCK_DEC)"

echo
echo "[4/5] recent block-production signals"
docker logs --tail 80 "$BEACON_CONTAINER" 2>&1 | grep -E "Valid block|Signed block|exec_hash|Not ready Bellatrix|Network is unreachable" || true
docker logs --tail 80 "$VALIDATOR_CONTAINER" 2>&1 | grep -E "Successfully published block|All validators active|No synced beacon nodes|Failed|ERROR|ERRO" || true

if [[ "$BLOCK_DEC" -lt 1 ]]; then
    echo "health check failed: eth_blockNumber is still zero" >&2
    exit 1
fi

echo
echo "[5/5] active validator registry"
if [[ "$EXPECTED_ACTIVE_VALIDATORS" -gt 0 ]]; then
    ACTIVE_COUNT="$(
        curl --noproxy '*' -sS --max-time 30 \
            "http://${BEACON_IP}:8000/eth/v1/beacon/states/head/validators?status=active_ongoing" \
            | "$PYTHON_BIN" -c 'import json, sys; print(len(json.load(sys.stdin).get("data", [])))'
    )"
    ACTIVE_COUNT="${ACTIVE_COUNT//[[:space:]]/}"
    ACTIVE_COUNT="${ACTIVE_COUNT:-0}"
    echo "active_validators=$ACTIVE_COUNT expected=$EXPECTED_ACTIVE_VALIDATORS"
    if [[ "$ACTIVE_COUNT" -ne "$EXPECTED_ACTIVE_VALIDATORS" ]]; then
        echo "health check failed: active validator count mismatch" >&2
        exit 1
    fi
else
    echo "active validator count check skipped"
fi

echo
echo "health check passed"
