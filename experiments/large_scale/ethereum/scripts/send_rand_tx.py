#!/usr/bin/env python3
"""Continuously broadcast random ETH transfers for the D61 PoS experiment.

The sender intentionally does not wait for transaction receipts. It keeps a
local pending nonce cache so the same account can submit multiple transactions
before previous ones are included in blocks.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from compose_endpoints import discover_execution_rpc_url


DEFAULT_CHAIN_ID = 1337
DEFAULT_ACCOUNT_COUNT = 20
DEFAULT_MNEMONIC = "gentle always fun glass foster produce north tail security list example gain"
DEFAULT_COMPOSE_FILE = Path(__file__).resolve().parent.parent / "output" / "docker-compose.yml"
CSV_FIELDS = [
    "timestamp",
    "elapsed_sec",
    "seq",
    "status",
    "tx_hash",
    "sender_index",
    "recipient_index",
    "sender",
    "recipient",
    "nonce",
    "amount_wei",
    "gas_price_wei",
    "error",
]


@dataclass
class Counters:
    sent: int = 0
    failed: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send random raw ETH transfers forever by default. The script "
            "does not wait for confirmations."
        )
    )
    parser.add_argument(
        "--rpc-url",
        help=(
            "Execution-layer RPC URL. If omitted, the script discovers EL_HOST "
            "or Ethereum-POS-Geth-000 from output/docker-compose.yml."
        ),
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=DEFAULT_COMPOSE_FILE,
        help=f"Compose file used for RPC discovery. Default: {DEFAULT_COMPOSE_FILE}",
    )
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID)
    parser.add_argument("--mnemonic", default=DEFAULT_MNEMONIC)
    parser.add_argument(
        "--accounts",
        type=int,
        default=DEFAULT_ACCOUNT_COUNT,
        help=(
            "Number of HD wallet accounts to use. D61 currently pre-funds 20 "
            "accounts in ethereum_pos.py."
        ),
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="Target transactions per second. Default: 1.0.",
    )
    parser.add_argument(
        "--amount-min",
        default="0.000001",
        help="Minimum transfer amount in ETH. Default: 0.000001.",
    )
    parser.add_argument(
        "--amount-max",
        default="0.0001",
        help="Maximum transfer amount in ETH. Default: 0.0001.",
    )
    parser.add_argument("--gas", type=int, default=21000)
    parser.add_argument(
        "--gas-price-gwei",
        type=decimal_arg,
        help="Fixed legacy gas price in gwei. Default: query eth_gasPrice each tx.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Stop after this many seconds. Default: run until Ctrl+C.",
    )
    parser.add_argument(
        "--max-tx",
        type=int,
        help="Stop after this many successful transaction broadcasts.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=30.0,
        help="Print aggregate status every N seconds. Default: 30.",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=0,
        help="Exit non-zero after N consecutive broadcast failures; 0 disables the limit.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("tx-load") / datetime.now().strftime("%Y%m%d-%H%M%S"),
        help="Directory for tx_sender.csv and run_info.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve config and connect to RPC, but do not send transactions.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every accepted transaction hash.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def decimal_arg(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid decimal value: {value}") from exc


def discover_rpc_url(compose_file: Path) -> str:
    return discover_execution_rpc_url(compose_file)


def parse_eth_amount(value: str, name: str) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal ETH amount: {value}") from exc

    if amount <= 0:
        raise ValueError(f"{name} must be positive")
    return int(amount * Decimal(10**18))


def raw_transaction_bytes(signed_txn) -> bytes:
    if hasattr(signed_txn, "raw_transaction"):
        return signed_txn.raw_transaction
    return signed_txn.rawTransaction


def build_accounts(mnemonic: str, total: int) -> list:
    if total < 2:
        raise ValueError("--accounts must be at least 2")

    try:
        from eth_account import Account
    except ModuleNotFoundError as exc:
        raise RuntimeError("missing Python dependency: eth-account") from exc

    Account.enable_unaudited_hdwallet_features()
    accounts = []
    for index in range(total):
        path = f"m/44'/60'/0'/0/{index}"
        accounts.append(Account.from_mnemonic(mnemonic, account_path=path))
    return accounts


def connect(rpc_url: str, timeout: int = 10):
    try:
        from web3 import Web3
    except ModuleNotFoundError as exc:
        raise RuntimeError("missing Python dependency: web3") from exc

    provider = Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout})
    w3 = Web3(provider)
    is_connected = getattr(w3, "is_connected", None) or getattr(w3, "isConnected", None)
    if is_connected is None:
        raise RuntimeError("unsupported web3.py version: no connection-check method")
    if not is_connected():
        raise ConnectionError(f"cannot connect to {rpc_url}")
    return w3


def init_pending_nonces(w3: Web3, accounts: list) -> dict[str, int]:
    nonces = {}
    for account in accounts:
        nonces[account.address] = w3.eth.get_transaction_count(account.address, "pending")
    return nonces


def choose_pair(account_count: int) -> tuple[int, int]:
    sender = random.randrange(account_count)
    recipient = random.randrange(account_count - 1)
    if recipient >= sender:
        recipient += 1
    return sender, recipient


def normalize_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]


def should_refresh_nonce(error: str) -> bool:
    lowered = error.lower()
    return any(
        token in lowered
        for token in (
            "nonce too low",
            "already known",
            "known transaction",
            "replacement transaction underpriced",
            "invalid transaction nonce",
        )
    )


def write_run_info(args: argparse.Namespace, rpc_url: str, log_dir: Path) -> None:
    info = {
        "started_at": utc_now(),
        "rpc_url": rpc_url,
        "compose_file": str(args.compose_file),
        "chain_id": args.chain_id,
        "accounts": args.accounts,
        "rate": args.rate,
        "amount_min_eth": args.amount_min,
        "amount_max_eth": args.amount_max,
        "gas": args.gas,
        "gas_price_gwei": (
            str(args.gas_price_gwei) if args.gas_price_gwei is not None else None
        ),
        "does_not_wait_for_confirmations": True,
    }
    (log_dir / "run_info.json").write_text(json.dumps(info, indent=2) + "\n")


def send_loop(
    args: argparse.Namespace,
    w3: Web3,
    accounts: list,
    nonces: dict[str, int],
    min_wei: int,
    max_wei: int,
    writer: csv.DictWriter,
    csv_file,
) -> Counters:
    counters = Counters()
    start = time.monotonic()
    next_send = start
    next_status = start + args.status_interval
    interval = 1.0 / args.rate if args.rate > 0 else 0.0
    stop = False
    consecutive_failures = 0

    def stop_handler(signum, frame) -> None:  # noqa: ARG001
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    while not stop:
        now = time.monotonic()
        elapsed = now - start
        if args.duration is not None and elapsed >= args.duration:
            break
        if args.max_tx is not None and counters.sent >= args.max_tx:
            break

        if now < next_send:
            time.sleep(min(0.25, next_send - now))
            continue
        next_send = max(next_send + interval, time.monotonic()) if interval else now

        sender_index, recipient_index = choose_pair(len(accounts))
        sender = accounts[sender_index]
        recipient = accounts[recipient_index]
        amount_wei = random.randint(min_wei, max_wei)
        nonce = nonces[sender.address]
        gas_price = ""

        try:
            gas_price = (
                int(args.gas_price_gwei * Decimal(10**9))
                if args.gas_price_gwei is not None
                else int(w3.eth.gas_price)
            )
            tx = {
                "to": recipient.address,
                "value": amount_wei,
                "gas": args.gas,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": args.chain_id,
            }
            signed_txn = w3.eth.account.sign_transaction(tx, sender.key)
            tx_hash = w3.eth.send_raw_transaction(raw_transaction_bytes(signed_txn)).hex()
            nonces[sender.address] = nonce + 1
            counters.sent += 1
            consecutive_failures = 0
            error = ""
            status = "sent"
            if args.verbose:
                print(
                    f"{counters.sent}: {tx_hash} "
                    f"account[{sender_index}] -> account[{recipient_index}] nonce={nonce}",
                    flush=True,
                )
        except Exception as exc:  # keep the load generator alive during churn
            counters.failed += 1
            consecutive_failures += 1
            tx_hash = ""
            status = "failed"
            error = normalize_error(exc)
            if should_refresh_nonce(error):
                try:
                    nonces[sender.address] = w3.eth.get_transaction_count(
                        sender.address, "pending"
                    )
                except Exception:
                    pass

        writer.writerow(
            {
                "timestamp": utc_now(),
                "elapsed_sec": f"{time.monotonic() - start:.3f}",
                "seq": counters.sent + counters.failed,
                "status": status,
                "tx_hash": tx_hash,
                "sender_index": sender_index,
                "recipient_index": recipient_index,
                "sender": sender.address,
                "recipient": recipient.address,
                "nonce": nonce,
                "amount_wei": amount_wei,
                "gas_price_wei": gas_price,
                "error": error,
            }
        )
        csv_file.flush()

        if (
            args.max_consecutive_failures > 0
            and consecutive_failures >= args.max_consecutive_failures
        ):
            raise RuntimeError(
                f"transaction sender reached {consecutive_failures} consecutive failures; "
                f"last error: {error}"
            )

        if time.monotonic() >= next_status:
            total = counters.sent + counters.failed
            rate = counters.sent / max(time.monotonic() - start, 0.001)
            print(
                f"{utc_now()} sent={counters.sent} failed={counters.failed} "
                f"total={total} avg_sent_rate={rate:.2f}/s",
                flush=True,
            )
            next_status = time.monotonic() + args.status_interval

    return counters


def main() -> int:
    args = parse_args()
    if args.rate < 0:
        print("--rate must be >= 0", file=sys.stderr)
        return 2
    if args.gas <= 0:
        print("--gas must be > 0", file=sys.stderr)
        return 2
    if args.status_interval <= 0:
        print("--status-interval must be > 0", file=sys.stderr)
        return 2
    if args.max_consecutive_failures < 0:
        print("--max-consecutive-failures must be >= 0", file=sys.stderr)
        return 2
    if args.gas_price_gwei is not None and args.gas_price_gwei < 0:
        print("--gas-price-gwei must be >= 0", file=sys.stderr)
        return 2

    try:
        min_wei = parse_eth_amount(args.amount_min, "--amount-min")
        max_wei = parse_eth_amount(args.amount_max, "--amount-max")
        if min_wei > max_wei:
            raise ValueError("--amount-min must be <= --amount-max")

        rpc_url = args.rpc_url or discover_rpc_url(args.compose_file)
        print(f"RPC URL: {rpc_url}")
        print(f"Generating {args.accounts} HD accounts")
        accounts = build_accounts(args.mnemonic, args.accounts)

        w3 = connect(rpc_url)
        actual_chain_id = w3.eth.chain_id
        if actual_chain_id != args.chain_id:
            raise RuntimeError(
                f"RPC chain id is {actual_chain_id}, expected {args.chain_id}"
            )

        print("Initializing pending nonces")
        nonces = init_pending_nonces(w3, accounts)

        args.log_dir.mkdir(parents=True, exist_ok=True)
        write_run_info(args, rpc_url, args.log_dir)
        csv_path = args.log_dir / "tx_sender.csv"

        if args.dry_run:
            print(f"Dry run OK. Logs would be written to {args.log_dir}")
            return 0

        print(
            f"Broadcasting transactions at target rate {args.rate}/s; "
            "not waiting for confirmations"
        )
        print(f"Writing transaction log to {csv_path}")
        with csv_path.open("a", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
            if csv_file.tell() == 0:
                writer.writeheader()
            counters = send_loop(
                args, w3, accounts, nonces, min_wei, max_wei, writer, csv_file
            )

        print(f"Stopped. sent={counters.sent} failed={counters.failed}")
        return 0
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
