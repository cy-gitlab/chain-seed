#!/usr/bin/env python3
"""Continuously send Solana transfer transactions through the bootstrap RPC."""

from __future__ import annotations

import argparse
import csv
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence


CSV_HEADER = ["timestamp", "status", "signature", "message"]


SENDER_SCRIPT = r"""#!/usr/bin/env bash
set -u

U="${RPC_URL:-http://127.0.0.1:8899}"
RATE="${RATE:-1}"
AMOUNT="${AMOUNT:-0.001}"
AIRDROP_SOL="${AIRDROP_SOL:-10000}"
RECIPIENTS="${RECIPIENTS:-128}"
SUBMIT_TIMEOUT="${SUBMIT_TIMEOUT:-90}"
DIR=/tmp/seedemu_solana_tx_load
GENESIS_PAYER_KEY="${GENESIS_PAYER_KEY:-/opt/solana/config/faucet.json}"
PAYER_KEY="$DIR/payer.json"
mkdir -p "$DIR"

if [ -f "$GENESIS_PAYER_KEY" ]; then
  PAYER_KEY="$GENESIS_PAYER_KEY"
else
  if [ ! -f "$PAYER_KEY" ]; then
    solana-keygen new --no-passphrase -fso "$PAYER_KEY" >/dev/null 2>&1
  fi
fi
PAYER=$(solana-keygen pubkey "$PAYER_KEY")

echo "INFO payer=$PAYER rpc=$U rate=$RATE amount=$AMOUNT recipients=$RECIPIENTS submit_timeout=$SUBMIT_TIMEOUT" >&2
if [ "$PAYER_KEY" != "$GENESIS_PAYER_KEY" ]; then
  until solana --url "$U" airdrop "$AIRDROP_SOL" "$PAYER" >/dev/null 2>&1; do
    echo "INFO waiting for faucet airdrop" >&2
    sleep 2
  done
else
  echo "INFO using genesis faucet key as payer; RPC airdrop skipped" >&2
fi

NO_WAIT_ARGS=()
if solana transfer --help 2>&1 | grep -q -- "--no-wait"; then
  NO_WAIT_ARGS=(--no-wait)
fi
UDP_ARGS=()
if solana transfer --help 2>&1 | grep -q -- "--use-udp"; then
  UDP_ARGS=(--use-udp)
fi

SLEEP_SEC=$(awk "BEGIN { if ($RATE <= 0) print 1; else printf \"%.6f\", 1 / $RATE }")
i=0
while true; do
  idx=$((i % RECIPIENTS))
  RECIPIENT_KEY="$DIR/recipient-$idx.json"
  if [ ! -f "$RECIPIENT_KEY" ]; then
    solana-keygen new --no-passphrase -fso "$RECIPIENT_KEY" >/dev/null 2>&1
  fi
  RECIPIENT=$(solana-keygen pubkey "$RECIPIENT_KEY")
  OUT=$(timeout "$SUBMIT_TIMEOUT" solana --url "$U" transfer \
      --from "$PAYER_KEY" \
      --fee-payer "$PAYER_KEY" \
      --allow-unfunded-recipient \
      --skip-preflight \
      --commitment processed \
      "${NO_WAIT_ARGS[@]}" \
      "${UDP_ARGS[@]}" \
      "$RECIPIENT" "$AMOUNT" 2>&1)
  STATUS=$?
  MSG=$(printf '%s' "$OUT" | tr '\t\r\n' ' ' | sed 's/  */ /g')
  SIG=$(printf '%s\n' "$OUT" | grep -Eo '[1-9A-HJ-NP-Za-km-z]{80,100}' | head -n 1 || true)
  TS=$(date -Is)
  if [ "$STATUS" -eq 0 ]; then
    printf 'TX\t%s\tsent\t%s\t%s\n' "$TS" "$SIG" "$MSG"
  else
    printf 'TX\t%s\tfailed\t%s\t%s\n' "$TS" "$SIG" "$MSG"
  fi
  i=$((i + 1))
  sleep "$SLEEP_SEC"
done
"""


def run_cmd(args: list[str], timeout: int = 20) -> str:
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {args}")
    return result.stdout.strip()


def find_boot_container(pattern: str, docker_timeout: int, boot_wait: int) -> str:
    explicit = os.environ.get("BOOT_CONTAINER_NAME", "").strip()
    if explicit:
        return explicit
    deadline = time.time() + boot_wait
    last_error = ""
    while time.time() < deadline:
        try:
            output = run_cmd(["docker", "ps", "--format", "{{.Names}}"], timeout=docker_timeout)
            for name in output.splitlines():
                if pattern in name:
                    return name
            last_error = f"bootstrap container not found: {pattern}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(5)
    raise RuntimeError(last_error or f"bootstrap container not found: {pattern}")


def write_csv_row(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADER)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuously send Solana transfer transactions.")
    parser.add_argument("--boot-pattern", default="Solana-Bootstrap-000")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8899")
    parser.add_argument("--rate", type=float, default=1.0, help="Target transactions per second.")
    parser.add_argument("--amount", default="0.001", help="SOL transferred per tx.")
    parser.add_argument("--airdrop", default="10000", help="Initial SOL airdropped to the payer.")
    parser.add_argument("--recipients", type=int, default=128)
    parser.add_argument(
        "--submit-timeout",
        type=int,
        default=90,
        help="Seconds allowed for one solana transfer invocation before retrying.",
    )
    parser.add_argument("--duration", type=int, default=0, help="Seconds to run. 0 means until interrupted.")
    parser.add_argument("--log-dir", type=Path, default=Path("tx-load") / datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--docker-timeout", type=int, default=120, help="Timeout for Docker CLI calls.")
    parser.add_argument("--boot-wait", type=int, default=300, help="Seconds to wait for the bootstrap container.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.rate < 0:
        print("--rate must be >= 0", file=sys.stderr)
        return 2
    if args.recipients < 1:
        print("--recipients must be >= 1", file=sys.stderr)
        return 2
    if args.docker_timeout < 1 or args.boot_wait < 1:
        print("--docker-timeout and --boot-wait must be >= 1", file=sys.stderr)
        return 2
    if args.submit_timeout < 1:
        print("--submit-timeout must be >= 1", file=sys.stderr)
        return 2

    args.log_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.log_dir / "tx_sender.csv"
    raw_path = args.log_dir / "tx_sender.raw.log"
    run_info = args.log_dir / "run_info.txt"

    boot = find_boot_container(args.boot_pattern, args.docker_timeout, args.boot_wait)
    run_info.write_text(
        "\n".join(
            [
                f"boot_container={boot}",
                f"rpc_url={args.rpc_url}",
                f"rate={args.rate}",
                f"amount={args.amount}",
                f"airdrop={args.airdrop}",
                f"recipients={args.recipients}",
                f"submit_timeout={args.submit_timeout}",
                f"duration={args.duration}",
                f"started_at={datetime.now().astimezone().isoformat(timespec='seconds')}",
            ]
        )
        + "\n"
    )

    env_args = [
        "RPC_URL=" + args.rpc_url,
        "RATE=" + str(args.rate),
        "AMOUNT=" + str(args.amount),
        "AIRDROP_SOL=" + str(args.airdrop),
        "RECIPIENTS=" + str(args.recipients),
        "SUBMIT_TIMEOUT=" + str(args.submit_timeout),
    ]
    process = subprocess.Popen(
        ["docker", "exec", "-i", boot, "env", *env_args, "bash", "-s"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None
    process.stdin.write(SENDER_SCRIPT)
    process.stdin.close()

    stop_requested = False

    def handle_stop(signum, frame):  # type: ignore[no-untyped-def]
        nonlocal stop_requested
        stop_requested = True
        process.terminate()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    deadline = time.time() + args.duration if args.duration else None
    sent = 0
    failed = 0
    print(f"writing tx rows to {csv_path}", flush=True)

    with raw_path.open("a") as raw:
        while True:
            if deadline is not None and time.time() >= deadline:
                process.terminate()
                break
            if stop_requested:
                break
            assert process.stdout is not None
            line = process.stdout.readline()
            if line == "":
                if process.poll() is not None:
                    break
                time.sleep(0.1)
                continue
            raw.write(line)
            raw.flush()
            line = line.rstrip("\n")
            if not line.startswith("TX\t"):
                print(line, flush=True)
                continue
            _, timestamp, status, signature, message = (line.split("\t", 4) + [""])[:5]
            write_csv_row(
                csv_path,
                {
                    "timestamp": timestamp,
                    "status": status,
                    "signature": signature,
                    "message": message,
                },
            )
            if status == "sent":
                sent += 1
            else:
                failed += 1
            print(f"tx status={status} sent={sent} failed={failed} sig={signature}", flush=True)

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

    with run_info.open("a") as handle:
        handle.write(f"finished_at={datetime.now().astimezone().isoformat(timespec='seconds')}\n")
        handle.write(f"sent={sent}\nfailed={failed}\n")
    return 0 if process.returncode in (0, -15, 143, None) else int(process.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
