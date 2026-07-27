#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
    echo "check_solana_health.sh requires bash." >&2
    exit 2
fi

set -uo pipefail

BOOT_NAME_PATTERN="${BOOT_NAME_PATTERN:-Solana-Bootstrap-000}"
BOOT_CONTAINER_NAME="${BOOT_CONTAINER_NAME:-}"
RPC_URL_LOCAL="${RPC_URL_LOCAL:-http://127.0.0.1:8899}"
MAX_WAIT="${MAX_WAIT:-900}"
BOOT_WAIT="${BOOT_WAIT:-$MAX_WAIT}"
RUNNING_WAIT="${RUNNING_WAIT:-$MAX_WAIT}"
SLOT_WAIT="${SLOT_WAIT:-30}"
SLOT_RETRIES="${SLOT_RETRIES:-3}"
EXPECTED_VALIDATORS="${EXPECTED_VALIDATORS:-300}"
MIN_RUNNING_SOLANA="${MIN_RUNNING_SOLANA:-$EXPECTED_VALIDATORS}"
MIN_ACTIVE_SOLANA="${MIN_ACTIVE_SOLANA:-0}"
ACTIVE_WAIT="${ACTIVE_WAIT:-300}"
MIN_VOTING_VALIDATORS="${MIN_VOTING_VALIDATORS:-1}"
MAX_DELINQUENT_VALIDATORS="${MAX_DELINQUENT_VALIDATORS:-}"
STRICT_VALIDATOR_STATUS="${STRICT_VALIDATOR_STATUS:-1}"
REQUIRE_TX_HEALTH="${REQUIRE_TX_HEALTH:-1}"
TX_CHECK_TIMEOUT="${TX_CHECK_TIMEOUT:-180}"
SOLANA_BEST_EFFORT_TIMEOUT="${SOLANA_BEST_EFFORT_TIMEOUT:-30}"
VALIDATOR_STATUS_WAIT="${VALIDATOR_STATUS_WAIT:-$MAX_WAIT}"
VALIDATOR_STATUS_INTERVAL="${VALIDATOR_STATUS_INTERVAL:-30}"
DOCKER_TIMEOUT="${DOCKER_TIMEOUT:-120}"
ACTIVE_COUNT_MODE="${ACTIVE_COUNT_MODE:-process-scan}"
SKIP_DOCKER_RUNNING_COUNT="${SKIP_DOCKER_RUNNING_COUNT:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ -z "$SKIP_DOCKER_RUNNING_COUNT" ]]; then
    if [[ "$ACTIVE_COUNT_MODE" == "process-scan" ]]; then
        SKIP_DOCKER_RUNNING_COUNT=1
    else
        SKIP_DOCKER_RUNNING_COUNT=0
    fi
fi

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
fail() { log "[fail] $*"; exit 1; }

if [[ "$STRICT_VALIDATOR_STATUS" != "0" && "$STRICT_VALIDATOR_STATUS" != "1" ]]; then
    fail "STRICT_VALIDATOR_STATUS must be 0 or 1"
fi

find_boot_container() {
    if [[ -n "$BOOT_CONTAINER_NAME" ]]; then
        printf '%s\n' "$BOOT_CONTAINER_NAME"
        return 0
    fi
    timeout "$DOCKER_TIMEOUT" docker ps --format '{{.Names}}' | grep -m 1 -- "$BOOT_NAME_PATTERN"
}

dump_solana_containers() {
    {
        log "current Solana containers:"
        timeout "$DOCKER_TIMEOUT" docker ps -a --format '{{.Names}} {{.Status}}' \
            | grep -E 'Solana-(Bootstrap|Validator)' \
            | head -n 40 \
            || true
    } >&2
}

wait_for_boot_container() {
    local deadline
    local next_log
    local now
    local cid
    deadline=$(( $(date +%s) + BOOT_WAIT ))
    next_log=0

    while (( $(date +%s) < deadline )); do
        cid="$(find_boot_container || true)"
        if [[ -n "$cid" ]] && timeout "$DOCKER_TIMEOUT" docker exec "$cid" true >/dev/null 2>&1; then
            printf '%s\n' "$cid"
            return 0
        fi

        now=$(date +%s)
        if (( now >= next_log )); then
            log "waiting for bootstrap container matching $BOOT_NAME_PATTERN ..." >&2
            next_log=$((now + 30))
        fi
        sleep 5
    done

    dump_solana_containers
    return 1
}

running_solana_names() {
    timeout "$DOCKER_TIMEOUT" docker ps --format '{{.Names}}' | grep -E 'Solana-(Bootstrap|Validator)' || true
}

running_solana_count() {
    if [[ "$SKIP_DOCKER_RUNNING_COUNT" == "1" ]]; then
        printf '0\n'
        return 0
    fi
    running_solana_names | wc -l | tr -d ' '
}

wait_for_running_solana_count() {
    local min_count="$1"
    local deadline
    local next_log
    local count=0
    deadline=$(( $(date +%s) + RUNNING_WAIT ))
    next_log=0

    while (( $(date +%s) < deadline )); do
        count=$(running_solana_count)
        if (( count >= min_count )); then
            printf '%s\n' "$count"
            return 0
        fi
        if (( $(date +%s) >= next_log )); then
            log "waiting for Solana containers: running=$count expected_min=$min_count"
            next_log=$(( $(date +%s) + 30 ))
        fi
        sleep 5
    done

    printf '%s\n' "$count"
    return 1
}

exec_boot() {
    local cid
    cid="$(find_boot_container)"
    [[ -n "$cid" ]] || return 1
    timeout "$DOCKER_TIMEOUT" docker exec "$cid" "$@"
}

active_agave_count() {
    case "$ACTIVE_COUNT_MODE" in
        process-scan)
            active_agave_count_process_scan
            return $?
            ;;
        running)
            running_solana_count
            return 0
            ;;
        docker-top)
            active_agave_count_docker_top
            return 0
            ;;
        *)
            fail "invalid ACTIVE_COUNT_MODE=$ACTIVE_COUNT_MODE"
            ;;
    esac
}

active_agave_count_docker_top() {
    local active=0
    local name
    while IFS= read -r name; do
        [[ -n "$name" ]] || continue
        if timeout "$DOCKER_TIMEOUT" docker top "$name" -eo pid,ppid,stat,comm,args 2>/dev/null | grep -q 'agave-validator'; then
            active=$((active + 1))
        fi
    done < <(running_solana_names)
    printf '%s\n' "$active"
}

active_agave_count_process_scan() {
    local ps_file
    local status
    ps_file="$(mktemp)" || return 1

    if ! ps -e -o pid=,ppid=,args= > "$ps_file" 2>/dev/null; then
        rm -f "$ps_file"
        return 1
    fi

    "$PYTHON_BIN" - "$ps_file" <<'PY'
import sys
from pathlib import Path

active = 0
for line in Path(sys.argv[1]).read_text(errors="ignore").splitlines():
    if "agave-validator" not in line:
        continue
    if "--identity /opt/solana/config" not in line:
        continue
    active += 1

print(active)
PY
    status=$?
    rm -f "$ps_file"
    return "$status"
}

sol() {
    local tries=0
    local out
    while (( tries < 6 )); do
        if out=$(exec_boot solana --url "$RPC_URL_LOCAL" "$@" 2>/dev/null); then
            printf '%s\n' "$out"
            return 0
        fi
        tries=$((tries + 1))
        sleep 2
    done
    return 1
}

sol_best_effort() {
    local cid
    cid="$(find_boot_container)"
    [[ -n "$cid" ]] || return 1
    timeout "$SOLANA_BEST_EFFORT_TIMEOUT" docker exec "$cid" solana --url "$RPC_URL_LOCAL" "$@" 2>/dev/null || true
}

boot_cid="$(wait_for_boot_container)"
[[ -n "$boot_cid" ]] || fail "bootstrap container not found (pattern: $BOOT_NAME_PATTERN)"
log "[ok] bootstrap container: $boot_cid"

if [[ "$SKIP_DOCKER_RUNNING_COUNT" == "1" ]]; then
    running_solana="skipped"
    log "running Solana container count skipped: SKIP_DOCKER_RUNNING_COUNT=1 active_count_mode=$ACTIVE_COUNT_MODE"
else
    running_solana=$(running_solana_count)
    if [[ "$MIN_RUNNING_SOLANA" =~ ^[0-9]+$ ]] && (( running_solana < MIN_RUNNING_SOLANA )); then
        if ! running_solana=$(wait_for_running_solana_count "$MIN_RUNNING_SOLANA"); then
            log "running Solana containers: $running_solana expected_min=$MIN_RUNNING_SOLANA"
            fail "not enough Solana containers are running"
        fi
    fi
    log "running Solana containers: $running_solana expected_min=$MIN_RUNNING_SOLANA"
fi

log "waiting for RPC at $RPC_URL_LOCAL"
deadline=$(( $(date +%s) + MAX_WAIT ))
version=""
while (( $(date +%s) < deadline )); do
    if version=$(sol cluster-version) && [[ -n "$version" ]]; then
        break
    fi
    sleep 10
done
[[ -n "$version" ]] || fail "RPC did not become ready within ${MAX_WAIT}s"
log "[ok] cluster-version: $version"

if [[ "$MIN_ACTIVE_SOLANA" =~ ^[0-9]+$ ]] && (( MIN_ACTIVE_SOLANA > 0 )); then
    active_deadline=$(( $(date +%s) + ACTIVE_WAIT ))
    active_solana=0
    running_now="skipped"
    while (( $(date +%s) < active_deadline )); do
        if [[ "$SKIP_DOCKER_RUNNING_COUNT" != "1" ]]; then
            running_now=$(running_solana_count)
            if [[ "$MIN_RUNNING_SOLANA" =~ ^[0-9]+$ ]] && (( running_now < MIN_RUNNING_SOLANA )); then
                log "Solana container count below expected during active wait: running=$running_now expected_min=$MIN_RUNNING_SOLANA"
                sleep 10
                continue
            fi
        fi
        if ! active_solana="$(active_agave_count)"; then
            fail "could not count active agave-validator processes with ACTIVE_COUNT_MODE=$ACTIVE_COUNT_MODE"
        fi
        log "active agave-validator processes: $active_solana min=$MIN_ACTIVE_SOLANA running=$running_now"
        if (( active_solana >= MIN_ACTIVE_SOLANA )); then
            break
        fi
        sleep 10
    done
    if (( active_solana < MIN_ACTIVE_SOLANA )); then
        fail "not enough active agave-validator processes"
    fi
fi

slot1=$(sol slot | tr -dc '0-9')
slot1=${slot1:-0}
slot2=0
advanced=0
for attempt in $(seq 1 "$SLOT_RETRIES"); do
    log "slot now: $slot1; waiting ${SLOT_WAIT}s attempt=${attempt}/${SLOT_RETRIES}"
    sleep "$SLOT_WAIT"
    slot2=$(sol slot | tr -dc '0-9')
    slot2=${slot2:-0}
    if (( slot2 > slot1 )); then
        advanced=1
        break
    fi
    slot1="$slot2"
done
if (( advanced == 0 )); then
    exec_boot sh -c 'tail -n 80 /opt/solana/config/bootstrap-validator/*.log' 2>/dev/null || true
    fail "slots did not advance: $slot1 -> $slot2"
fi
log "[ok] slots advancing: $slot1 -> $slot2"

block_height=$(sol_best_effort block-height | tr -dc '0-9')
tx_count_before=$(sol_best_effort transaction-count | tr -dc '0-9')
log "block_height=${block_height:-unknown} transaction_count_before=${tx_count_before:-unknown}"

if [[ "$REQUIRE_TX_HEALTH" == "0" ]]; then
    log "transaction health check skipped: REQUIRE_TX_HEALTH=0"
else
    log "checking transaction processing with a small faucet-key transfer timeout=${TX_CHECK_TIMEOUT}s"
    boot_cid="$(wait_for_boot_container)"
    [[ -n "$boot_cid" ]] || fail "bootstrap container disappeared before tx health check"
    if ! timeout "$((TX_CHECK_TIMEOUT + 30))" docker exec "$boot_cid" env TX_CHECK_TIMEOUT="$TX_CHECK_TIMEOUT" bash -c '
    set -e
    U="http://127.0.0.1:8899"
    PAYER=/opt/solana/config/faucet.json
    KEY=/tmp/seedemu_solana_health_recipient.json
    DEADLINE=$(( $(date +%s) + TX_CHECK_TIMEOUT ))
    [ -f "$PAYER" ] || exit 1
    solana-keygen new --no-passphrase -fso "$KEY" >/dev/null 2>&1
    PK=$(solana-keygen pubkey "$KEY")
    NO_WAIT_ARGS=()
    if solana transfer --help 2>&1 | grep -q -- "--no-wait"; then
        NO_WAIT_ARGS=(--no-wait)
    fi
    UDP_ARGS=()
    if solana transfer --help 2>&1 | grep -q -- "--use-udp"; then
        UDP_ARGS=(--use-udp)
    fi
    attempt=0
    while [ "$(date +%s)" -lt "$DEADLINE" ]; do
        attempt=$((attempt + 1))
        if solana --url "$U" transfer \
            --from "$PAYER" \
            --fee-payer "$PAYER" \
            --allow-unfunded-recipient \
            --skip-preflight \
            --commitment processed \
            "${NO_WAIT_ARGS[@]}" \
            "${UDP_ARGS[@]}" \
            "$PK" 0.001 >/dev/null 2>&1; then
            exit 0
        fi
        sleep 5
    done
    exit 1
    '; then
        fail "test transfer did not submit within ${TX_CHECK_TIMEOUT}s"
    fi
    tx_count_after=$(sol_best_effort transaction-count | tr -dc '0-9')
    log "[ok] transaction processing works: transaction_count=${tx_count_before:-unknown}->${tx_count_after:-unknown}"
fi

validator_deadline=$(( $(date +%s) + VALIDATOR_STATUS_WAIT ))
validators_output=""
voting_validators=0
delinquent_validators=0
validator_status_ok=0

read_validator_status() {
    validators_output="$(sol validators 2>/dev/null || true)"
    voting_validators=$(printf '%s\n' "$validators_output" | grep -ioE '[0-9]+[[:space:]]+current validators?' | grep -oE '^[0-9]+' | head -n 1)
    if [[ -z "$voting_validators" ]]; then
        voting_validators=$(printf '%s\n' "$validators_output" | grep -ioE 'current validators?:[[:space:]]*[0-9]+' | grep -oE '[0-9]+$' | head -n 1)
    fi
    voting_validators=${voting_validators:-0}

    delinquent_validators=$(printf '%s\n' "$validators_output" | grep -ioE '[0-9]+[[:space:]]+delinquent validators?' | grep -oE '^[0-9]+' | head -n 1)
    if [[ -z "$delinquent_validators" ]]; then
        delinquent_validators=$(printf '%s\n' "$validators_output" | grep -ioE 'delinquent validators?:[[:space:]]*[0-9]+' | grep -oE '[0-9]+$' | head -n 1)
    fi
    delinquent_validators=${delinquent_validators:-0}
}

if [[ "$STRICT_VALIDATOR_STATUS" == "0" ]]; then
    if [[ "${MIN_VOTING_VALIDATORS:-0}" == "0" && -z "$MAX_DELINQUENT_VALIDATORS" ]]; then
        log "validator status advisory skipped: strict=0 min_voting_validators=0 max_delinquent_validators=unset"
    else
        read_validator_status
        log "validator status advisory strict=0 voting_validators=$voting_validators min=$MIN_VOTING_VALIDATORS delinquent_validators=$delinquent_validators max=${MAX_DELINQUENT_VALIDATORS:-unset}"
    fi
else
    while (( $(date +%s) < validator_deadline )); do
        read_validator_status

        log "voting_validators=$voting_validators min=$MIN_VOTING_VALIDATORS delinquent_validators=$delinquent_validators max=${MAX_DELINQUENT_VALIDATORS:-unset}"

        validator_status_ok=1
        if [[ "$MIN_VOTING_VALIDATORS" =~ ^[0-9]+$ ]] && (( voting_validators < MIN_VOTING_VALIDATORS )); then
            validator_status_ok=0
        fi
        if [[ -n "$MAX_DELINQUENT_VALIDATORS" ]] && (( delinquent_validators > MAX_DELINQUENT_VALIDATORS )); then
            validator_status_ok=0
        fi
        if (( validator_status_ok == 1 )); then
            break
        fi
        sleep "$VALIDATOR_STATUS_INTERVAL"
    done

    if (( validator_status_ok == 0 )); then
        printf '%s\n' "$validators_output"
        if [[ "$MIN_VOTING_VALIDATORS" =~ ^[0-9]+$ ]] && (( voting_validators < MIN_VOTING_VALIDATORS )); then
            fail "not enough voting validators"
        fi
        fail "too many delinquent validators"
    fi
fi

log "[ok] Solana health check passed"
