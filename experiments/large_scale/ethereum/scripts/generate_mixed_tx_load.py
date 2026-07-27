#!/usr/bin/env python3
"""Generate phased, reproducible mixed Ethereum transaction load."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from compose_endpoints import discover_execution_rpc_url


DEFAULT_CHAIN_ID = 1337
DEFAULT_MNEMONIC = "gentle always fun glass foster produce north tail security list example gain"
DEFAULT_COMPOSE_FILE = Path(__file__).resolve().parent.parent / "output" / "docker-compose.yml"
DEFAULT_PROFILE = Path(__file__).resolve().parent.parent / "configs" / "tx_mix_worker_v1.json"
SUPPORTED_KINDS = {"native_transfer", "contract_call", "contract_deploy"}
CSV_FIELDS = [
    "timestamp",
    "elapsed_sec",
    "seq",
    "phase",
    "target_rate",
    "tx_type",
    "status",
    "tx_hash",
    "sender_index",
    "recipient_index",
    "sender",
    "recipient",
    "nonce",
    "amount_wei",
    "gas_limit",
    "gas_price_wei",
    "data_bytes",
    "schedule_lag_ms",
    "error",
]
PHASE_FIELDS = [
    "phase",
    "target_rate",
    "configured_duration_sec",
    "start_timestamp",
    "end_timestamp",
    "start_elapsed_sec",
    "end_elapsed_sec",
    "attempted",
    "sent",
    "failed",
    "scheduler_skipped",
]


@dataclass(frozen=True)
class Phase:
    name: str
    rate: float
    duration: float


@dataclass(frozen=True)
class TransactionType:
    name: str
    kind: str
    weight: float
    gas_limit: int
    data_bytes: int


@dataclass(frozen=True)
class WorkloadProfile:
    name: str
    init_code: bytes
    setup_gas_limit: int
    transaction_types: tuple[TransactionType, ...]


@dataclass
class Counters:
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    scheduler_skipped: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--rpc-url")
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID)
    parser.add_argument("--mnemonic", default=DEFAULT_MNEMONIC)
    parser.add_argument("--accounts", type=int, default=200)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument(
        "--phases",
        default="baseline:1:3600",
        help="Comma-separated NAME:TX_PER_SEC:DURATION_SEC phases.",
    )
    parser.add_argument("--setup-file", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--amount-min", default="0.000001")
    parser.add_argument("--amount-max", default="0.0001")
    parser.add_argument("--gas-price-floor-gwei", type=decimal_arg, default=Decimal("1"))
    parser.add_argument("--gas-price-multiplier", type=decimal_arg, default=Decimal("2"))
    parser.add_argument("--gas-price-refresh", type=float, default=5.0)
    parser.add_argument("--receipt-timeout", type=float, default=180.0)
    parser.add_argument("--status-interval", type=float, default=30.0)
    parser.add_argument("--log-flush-interval", type=float, default=0.25)
    parser.add_argument("--max-consecutive-failures", type=int, default=0)
    parser.add_argument("--max-catchup-seconds", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument(
        "--hold-after-schedule",
        action="store_true",
        help="Remain alive after all phases until terminated by the benchmark runner.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def decimal_arg(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid decimal value: {value}") from exc


def utc_now(timespec: str = "milliseconds") -> str:
    return datetime.now(timezone.utc).isoformat(timespec=timespec)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def parse_hex_bytes(value: str, field: str) -> bytes:
    normalized = value.removeprefix("0x")
    if not normalized or len(normalized) % 2:
        raise ValueError(f"{field} must contain an even number of hexadecimal digits")
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} is not valid hexadecimal") from exc


def load_profile(path: Path) -> WorkloadProfile:
    payload = json.loads(path.read_text())
    setup = payload.get("setup_contract") or {}
    init_code = parse_hex_bytes(str(setup.get("init_code", "")), "setup_contract.init_code")
    setup_gas_limit = int(setup.get("gas_limit", 0))
    if setup_gas_limit < 53_000:
        raise ValueError("setup_contract.gas_limit must be at least 53000")

    transaction_types: list[TransactionType] = []
    names: set[str] = set()
    for raw in payload.get("transaction_types") or []:
        item = TransactionType(
            name=str(raw.get("name", "")).strip(),
            kind=str(raw.get("kind", "")).strip(),
            weight=float(raw.get("weight", 0)),
            gas_limit=int(raw.get("gas_limit", 0)),
            data_bytes=int(raw.get("data_bytes", 0)),
        )
        if not item.name or item.name in names:
            raise ValueError(f"transaction type names must be non-empty and unique: {item.name!r}")
        if item.kind not in SUPPORTED_KINDS:
            raise ValueError(f"unsupported transaction kind {item.kind!r}")
        if item.weight <= 0 or item.gas_limit < 21_000 or item.data_bytes < 0:
            raise ValueError(f"invalid transaction type parameters: {item}")
        if item.kind == "contract_call" and item.data_bytes < 32:
            raise ValueError(f"contract_call {item.name!r} requires at least 32 data bytes")
        names.add(item.name)
        transaction_types.append(item)
    if not transaction_types:
        raise ValueError("profile has no transaction_types")
    return WorkloadProfile(
        name=str(payload.get("name", path.stem)),
        init_code=init_code,
        setup_gas_limit=setup_gas_limit,
        transaction_types=tuple(transaction_types),
    )


def parse_phases(value: str) -> list[Phase]:
    phases: list[Phase] = []
    names: set[str] = set()
    for raw in value.split(","):
        parts = raw.split(":")
        if len(parts) != 3:
            raise ValueError(f"invalid phase {raw!r}; expected NAME:RATE:DURATION")
        name, raw_rate, raw_duration = parts
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in names:
            raise ValueError(f"phase names must be unique identifiers: {name!r}")
        rate = float(raw_rate)
        duration = float(raw_duration)
        if rate < 0 or duration <= 0:
            raise ValueError(f"phase rate must be non-negative and duration positive: {raw!r}")
        names.add(name)
        phases.append(Phase(name=name, rate=rate, duration=duration))
    if not phases:
        raise ValueError("phase schedule is empty")
    return phases


def parse_eth_amount(value: str, field: str) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be an ETH decimal") from exc
    if amount <= 0:
        raise ValueError(f"{field} must be positive")
    return int(amount * Decimal(10**18))


def build_accounts(mnemonic: str, total: int) -> list[Any]:
    if total < 2:
        raise ValueError("--accounts must be at least 2")
    try:
        from eth_account import Account
    except ModuleNotFoundError as exc:
        raise RuntimeError("missing Python dependency: eth-account") from exc
    Account.enable_unaudited_hdwallet_features()
    return [
        Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{index}")
        for index in range(total)
    ]


def connect(rpc_url: str) -> Any:
    try:
        from web3 import Web3
    except ModuleNotFoundError as exc:
        raise RuntimeError("missing Python dependency: web3") from exc
    provider = Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15})
    w3 = Web3(provider)
    is_connected = getattr(w3, "is_connected", None) or getattr(w3, "isConnected", None)
    if is_connected is None or not is_connected():
        raise ConnectionError(f"cannot connect to {rpc_url}")
    return w3


def raw_transaction_bytes(signed_txn: Any) -> bytes:
    if hasattr(signed_txn, "raw_transaction"):
        return signed_txn.raw_transaction
    return signed_txn.rawTransaction


def normalized_error(exc: Exception) -> str:
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


def current_gas_price(
    w3: Any,
    floor_wei: int,
    multiplier: Decimal,
    cached: tuple[float, int],
    refresh_seconds: float,
) -> tuple[int, tuple[float, int]]:
    timestamp, value = cached
    now = time.monotonic()
    if value > 0 and now - timestamp < refresh_seconds:
        return value, cached
    try:
        suggested = int(w3.eth.gas_price)
        value = max(floor_wei, int(Decimal(suggested) * multiplier))
    except Exception:
        value = value if value > 0 else floor_wei
    return value, (now, value)


def deploy_setup_contract(
    args: argparse.Namespace,
    profile: WorkloadProfile,
    rpc_url: str,
) -> dict[str, Any]:
    accounts = build_accounts(args.mnemonic, args.accounts)
    w3 = connect(rpc_url)
    if int(w3.eth.chain_id) != args.chain_id:
        raise RuntimeError(f"RPC chain id is {w3.eth.chain_id}, expected {args.chain_id}")
    sender = accounts[0]
    nonce = int(w3.eth.get_transaction_count(sender.address, "pending"))
    floor_wei = int(args.gas_price_floor_gwei * Decimal(10**9))
    gas_price = max(floor_wei, int(Decimal(int(w3.eth.gas_price)) * args.gas_price_multiplier))
    tx = {
        "value": 0,
        "data": profile.init_code,
        "gas": profile.setup_gas_limit,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": args.chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, sender.key)
    tx_hash = w3.eth.send_raw_transaction(raw_transaction_bytes(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=args.receipt_timeout)
    status = int(receipt.get("status", 0))
    address = receipt.get("contractAddress")
    if status != 1 or not address:
        raise RuntimeError(f"setup contract deployment failed: status={status}, receipt={receipt}")
    payload = {
        "status": "ready",
        "prepared_at": utc_now(),
        "rpc_url": rpc_url,
        "chain_id": args.chain_id,
        "profile": profile.name,
        "profile_path": str(args.profile.resolve()),
        "account_count": args.accounts,
        "contract_address": address,
        "deployment_tx_hash": tx_hash.hex(),
        "deployment_block_number": int(receipt["blockNumber"]),
        "init_code_sha256": hashlib.sha256(profile.init_code).hexdigest(),
    }
    atomic_json(args.setup_file, payload)
    return payload


def choose_transaction_type(
    rng: random.Random, transaction_types: tuple[TransactionType, ...]
) -> TransactionType:
    return rng.choices(
        transaction_types,
        weights=[item.weight for item in transaction_types],
        k=1,
    )[0]


def random_bytes(rng: random.Random, length: int) -> bytes:
    if length <= 0:
        return b""
    value = rng.randbytes(length)
    if length >= 32 and not any(value[:32]):
        value = b"\x01" + value[1:]
    return value


def build_transaction(
    args: argparse.Namespace,
    profile: WorkloadProfile,
    tx_type: TransactionType,
    accounts: list[Any],
    sender_index: int,
    nonce: int,
    contract_address: str,
    min_wei: int,
    max_wei: int,
    gas_price: int,
    rng: random.Random,
) -> tuple[dict[str, Any], int, str, int, int]:
    sender = accounts[sender_index]
    recipient_index = -1
    recipient = ""
    amount_wei = 0
    data = b""
    tx: dict[str, Any] = {
        "value": 0,
        "gas": tx_type.gas_limit,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": args.chain_id,
    }
    if tx_type.kind == "native_transfer":
        recipient_index = rng.randrange(len(accounts) - 1)
        if recipient_index >= sender_index:
            recipient_index += 1
        recipient = accounts[recipient_index].address
        amount_wei = rng.randint(min_wei, max_wei)
        tx.update({"to": recipient, "value": amount_wei})
    elif tx_type.kind == "contract_call":
        recipient = contract_address
        data = random_bytes(rng, tx_type.data_bytes)
        tx.update({"to": contract_address, "data": data})
    elif tx_type.kind == "contract_deploy":
        data = profile.init_code
        tx["data"] = data
    else:
        raise RuntimeError(f"unsupported transaction kind: {tx_type.kind}")
    return tx, recipient_index, recipient, amount_wei, len(data)


def phase_state(
    phase: Phase,
    index: int,
    total: int,
    status: str,
    phase_started: float,
    experiment_started: float,
    counters: Counters,
) -> dict[str, Any]:
    return {
        "status": status,
        "phase": phase.name,
        "phase_index": index,
        "phase_count": total,
        "target_rate": phase.rate,
        "configured_duration_sec": phase.duration,
        "phase_elapsed_sec": max(0.0, time.monotonic() - phase_started),
        "elapsed_sec": max(0.0, time.monotonic() - experiment_started),
        "attempted": counters.attempted,
        "sent": counters.sent,
        "failed": counters.failed,
        "scheduler_skipped": counters.scheduler_skipped,
        "updated_at": utc_now(),
    }


def write_run_info(
    path: Path,
    status: str,
    args: argparse.Namespace,
    profile: WorkloadProfile,
    phases: list[Phase],
    counters: Counters,
    current_phase: str,
    error: str = "",
) -> None:
    atomic_json(
        path,
        {
            "status": status,
            "updated_at": utc_now(),
            "profile": profile.name,
            "profile_path": str(args.profile.resolve()),
            "phase_schedule": [phase.__dict__ for phase in phases],
            "configured_duration_sec": sum(phase.duration for phase in phases),
            "account_count": args.accounts,
            "seed": args.seed,
            "current_phase": current_phase,
            "attempted": counters.attempted,
            "sent": counters.sent,
            "failed": counters.failed,
            "scheduler_skipped": counters.scheduler_skipped,
            "does_not_wait_for_confirmations": True,
            "error": error,
        },
    )


def run_schedule(
    args: argparse.Namespace,
    profile: WorkloadProfile,
    phases: list[Phase],
    rpc_url: str,
) -> int:
    if args.log_dir is None:
        raise ValueError("--log-dir is required unless --prepare-only is used")
    setup = json.loads(args.setup_file.read_text())
    contract_address = str(setup.get("contract_address", ""))
    if any(item.kind == "contract_call" for item in profile.transaction_types) and not contract_address:
        raise ValueError(f"setup file has no contract_address: {args.setup_file}")

    accounts = build_accounts(args.mnemonic, args.accounts)
    w3 = connect(rpc_url)
    if int(w3.eth.chain_id) != args.chain_id:
        raise RuntimeError(f"RPC chain id is {w3.eth.chain_id}, expected {args.chain_id}")
    nonces = {
        account.address: int(w3.eth.get_transaction_count(account.address, "pending"))
        for account in accounts
    }
    min_wei = parse_eth_amount(args.amount_min, "--amount-min")
    max_wei = parse_eth_amount(args.amount_max, "--amount-max")
    if min_wei > max_wei:
        raise ValueError("--amount-min must be <= --amount-max")

    args.log_dir.mkdir(parents=True, exist_ok=True)
    sender_path = args.log_dir / "tx_sender.csv"
    phases_path = args.log_dir / "tx_phases.csv"
    state_path = args.log_dir / "tx_phase_state.json"
    run_info_path = args.log_dir / "run_info.json"
    rng = random.Random(args.seed)
    counters = Counters()
    phase_rows: list[dict[str, Any]] = []
    stop = False
    experiment_started = time.monotonic()
    next_status = experiment_started + args.status_interval
    next_flush = experiment_started + args.log_flush_interval
    gas_cache = (0.0, 0)
    floor_wei = int(args.gas_price_floor_gwei * Decimal(10**9))
    consecutive_failures = 0
    sequence = 0

    def stop_handler(signum: int, frame: object) -> None:  # noqa: ARG001
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    write_run_info(run_info_path, "running", args, profile, phases, counters, "")

    with sender_path.open("w", newline="") as sender_handle:
        writer = csv.DictWriter(sender_handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for phase_index, phase in enumerate(phases, start=1):
            if stop:
                break
            phase_started = time.monotonic()
            phase_started_at = utc_now()
            phase_counters = Counters()
            next_send = phase_started
            atomic_json(
                state_path,
                phase_state(
                    phase,
                    phase_index,
                    len(phases),
                    "running",
                    phase_started,
                    experiment_started,
                    phase_counters,
                ),
            )
            while not stop and time.monotonic() - phase_started < phase.duration:
                now = time.monotonic()
                if phase.rate <= 0:
                    time.sleep(min(0.25, phase.duration - (now - phase_started)))
                    continue
                if now < next_send:
                    time.sleep(min(0.05, next_send - now))
                    continue
                lag = max(0.0, now - next_send)
                if lag > args.max_catchup_seconds:
                    skipped = max(1, int((lag - args.max_catchup_seconds) * phase.rate))
                    counters.scheduler_skipped += skipped
                    phase_counters.scheduler_skipped += skipped
                    next_send = now - args.max_catchup_seconds

                tx_type = choose_transaction_type(rng, profile.transaction_types)
                sender_index = sequence % len(accounts)
                sender = accounts[sender_index]
                nonce = nonces[sender.address]
                gas_price, gas_cache = current_gas_price(
                    w3,
                    floor_wei,
                    args.gas_price_multiplier,
                    gas_cache,
                    args.gas_price_refresh,
                )
                tx, recipient_index, recipient, amount_wei, data_bytes = build_transaction(
                    args,
                    profile,
                    tx_type,
                    accounts,
                    sender_index,
                    nonce,
                    contract_address,
                    min_wei,
                    max_wei,
                    gas_price,
                    rng,
                )
                counters.attempted += 1
                phase_counters.attempted += 1
                sequence += 1
                tx_hash = ""
                error = ""
                status = "failed"
                try:
                    signed = w3.eth.account.sign_transaction(tx, sender.key)
                    tx_hash = w3.eth.send_raw_transaction(raw_transaction_bytes(signed)).hex()
                    nonces[sender.address] = nonce + 1
                    counters.sent += 1
                    phase_counters.sent += 1
                    consecutive_failures = 0
                    status = "sent"
                except Exception as exc:  # keep applying pressure while recording backpressure
                    counters.failed += 1
                    phase_counters.failed += 1
                    consecutive_failures += 1
                    error = normalized_error(exc)
                    if should_refresh_nonce(error):
                        try:
                            nonces[sender.address] = int(
                                w3.eth.get_transaction_count(sender.address, "pending")
                            )
                        except Exception:
                            pass

                writer.writerow(
                    {
                        "timestamp": utc_now(),
                        "elapsed_sec": f"{time.monotonic() - experiment_started:.6f}",
                        "seq": sequence,
                        "phase": phase.name,
                        "target_rate": phase.rate,
                        "tx_type": tx_type.name,
                        "status": status,
                        "tx_hash": tx_hash,
                        "sender_index": sender_index,
                        "recipient_index": recipient_index,
                        "sender": sender.address,
                        "recipient": recipient,
                        "nonce": nonce,
                        "amount_wei": amount_wei,
                        "gas_limit": tx_type.gas_limit,
                        "gas_price_wei": gas_price,
                        "data_bytes": data_bytes,
                        "schedule_lag_ms": f"{lag * 1000.0:.3f}",
                        "error": error,
                    }
                )
                if time.monotonic() >= next_flush:
                    sender_handle.flush()
                    next_flush = time.monotonic() + args.log_flush_interval
                next_send += 1.0 / phase.rate

                if (
                    args.max_consecutive_failures > 0
                    and consecutive_failures >= args.max_consecutive_failures
                ):
                    raise RuntimeError(
                        f"reached {consecutive_failures} consecutive failures; last={error}"
                    )
                if time.monotonic() >= next_status:
                    atomic_json(
                        state_path,
                        phase_state(
                            phase,
                            phase_index,
                            len(phases),
                            "running",
                            phase_started,
                            experiment_started,
                            phase_counters,
                        ),
                    )
                    write_run_info(
                        run_info_path,
                        "running",
                        args,
                        profile,
                        phases,
                        counters,
                        phase.name,
                    )
                    elapsed = max(time.monotonic() - experiment_started, 0.001)
                    print(
                        f"{utc_now()} phase={phase.name} target={phase.rate:g}/s "
                        f"sent={counters.sent} failed={counters.failed} "
                        f"skipped={counters.scheduler_skipped} achieved={counters.sent / elapsed:.2f}/s",
                        flush=True,
                    )
                    next_status = time.monotonic() + args.status_interval

            phase_ended = time.monotonic()
            phase_ended_at = utc_now()
            sender_handle.flush()
            phase_rows.append(
                {
                    "phase": phase.name,
                    "target_rate": phase.rate,
                    "configured_duration_sec": phase.duration,
                    "start_timestamp": phase_started_at,
                    "end_timestamp": phase_ended_at,
                    "start_elapsed_sec": f"{phase_started - experiment_started:.6f}",
                    "end_elapsed_sec": f"{phase_ended - experiment_started:.6f}",
                    "attempted": phase_counters.attempted,
                    "sent": phase_counters.sent,
                    "failed": phase_counters.failed,
                    "scheduler_skipped": phase_counters.scheduler_skipped,
                }
            )
            with phases_path.open("w", newline="") as phase_handle:
                phase_writer = csv.DictWriter(phase_handle, fieldnames=PHASE_FIELDS)
                phase_writer.writeheader()
                phase_writer.writerows(phase_rows)

        completion_status = "complete" if len(phase_rows) == len(phases) else "stopped"
        atomic_json(
            state_path,
            {
                "status": completion_status,
                "phase": phase_rows[-1]["phase"] if phase_rows else "",
                "phase_count": len(phases),
                "completed_phases": len(phase_rows),
                "elapsed_sec": time.monotonic() - experiment_started,
                "attempted": counters.attempted,
                "sent": counters.sent,
                "failed": counters.failed,
                "scheduler_skipped": counters.scheduler_skipped,
                "updated_at": utc_now(),
            },
        )
        write_run_info(
            run_info_path,
            completion_status,
            args,
            profile,
            phases,
            counters,
            phase_rows[-1]["phase"] if phase_rows else "",
        )
        while args.hold_after_schedule and not stop:
            time.sleep(0.5)
    return 0 if len(phase_rows) == len(phases) else 130


def validate_args(args: argparse.Namespace) -> tuple[WorkloadProfile, list[Phase], str]:
    if args.accounts < 2:
        raise ValueError("--accounts must be at least 2")
    if args.chain_id < 1:
        raise ValueError("--chain-id must be positive")
    if (
        args.status_interval <= 0
        or args.log_flush_interval <= 0
        or args.gas_price_refresh <= 0
    ):
        raise ValueError("status, log-flush, and gas-price refresh intervals must be positive")
    if args.max_catchup_seconds < 0 or args.max_consecutive_failures < 0:
        raise ValueError("catchup and failure limits must be non-negative")
    if args.gas_price_floor_gwei < 0 or args.gas_price_multiplier < 1:
        raise ValueError("gas price floor must be non-negative and multiplier at least one")
    profile = load_profile(args.profile)
    phases = parse_phases(args.phases)
    rpc_url = args.rpc_url or (
        f"auto-discover:{args.compose_file}"
        if args.dry_run
        else discover_execution_rpc_url(args.compose_file)
    )
    return profile, phases, rpc_url


def main() -> int:
    args = parse_args()
    try:
        profile, phases, rpc_url = validate_args(args)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "profile": profile.name,
                        "transaction_types": [item.__dict__ for item in profile.transaction_types],
                        "phases": [phase.__dict__ for phase in phases],
                        "duration_sec": sum(phase.duration for phase in phases),
                        "rpc_url": rpc_url,
                    },
                    indent=2,
                )
            )
            return 0
        if args.prepare_only:
            print(json.dumps(deploy_setup_contract(args, profile, rpc_url)))
            return 0
        return run_schedule(args, profile, phases, rpc_url)
    except Exception as exc:
        if args.log_dir:
            try:
                profile = load_profile(args.profile)
                phases = parse_phases(args.phases)
                write_run_info(
                    args.log_dir / "run_info.json",
                    "failed",
                    args,
                    profile,
                    phases,
                    Counters(),
                    "",
                    normalized_error(exc),
                )
            except Exception:
                pass
        print(f"fatal: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
