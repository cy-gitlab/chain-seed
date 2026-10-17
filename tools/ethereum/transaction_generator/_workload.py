#!/usr/bin/env python3
"""Generate phased, reproducible mixed Ethereum transaction load."""

from __future__ import annotations

import argparse
import csv
import faulthandler
import hashlib
import http.client
import json
import math
import queue
import random
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from compose_endpoints import discover_execution_rpc_urls
from _templates import (
    DEPLOYMENT_CONTRACT, PROFILE_NAME, SETUP_CONTRACTS,
    TRANSACTION_ENVELOPES, TRANSACTION_TEMPLATES,
)


DEFAULT_CHAIN_ID = 1337
GENERATOR_VERSION = "mainnet-calibrated-v12-nonce-safe"
DEFAULT_MNEMONIC = "gentle always fun glass foster produce north tail security list example gain"
DEFAULT_PROFILE = Path(__file__).resolve().parent / "profiles" / "tx_mix_mainnet_v4.json"
TEMPLATES_FILE = Path(__file__).resolve().with_name("_templates.py")
SUPPORTED_KINDS = {"native_transfer", "contract_call", "contract_deploy"}
SUPPORTED_ENVELOPES = {"legacy": 0, "access_list": 1, "dynamic_fee": 2}
CSV_FIELDS = [
    "timestamp",
    "elapsed_sec",
    "seq",
    "phase",
    "target_rate",
    "tx_type",
    "tx_envelope",
    "transaction_type",
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
    "max_fee_per_gas_wei",
    "max_priority_fee_per_gas_wei",
    "data_bytes",
    "schedule_lag_ms",
    "queue_wait_ms",
    "sign_latency_ms",
    "rpc_latency_ms",
    "worker_id",
    "rpc_url",
    "send_attempts",
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
    "scheduled",
    "enqueued",
    "attempted",
    "sent",
    "failed",
    "scheduler_skipped",
    "queue_dropped",
    "send_retries",
    "drain_duration_sec",
    "receipt_drain_status",
    "receipt_drain_duration_sec",
    "receipt_known_at_end",
    "receipt_committed_at_end",
    "receipt_pending_at_end",
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
    contract: str
    selector: str
    target_gas_p50: int


@dataclass(frozen=True)
class EnvelopeType:
    name: str
    transaction_type: int
    weight: float


@dataclass(frozen=True)
class SetupContract:
    name: str
    init_code: bytes
    gas_limit: int


@dataclass(frozen=True)
class WorkloadProfile:
    name: str
    setup_contracts: tuple[SetupContract, ...]
    deployment_init_code: bytes
    envelopes: tuple[EnvelopeType, ...]
    transaction_types: tuple[TransactionType, ...]


@dataclass
class Counters:
    scheduled: int = 0
    enqueued: int = 0
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    scheduler_skipped: int = 0
    queue_dropped: int = 0
    send_retries: int = 0


@dataclass(frozen=True)
class TransactionJob:
    sequence: int
    phase: str
    target_rate: float
    due_at: float
    enqueued_at: float
    tx_type: TransactionType
    envelope: EnvelopeType
    sender_index: int
    recipient_index: int
    sender: str
    recipient: str
    nonce: int
    amount_wei: int
    gas_price_wei: int
    max_fee_per_gas_wei: int
    max_priority_fee_per_gas_wei: int
    data_bytes: int
    transaction: dict[str, Any]


@dataclass(frozen=True)
class TransactionResult:
    job: TransactionJob
    status: str
    tx_hash: str
    error: str
    worker_id: int
    rpc_url: str
    started_at: float
    rpc_started_at: float
    completed_at: float
    completed_timestamp: str
    attempts: int


@dataclass(frozen=True)
class ReceiptDrainResult:
    status: str
    duration: float
    known: Optional[int]
    committed: Optional[int]
    pending: Optional[int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("-f", "--compose-file", type=Path, help="Discover execution RPCs from a SeedEmu PoS Compose file")
    target.add_argument("--rpc-url", action="append", help="Execution HTTP RPC; repeat to use multiple entry points")
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID)
    parser.add_argument("--mnemonic", default=DEFAULT_MNEMONIC)
    parser.add_argument("--mnemonic-file", type=Path, help="Read the funded accounts' mnemonic from a local file")
    parser.add_argument("--accounts", type=int, default=200)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE,
                        help="JSON mapping transaction type names to sampling weights")
    parser.add_argument(
        "--phases",
        default="baseline:1:3600",
        help="Comma-separated NAME:TX_PER_SEC:DURATION_SEC phases.",
    )
    parser.add_argument("--contracts-file", type=Path, required=True, help="Contract addresses written by deploy_contracts.py")
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
    parser.add_argument("--send-workers", type=int, default=8)
    parser.add_argument("--sign-workers", type=int, default=4)
    parser.add_argument("--signer-max-tasks", type=int, default=100_000)
    parser.add_argument("--rpc-targets", type=int, default=4)
    parser.add_argument("--queue-depth", type=int, default=64)
    parser.add_argument("--send-retries", type=int, default=2)
    parser.add_argument("--receipt-state-file", type=Path)
    parser.add_argument("--phase-drain-timeout", type=float, default=0.0)
    parser.add_argument("--phase-drain-interval", type=float, default=2.0)
    parser.add_argument("--phase-settle-seconds", type=float, default=24.0)
    parser.add_argument(
        "--phase-drain-min-commit-pct",
        type=float,
        default=100.0,
        help=(
            "At drain timeout, accept a fully observed phase when at least "
            "this percentage of sent transactions committed."
        ),
    )
    parser.add_argument(
        "--phase-drain-required",
        action="store_true",
        help="Fail if receipt drain does not complete before the next phase.",
    )
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


def evm_push(value: int) -> bytes:
    if value < 0:
        raise ValueError("EVM push value must be non-negative")
    width = max(1, (value.bit_length() + 7) // 8)
    if width > 32:
        raise ValueError("EVM push value exceeds 32 bytes")
    return bytes([0x5F + width]) + value.to_bytes(width, "big")


def synthetic_init_code(
    storage_writes: int,
    runtime_padding_bytes: int = 0,
    calldata_words: int = 16,
) -> bytes:
    if not 0 <= storage_writes <= 256:
        raise ValueError("synthetic contract storage_writes must be in [0, 256]")
    if not 0 <= runtime_padding_bytes <= 24_000:
        raise ValueError("synthetic contract runtime_padding_bytes must be in [0, 24000]")
    if not 1 <= calldata_words <= 256:
        raise ValueError("synthetic contract calldata_words must be in [1, 256]")

    runtime = bytearray()
    for slot in range(storage_writes):
        # Values come from rotating calldata words. Reading beyond calldata is valid
        # EVM behavior and yields zero, so short ABI-shaped calls remain successful.
        runtime.extend(evm_push(4 + (slot % calldata_words) * 32))
        runtime.append(0x35)  # CALLDATALOAD
        runtime.extend(evm_push(slot))
        runtime.append(0x55)  # SSTORE
    runtime.append(0x00)  # STOP
    runtime.extend(b"\x5b" * runtime_padding_bytes)  # Unexecuted deployed-code payload.

    offset = 0
    while True:
        prefix = (
            evm_push(len(runtime))
            + evm_push(offset)
            + evm_push(0)
            + b"\x39"
            + evm_push(len(runtime))
            + evm_push(0)
            + b"\xf3"
        )
        if len(prefix) == offset:
            return prefix + bytes(runtime)
        offset = len(prefix)


def load_setup_contract(raw: dict[str, Any], field: str) -> SetupContract:
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError(f"{field}.name must be non-empty")
    gas_limit = int(raw.get("gas_limit", 0))
    if gas_limit < 53_000:
        raise ValueError(f"{field}.gas_limit must be at least 53000")
    if raw.get("init_code"):
        init_code = parse_hex_bytes(str(raw["init_code"]), f"{field}.init_code")
    else:
        init_code = synthetic_init_code(
            int(raw.get("storage_writes", 0)),
            int(raw.get("runtime_padding_bytes", 0)),
            int(raw.get("calldata_words", 16)),
        )
    return SetupContract(name=name, init_code=init_code, gas_limit=gas_limit)


def load_profile(path: Path) -> WorkloadProfile:
    weights = json.loads(path.read_text())
    if not isinstance(weights, dict) or not weights:
        raise ValueError("profile must be a non-empty JSON object of transaction weights")
    selected: list[dict[str, Any]] = []
    for name, weight in weights.items():
        if name not in TRANSACTION_TEMPLATES:
            raise ValueError(f"unknown transaction type {name!r}")
        if (isinstance(weight, bool) or not isinstance(weight, (int, float))
                or not math.isfinite(weight) or weight < 0):
            raise ValueError(f"weight for {name!r} must be a finite nonnegative number")
        if weight > 0:
            selected.append({**TRANSACTION_TEMPLATES[name], "name": name, "weight": weight})
    if not selected:
        raise ValueError("profile must contain at least one positive transaction weight")
    required_contracts = {raw["contract"] for raw in selected if raw["kind"] == "contract_call"}
    setup_contracts = [
        load_setup_contract(raw, f"setup_contracts[{index}]")
        for index, raw in enumerate(SETUP_CONTRACTS)
        if raw["name"] in required_contracts
    ]
    contract_names = {contract.name for contract in setup_contracts}
    if len(contract_names) != len(setup_contracts):
        raise ValueError("setup contract names must be unique")

    deployment_init_code = load_setup_contract(
        {"name": "deployment_template", **DEPLOYMENT_CONTRACT}, "deployment_contract",
    ).init_code

    envelopes: list[EnvelopeType] = []
    for raw in TRANSACTION_ENVELOPES:
        name = str(raw.get("name", "")).strip()
        weight = float(raw.get("weight", 0))
        if name not in SUPPORTED_ENVELOPES or weight <= 0:
            raise ValueError(f"invalid transaction envelope: {raw!r}")
        envelopes.append(
            EnvelopeType(
                name=name,
                transaction_type=SUPPORTED_ENVELOPES[name],
                weight=weight,
            )
        )

    transaction_types: list[TransactionType] = []
    names: set[str] = set()
    for raw in selected:
        item = TransactionType(
            name=str(raw.get("name", "")).strip(),
            kind=str(raw.get("kind", "")).strip(),
            weight=float(raw.get("weight", 0)),
            gas_limit=int(raw.get("gas_limit", 0)),
            data_bytes=int(raw.get("data_bytes", 0)),
            contract=str(raw.get("contract", "default")).strip(),
            selector=str(raw.get("selector", "")).lower().removeprefix("0x"),
            target_gas_p50=int(raw.get("target_gas_p50", 0)),
        )
        if not item.name or item.name in names:
            raise ValueError(f"transaction type names must be non-empty and unique: {item.name!r}")
        if item.kind not in SUPPORTED_KINDS:
            raise ValueError(f"unsupported transaction kind {item.kind!r}")
        if item.weight <= 0 or item.gas_limit < 21_000 or item.data_bytes < 0:
            raise ValueError(f"invalid transaction type parameters: {item}")
        if item.target_gas_p50 < 0 or item.target_gas_p50 > item.gas_limit:
            raise ValueError(f"invalid target gas median: {item}")
        if item.kind == "contract_call":
            if item.contract not in contract_names:
                raise ValueError(
                    f"contract_call {item.name!r} references unknown contract "
                    f"{item.contract!r}"
                )
            if item.selector and not re.fullmatch(r"[0-9a-f]{8}", item.selector):
                raise ValueError(f"contract_call {item.name!r} has invalid selector")
            minimum_data = 4 if item.selector else 32
            if item.data_bytes < minimum_data:
                raise ValueError(
                    f"contract_call {item.name!r} requires at least "
                    f"{minimum_data} data bytes"
                )
        names.add(item.name)
        transaction_types.append(item)
    if not transaction_types:
        raise ValueError("profile has no transaction_types")
    return WorkloadProfile(
        name=PROFILE_NAME,
        setup_contracts=tuple(setup_contracts),
        deployment_init_code=deployment_init_code,
        envelopes=tuple(envelopes),
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


def sign_transaction_payload(transaction: dict[str, Any], private_key: bytes) -> bytes:
    from eth_account import Account

    return bytes(raw_transaction_bytes(Account.sign_transaction(transaction, private_key)))


def signer_worker_main() -> int:
    from eth_hash.auto import keccak

    for line in sys.stdin:
        try:
            request = json.loads(line)
            transaction = {
                key: bytes.fromhex(value[2:])
                if key == "data" and isinstance(value, str) and value.startswith("0x")
                else value
                for key, value in request["transaction"].items()
            }
            raw = sign_transaction_payload(
                transaction,
                bytes.fromhex(request["private_key"]),
            )
            response = {
                "raw_transaction": raw.hex(),
                "tx_hash": "0x" + keccak(raw).hex(),
                "error": "",
            }
        except Exception as exc:
            response = {
                "raw_transaction": "",
                "tx_hash": "",
                "error": normalized_error(exc),
            }
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


class SignerClient:
    def __init__(self, script: Path, max_tasks: int) -> None:
        self.script = script
        self.max_tasks = max_tasks
        self.tasks = 0
        self.lock = threading.Lock()
        self.process: Optional[subprocess.Popen[str]] = None
        self._start()

    def _start(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(self.script), "--internal-signer-worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.tasks = 0

    def _stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.stdin:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _restart(self) -> None:
        self._stop()
        self._start()

    def sign(self, transaction: dict[str, Any], private_key: bytes) -> tuple[bytes, str]:
        request = {
            "transaction": {
                key: "0x" + bytes(value).hex() if isinstance(value, bytes) else value
                for key, value in transaction.items()
            },
            "private_key": bytes(private_key).hex(),
        }
        encoded = json.dumps(request, separators=(",", ":")) + "\n"
        with self.lock:
            if self.max_tasks and self.tasks >= self.max_tasks:
                self._restart()
            for attempt in range(2):
                process = self.process
                try:
                    if process is None or process.poll() is not None:
                        raise RuntimeError("signer subprocess is not running")
                    assert process.stdin is not None and process.stdout is not None
                    process.stdin.write(encoded)
                    process.stdin.flush()
                    response_line = process.stdout.readline()
                    if not response_line:
                        raise RuntimeError("signer subprocess closed its output")
                    response = json.loads(response_line)
                    if response.get("error"):
                        raise RuntimeError(f"signer rejected transaction: {response['error']}")
                    raw = bytes.fromhex(str(response["raw_transaction"]))
                    tx_hash = str(response["tx_hash"])
                    self.tasks += 1
                    return raw, tx_hash
                except (BrokenPipeError, EOFError, json.JSONDecodeError, KeyError, RuntimeError):
                    if attempt:
                        raise
                    self._restart()
        raise RuntimeError("signer subprocess failed")

    def close(self) -> None:
        with self.lock:
            self._stop()


class DirectJsonRpcClient:
    """Minimal per-thread JSON-RPC client for the transaction hot path."""

    def __init__(self, rpc_url: str, timeout: float = 15.0) -> None:
        parsed = urllib.parse.urlsplit(rpc_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"unsupported execution RPC URL: {rpc_url}")
        self.rpc_url = rpc_url
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += f"?{parsed.query}"
        self.timeout = timeout
        self.connection: Optional[http.client.HTTPConnection] = None
        self.request_id = 0

    def _connect(self) -> http.client.HTTPConnection:
        if self.connection is None:
            connection_type = (
                http.client.HTTPSConnection
                if self.scheme == "https"
                else http.client.HTTPConnection
            )
            self.connection = connection_type(self.host, self.port, timeout=self.timeout)
        return self.connection

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def send_raw_transaction(self, raw_transaction: bytes) -> str:
        result = self.call("eth_sendRawTransaction", ["0x" + raw_transaction.hex()])
        if not isinstance(result, str) or not result.startswith("0x"):
            raise RuntimeError(f"execution RPC returned an invalid transaction hash: {result!r}")
        return result

    def call(self, method: str, params: list) -> Any:
        self.request_id += 1
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": self.request_id,
                "method": method,
                "params": params,
            },
            separators=(",", ":"),
        )
        try:
            connection = self._connect()
            connection.request(
                "POST",
                self.path,
                body=request,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "seedemu-mixed-tx/v9",
                },
            )
            response = connection.getresponse()
            body = response.read()
            if response.will_close:
                self.close()
            if response.status != 200:
                raise ConnectionError(
                    f"execution RPC HTTP {response.status} {response.reason}"
                )
            payload = json.loads(body)
        except (OSError, TimeoutError, http.client.HTTPException, json.JSONDecodeError) as exc:
            self.close()
            raise ConnectionError(f"execution RPC connection failed: {exc}") from exc

        error = payload.get("error")
        if error:
            if isinstance(error, dict):
                message = str(error.get("message") or error)
            else:
                message = str(error)
            raise RuntimeError(message)
        return payload.get("result")


def normalized_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]


def transaction_already_known(error: str) -> bool:
    lowered = error.lower()
    return "already known" in lowered or lowered.strip() == "known transaction"


def retryable_send_error(error: str) -> bool:
    lowered = error.lower()
    return any(
        token in lowered
        for token in (
            "connection",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "bad gateway",
            "service unavailable",
            "connection reset",
            "eof",
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




def choose_transaction_type(
    rng: random.Random, transaction_types: tuple[TransactionType, ...]
) -> TransactionType:
    return rng.choices(
        transaction_types,
        weights=[item.weight for item in transaction_types],
        k=1,
    )[0]


def choose_envelope(
    rng: random.Random, envelopes: tuple[EnvelopeType, ...]
) -> EnvelopeType:
    return rng.choices(
        envelopes,
        weights=[item.weight for item in envelopes],
        k=1,
    )[0]


def random_bytes(rng: random.Random, length: int) -> bytes:
    if length <= 0:
        return b""
    value = rng.randbytes(length)
    if length >= 32 and not any(value[:32]):
        value = b"\x01" + value[1:]
    return value


def transaction_rng(seed: int, sequence: int) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{sequence}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def build_transaction(
    args: argparse.Namespace,
    profile: WorkloadProfile,
    tx_type: TransactionType,
    envelope: EnvelopeType,
    accounts: list[Any],
    sender_index: int,
    nonce: int,
    contract_addresses: dict[str, str],
    min_wei: int,
    max_wei: int,
    gas_price: int,
    rng: random.Random,
) -> tuple[dict[str, Any], int, str, int, int, int, int]:
    sender = accounts[sender_index]
    recipient_index = -1
    recipient = ""
    amount_wei = 0
    data = b""
    tx: dict[str, Any] = {
        "value": 0,
        "gas": tx_type.gas_limit,
        "nonce": nonce,
        "chainId": args.chain_id,
    }
    max_fee_per_gas = 0
    max_priority_fee_per_gas = 0
    if envelope.name == "dynamic_fee":
        max_fee_per_gas = gas_price
        max_priority_fee_per_gas = min(gas_price, 10**9)
        tx.update(
            {
                "type": 2,
                "maxFeePerGas": max_fee_per_gas,
                "maxPriorityFeePerGas": max_priority_fee_per_gas,
            }
        )
    elif envelope.name == "access_list":
        tx.update({"type": 1, "gasPrice": gas_price, "accessList": []})
    else:
        tx["gasPrice"] = gas_price
    if tx_type.kind == "native_transfer":
        recipient_index = rng.randrange(len(accounts) - 1)
        if recipient_index >= sender_index:
            recipient_index += 1
        recipient = accounts[recipient_index].address
        amount_wei = rng.randint(min_wei, max_wei)
        tx.update({"to": recipient, "value": amount_wei})
    elif tx_type.kind == "contract_call":
        recipient = contract_addresses[tx_type.contract]
        selector = bytes.fromhex(tx_type.selector) if tx_type.selector else b""
        data = selector + random_bytes(rng, tx_type.data_bytes - len(selector))
        tx.update({"to": recipient, "data": data})
    elif tx_type.kind == "contract_deploy":
        data = profile.deployment_init_code
        tx["data"] = data
    else:
        raise RuntimeError(f"unsupported transaction kind: {tx_type.kind}")
    return (
        tx,
        recipient_index,
        recipient,
        amount_wei,
        len(data),
        max_fee_per_gas,
        max_priority_fee_per_gas,
    )


def copy_counters(counters: Counters) -> Counters:
    return Counters(**counters.__dict__)


def sender_worker(
    worker_id: int,
    rpc_url: str,
    rpc_client: DirectJsonRpcClient,
    accounts: list[Any],
    jobs: queue.Queue[Optional[TransactionJob]],
    results: queue.Queue[Optional[TransactionResult]],
    send_retries: int,
    signer: SignerClient,
) -> None:
    blocked_senders: set[int] = set()
    while True:
        job = jobs.get()
        try:
            if job is None:
                return
            started_at = time.monotonic()
            status = "failed"
            tx_hash = ""
            error = ""
            attempts = 1
            rpc_started_at = started_at
            try:
                if job.sender_index in blocked_senders:
                    raise RuntimeError("sender blocked after unresolved nonce submission")
                sender = accounts[job.sender_index]
                raw_transaction, local_hash = signer.sign(
                    job.transaction,
                    bytes(sender.key),
                )
                rpc_started_at = time.monotonic()
                attempts = 0
                while True:
                    attempts += 1
                    try:
                        tx_hash = rpc_client.send_raw_transaction(raw_transaction)
                        status = "sent"
                        error = ""
                        break
                    except Exception as exc:
                        error = normalized_error(exc)
                        if transaction_already_known(error):
                            tx_hash = local_hash
                            status = "sent"
                            error = ""
                            break
                        if attempts > send_retries or not retryable_send_error(error):
                            # An RPC failure can occur after acceptance. Reconcile by hash,
                            # never infer success merely from the account's next nonce.
                            try:
                                known = rpc_client.call("eth_getTransactionByHash", [local_hash])
                                receipt = None if known else rpc_client.call(
                                    "eth_getTransactionReceipt", [local_hash]
                                )
                                if ((isinstance(known, dict) and known.get("hash", "").lower() == local_hash.lower())
                                        or (isinstance(receipt, dict) and receipt.get("transactionHash", "").lower() == local_hash.lower())):
                                    tx_hash, status, error = local_hash, "sent", ""
                            except Exception as lookup_error:
                                error += f"; reconciliation failed: {normalized_error(lookup_error)}"
                            break
                        time.sleep(min(0.05 * (2 ** (attempts - 1)), 0.5))
            except Exception as exc:
                error = normalized_error(exc)

            if status != "sent":
                blocked_senders.add(job.sender_index)
            results.put(
                TransactionResult(
                    job=job,
                    status=status,
                    tx_hash=tx_hash,
                    error=error,
                    worker_id=worker_id,
                    rpc_url=rpc_url,
                    started_at=started_at,
                    rpc_started_at=rpc_started_at,
                    completed_at=time.monotonic(),
                    completed_timestamp=utc_now(),
                    attempts=max(1, attempts),
                )
            )
        finally:
            jobs.task_done()


def result_writer(
    sender_path: Path,
    results: queue.Queue[Optional[TransactionResult]],
    counters: Counters,
    phase_counters: dict[str, Counters],
    counters_lock: threading.Lock,
    stop_event: threading.Event,
    fatal_errors: list[str],
    experiment_started: float,
    flush_interval: float,
    flush_requested: threading.Event,
    flush_completed: threading.Event,
    max_consecutive_failures: int,
) -> None:
    consecutive_failures = 0
    next_flush = time.monotonic() + flush_interval
    with sender_path.open("w", newline="") as sender_handle:
        writer = csv.DictWriter(sender_handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        sender_handle.flush()
        while True:
            try:
                result = results.get(timeout=flush_interval)
            except queue.Empty:
                sender_handle.flush()
                if flush_requested.is_set():
                    flush_requested.clear()
                    flush_completed.set()
                continue
            try:
                if result is None:
                    sender_handle.flush()
                    return
                job = result.job
                with counters_lock:
                    targets = (counters, phase_counters[job.phase])
                    for target in targets:
                        target.attempted += 1
                        target.send_retries += max(0, result.attempts - 1)
                    if result.status == "sent":
                        for target in targets:
                            target.sent += 1
                        consecutive_failures = 0
                    else:
                        for target in targets:
                            target.failed += 1
                        if not fatal_errors:
                            fatal_errors.append(
                                f"unresolved transaction submission: sender={job.sender} "
                                f"nonce={job.nonce}; {result.error}"
                            )
                            stop_event.set()
                        consecutive_failures += 1
                        if (
                            max_consecutive_failures > 0
                            and consecutive_failures >= max_consecutive_failures
                            and not fatal_errors
                        ):
                            fatal_errors.append(
                                f"reached {consecutive_failures} consecutive send failures; "
                                f"last={result.error}"
                            )
                            stop_event.set()

                writer.writerow(
                    {
                        "timestamp": result.completed_timestamp,
                        "elapsed_sec": f"{result.completed_at - experiment_started:.6f}",
                        "seq": job.sequence,
                        "phase": job.phase,
                        "target_rate": job.target_rate,
                        "tx_type": job.tx_type.name,
                        "tx_envelope": job.envelope.name,
                        "transaction_type": job.envelope.transaction_type,
                        "status": result.status,
                        "tx_hash": result.tx_hash,
                        "sender_index": job.sender_index,
                        "recipient_index": job.recipient_index,
                        "sender": job.sender,
                        "recipient": job.recipient,
                        "nonce": job.nonce,
                        "amount_wei": job.amount_wei,
                        "gas_limit": job.tx_type.gas_limit,
                        "gas_price_wei": job.gas_price_wei,
                        "max_fee_per_gas_wei": job.max_fee_per_gas_wei,
                        "max_priority_fee_per_gas_wei": (
                            job.max_priority_fee_per_gas_wei
                        ),
                        "data_bytes": job.data_bytes,
                        "schedule_lag_ms": (
                            f"{max(0.0, job.enqueued_at - job.due_at) * 1000.0:.3f}"
                        ),
                        "queue_wait_ms": (
                            f"{max(0.0, result.started_at - job.enqueued_at) * 1000.0:.3f}"
                        ),
                        "sign_latency_ms": (
                            f"{max(0.0, result.rpc_started_at - result.started_at) * 1000.0:.3f}"
                        ),
                        "rpc_latency_ms": (
                            f"{max(0.0, result.completed_at - result.rpc_started_at) * 1000.0:.3f}"
                        ),
                        "worker_id": result.worker_id,
                        "rpc_url": result.rpc_url,
                        "send_attempts": result.attempts,
                        "error": result.error,
                    }
                )
                if time.monotonic() >= next_flush:
                    sender_handle.flush()
                    next_flush = time.monotonic() + flush_interval
                if flush_requested.is_set():
                    sender_handle.flush()
                    flush_requested.clear()
                    flush_completed.set()
            finally:
                results.task_done()


def receipt_progress(path: Optional[Path]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    if path is None or not path.exists():
        return None, None, None
    try:
        payload = json.loads(path.read_text())
        return (
            int(payload["known_sent_transactions"]),
            int(payload["committed_transactions"]),
            int(payload["pending_transactions"]),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None, None, None


def wait_for_receipt_drain(
    path: Optional[Path],
    expected_sent: int,
    timeout: float,
    interval: float,
    settle_seconds: float,
    stop_event: threading.Event,
    minimum_commit_pct: float = 100.0,
) -> ReceiptDrainResult:
    if timeout <= 0:
        return ReceiptDrainResult("disabled", 0.0, None, None, None)

    started = time.monotonic()
    deadline = started + timeout
    settled_at: Optional[float] = None
    known: Optional[int] = None
    committed: Optional[int] = None
    pending: Optional[int] = None

    while not stop_event.is_set():
        now = time.monotonic()
        known, committed, pending = receipt_progress(path)
        caught_up = (
            known is not None
            and committed is not None
            and pending is not None
            and known >= expected_sent
            and committed >= expected_sent
            and pending == 0
        )
        if caught_up:
            if settled_at is None:
                settled_at = now
            if now - settled_at >= settle_seconds:
                return ReceiptDrainResult(
                    "complete",
                    now - started,
                    known,
                    committed,
                    pending,
                )
        else:
            settled_at = None
        if now >= deadline:
            bounded = bool(
                known is not None
                and committed is not None
                and pending is not None
                and known >= expected_sent
                and committed * 100.0
                >= expected_sent * minimum_commit_pct
                and pending <= max(known - committed, 0)
            )
            return ReceiptDrainResult(
                "bounded" if bounded else "timeout",
                now - started,
                known,
                committed,
                pending,
            )
        stop_event.wait(min(interval, max(0.0, deadline - now)))

    return ReceiptDrainResult(
        "stopped",
        time.monotonic() - started,
        known,
        committed,
        pending,
    )


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
        "scheduled": counters.scheduled,
        "enqueued": counters.enqueued,
        "attempted": counters.attempted,
        "sent": counters.sent,
        "failed": counters.failed,
        "scheduler_skipped": counters.scheduler_skipped,
        "queue_dropped": counters.queue_dropped,
        "send_retries": counters.send_retries,
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
            "generator_version": GENERATOR_VERSION,
            "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "profile": profile.name,
            "profile_path": str(args.profile.resolve()),
            "profile_sha256": hashlib.sha256(args.profile.read_bytes()).hexdigest(),
            "templates_sha256": hashlib.sha256(TEMPLATES_FILE.read_bytes()).hexdigest(),
            "chain_id": args.chain_id,
            "transaction_types": [item.__dict__ for item in profile.transaction_types],
            "transaction_envelopes": [item.__dict__ for item in profile.envelopes],
            "phase_schedule": [phase.__dict__ for phase in phases],
            "configured_duration_sec": sum(phase.duration for phase in phases),
            "account_count": args.accounts,
            "seed": args.seed,
            "send_workers": args.send_workers,
            "sign_workers": args.sign_workers,
            "signer_max_tasks": args.signer_max_tasks,
            "rpc_targets": args.rpc_targets,
            "resolved_rpc_urls": getattr(args, "resolved_rpc_urls", []),
            "queue_depth": args.queue_depth,
            "send_retries": args.send_retries,
            "receipt_state_file": (
                str(args.receipt_state_file.resolve()) if args.receipt_state_file else ""
            ),
            "phase_drain_timeout_sec": args.phase_drain_timeout,
            "phase_drain_interval_sec": args.phase_drain_interval,
            "phase_settle_seconds": args.phase_settle_seconds,
            "phase_drain_min_commit_pct": args.phase_drain_min_commit_pct,
            "phase_drain_required": args.phase_drain_required,
            "current_phase": current_phase,
            "scheduled": counters.scheduled,
            "enqueued": counters.enqueued,
            "attempted": counters.attempted,
            "sent": counters.sent,
            "failed": counters.failed,
            "scheduler_skipped": counters.scheduler_skipped,
            "queue_dropped": counters.queue_dropped,
            "send_retry_attempts": counters.send_retries,
            "does_not_wait_for_confirmations": True,
            "error": error,
        },
    )


def run_schedule(
    args: argparse.Namespace,
    profile: WorkloadProfile,
    phases: list[Phase],
    rpc_urls: list[str],
) -> int:
    if args.log_dir is None:
        raise ValueError("--log-dir is required for sending")
    setup = json.loads(args.contracts_file.read_text())
    if setup.get("chain_id") != args.chain_id or setup.get("profile") != profile.name:
        raise ValueError("contracts file chain_id/profile differs from the selected workload")
    contract_addresses = {
        str(name): str(address)
        for name, address in (setup.get("contract_addresses") or {}).items()
    }
    if not contract_addresses and setup.get("contract_address"):
        contract_addresses["default"] = str(setup["contract_address"])
    required_contracts = {
        item.contract
        for item in profile.transaction_types
        if item.kind == "contract_call"
    }
    missing_contracts = required_contracts - contract_addresses.keys()
    if missing_contracts:
        raise ValueError(
            f"contracts file is missing contract addresses {sorted(missing_contracts)}: "
            f"{args.contracts_file}"
        )

    accounts = build_accounts(args.mnemonic, args.accounts)
    args.resolved_rpc_urls = list(rpc_urls)
    w3 = connect(rpc_urls[0])
    if int(w3.eth.chain_id) != args.chain_id:
        raise RuntimeError(f"RPC chain id is {w3.eth.chain_id}, expected {args.chain_id}")
    for rpc_url in rpc_urls:
        rpc_probe = connect(rpc_url)
        if int(rpc_probe.eth.chain_id) != args.chain_id:
            raise RuntimeError(
                f"RPC {rpc_url} chain id is {rpc_probe.eth.chain_id}, "
                f"expected {args.chain_id}"
            )
    worker_urls = [rpc_urls[index % len(rpc_urls)] for index in range(args.send_workers)]
    worker_clients = [DirectJsonRpcClient(rpc_url) for rpc_url in worker_urls]
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
    counters = Counters()
    phase_counters = {phase.name: Counters() for phase in phases}
    counters_lock = threading.Lock()
    phase_rows: list[dict[str, Any]] = []
    stop_event = threading.Event()
    fatal_errors: list[str] = []
    flush_requested = threading.Event()
    flush_completed = threading.Event()
    experiment_started = time.monotonic()
    next_status = experiment_started + args.status_interval
    gas_cache = (0.0, 0)
    floor_wei = int(args.gas_price_floor_gwei * Decimal(10**9))
    sequence = 0
    completed_phases = 0

    job_queues: list[queue.Queue[Optional[TransactionJob]]] = [
        queue.Queue(maxsize=args.queue_depth) for _ in range(args.send_workers)
    ]
    result_queue: queue.Queue[Optional[TransactionResult]] = queue.Queue(
        maxsize=max(args.send_workers * args.queue_depth * 2, args.send_workers)
    )
    signers = [
        SignerClient(Path(__file__).resolve(), args.signer_max_tasks)
        for _index in range(args.sign_workers)
    ]
    writer_thread = threading.Thread(
        target=result_writer,
        name="tx-result-writer",
        args=(
            sender_path,
            result_queue,
            counters,
            phase_counters,
            counters_lock,
            stop_event,
            fatal_errors,
            experiment_started,
            args.log_flush_interval,
            flush_requested,
            flush_completed,
            args.max_consecutive_failures,
        ),
    )
    worker_threads = [
        threading.Thread(
            target=sender_worker,
            name=f"tx-sender-{worker_id}",
            args=(
                worker_id,
                worker_urls[worker_id],
                worker_clients[worker_id],
                accounts,
                job_queues[worker_id],
                result_queue,
                args.send_retries,
                signers[worker_id % len(signers)],
            ),
        )
        for worker_id in range(args.send_workers)
    ]

    def stop_handler(signum: int, frame: object) -> None:  # noqa: ARG001
        stop_event.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    writer_thread.start()
    for worker_thread in worker_threads:
        worker_thread.start()
    write_run_info(run_info_path, "running", args, profile, phases, counters, "")

    try:
        for phase_index, phase in enumerate(phases, start=1):
            if stop_event.is_set():
                break
            phase_started = time.monotonic()
            phase_started_at = utc_now()
            current_counters = phase_counters[phase.name]
            target_jobs = int(math.floor(phase.rate * phase.duration + 1e-9))
            atomic_json(
                state_path,
                phase_state(
                    phase,
                    phase_index,
                    len(phases),
                    "running",
                    phase_started,
                    experiment_started,
                    copy_counters(current_counters),
                ),
            )
            slot = 0
            while not stop_event.is_set() and slot < target_jobs:
                due_at = phase_started + (slot / phase.rate)
                now = time.monotonic()
                if now < due_at:
                    stop_event.wait(due_at - now)
                    continue
                lag = max(0.0, now - due_at)
                if lag > args.max_catchup_seconds:
                    latest_expired = min(
                        target_jobs - 1,
                        int(
                            math.floor(
                                (now - args.max_catchup_seconds - phase_started)
                                * phase.rate
                            )
                        ),
                    )
                    skipped = max(1, latest_expired - slot + 1)
                    with counters_lock:
                        for target in (counters, current_counters):
                            target.scheduled += skipped
                            target.scheduler_skipped += skipped
                    sequence += skipped
                    slot += skipped
                    continue

                job_rng = transaction_rng(args.seed, sequence)
                tx_type = choose_transaction_type(job_rng, profile.transaction_types)
                envelope = choose_envelope(job_rng, profile.envelopes)
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
                (
                    tx,
                    recipient_index,
                    recipient,
                    amount_wei,
                    data_bytes,
                    max_fee_per_gas,
                    max_priority_fee_per_gas,
                ) = build_transaction(
                    args,
                    profile,
                    tx_type,
                    envelope,
                    accounts,
                    sender_index,
                    nonce,
                    contract_addresses,
                    min_wei,
                    max_wei,
                    gas_price,
                    job_rng,
                )
                enqueued_at = time.monotonic()
                job = TransactionJob(
                    sequence=sequence + 1,
                    phase=phase.name,
                    target_rate=phase.rate,
                    due_at=due_at,
                    enqueued_at=enqueued_at,
                    tx_type=tx_type,
                    envelope=envelope,
                    sender_index=sender_index,
                    recipient_index=recipient_index,
                    sender=sender.address,
                    recipient=recipient,
                    nonce=nonce,
                    amount_wei=amount_wei,
                    gas_price_wei=gas_price,
                    max_fee_per_gas_wei=max_fee_per_gas,
                    max_priority_fee_per_gas_wei=max_priority_fee_per_gas,
                    data_bytes=data_bytes,
                    transaction=tx,
                )
                with counters_lock:
                    for target in (counters, current_counters):
                        target.scheduled += 1
                lane = sender_index % args.send_workers
                try:
                    job_queues[lane].put_nowait(job)
                except queue.Full:
                    with counters_lock:
                        for target in (counters, current_counters):
                            target.queue_dropped += 1
                else:
                    nonces[sender.address] = nonce + 1
                    with counters_lock:
                        for target in (counters, current_counters):
                            target.enqueued += 1
                sequence += 1
                slot += 1
                if time.monotonic() >= next_status:
                    with counters_lock:
                        phase_snapshot = copy_counters(current_counters)
                        total_snapshot = copy_counters(counters)
                    atomic_json(
                        state_path,
                        phase_state(
                            phase,
                            phase_index,
                            len(phases),
                            "running",
                            phase_started,
                            experiment_started,
                            phase_snapshot,
                        ),
                    )
                    write_run_info(
                        run_info_path,
                        "running",
                        args,
                        profile,
                        phases,
                        total_snapshot,
                        phase.name,
                    )
                    elapsed = max(time.monotonic() - phase_started, 0.001)
                    print(
                        f"{utc_now()} phase={phase.name} target={phase.rate:g}/s "
                        f"sent={phase_snapshot.sent} failed={phase_snapshot.failed} "
                        f"skipped={phase_snapshot.scheduler_skipped} "
                        f"dropped={phase_snapshot.queue_dropped} "
                        f"achieved={phase_snapshot.sent / elapsed:.2f}/s",
                        flush=True,
                    )
                    next_status = time.monotonic() + args.status_interval

            remaining = phase.duration - (time.monotonic() - phase_started)
            if remaining > 0 and not stop_event.is_set():
                stop_event.wait(remaining)
            drain_started = time.monotonic()
            for job_queue in job_queues:
                job_queue.join()
            result_queue.join()
            flush_completed.clear()
            flush_requested.set()
            if not flush_completed.wait(timeout=max(5.0, args.log_flush_interval * 4.0)):
                raise RuntimeError("transaction sender log flush timed out")
            drain_duration = time.monotonic() - drain_started
            phase_ended = time.monotonic()
            phase_ended_at = utc_now()
            with counters_lock:
                phase_snapshot = copy_counters(current_counters)
                total_snapshot = copy_counters(counters)

            atomic_json(
                state_path,
                {
                    **phase_state(
                        phase,
                        phase_index,
                        len(phases),
                        "draining",
                        phase_started,
                        experiment_started,
                        phase_snapshot,
                    ),
                    "phase": f"__drain_after_{phase.name}",
                    "expected_sent": total_snapshot.sent,
                },
            )
            receipt_drain = wait_for_receipt_drain(
                args.receipt_state_file,
                total_snapshot.sent,
                args.phase_drain_timeout,
                args.phase_drain_interval,
                args.phase_settle_seconds,
                stop_event,
                args.phase_drain_min_commit_pct,
            )
            phase_rows.append(
                {
                    "phase": phase.name,
                    "target_rate": phase.rate,
                    "configured_duration_sec": phase.duration,
                    "start_timestamp": phase_started_at,
                    "end_timestamp": phase_ended_at,
                    "start_elapsed_sec": f"{phase_started - experiment_started:.6f}",
                    "end_elapsed_sec": f"{phase_ended - experiment_started:.6f}",
                    "scheduled": phase_snapshot.scheduled,
                    "enqueued": phase_snapshot.enqueued,
                    "attempted": phase_snapshot.attempted,
                    "sent": phase_snapshot.sent,
                    "failed": phase_snapshot.failed,
                    "scheduler_skipped": phase_snapshot.scheduler_skipped,
                    "queue_dropped": phase_snapshot.queue_dropped,
                    "send_retries": phase_snapshot.send_retries,
                    "drain_duration_sec": f"{drain_duration:.6f}",
                    "receipt_drain_status": receipt_drain.status,
                    "receipt_drain_duration_sec": f"{receipt_drain.duration:.6f}",
                    "receipt_known_at_end": (
                        "" if receipt_drain.known is None else receipt_drain.known
                    ),
                    "receipt_committed_at_end": (
                        "" if receipt_drain.committed is None else receipt_drain.committed
                    ),
                    "receipt_pending_at_end": (
                        "" if receipt_drain.pending is None else receipt_drain.pending
                    ),
                }
            )
            with phases_path.open("w", newline="") as phase_handle:
                phase_writer = csv.DictWriter(phase_handle, fieldnames=PHASE_FIELDS)
                phase_writer.writeheader()
                phase_writer.writerows(phase_rows)
            if (
                args.phase_drain_required
                and receipt_drain.status not in {"complete", "bounded"}
            ):
                fatal_errors.append(
                    "receipt drain after phase {} ended with status={} "
                    "known={} committed={} pending={} expected={} "
                    "minimum_commit_pct={}".format(
                        phase.name,
                        receipt_drain.status,
                        receipt_drain.known,
                        receipt_drain.committed,
                        receipt_drain.pending,
                        total_snapshot.sent,
                        args.phase_drain_min_commit_pct,
                    )
                )
                stop_event.set()
            if (
                not stop_event.is_set()
                and slot == target_jobs
                and time.monotonic() - phase_started >= phase.duration
            ):
                completed_phases += 1

        completion_status = "complete" if completed_phases == len(phases) else "stopped"
    finally:
        for job_queue in job_queues:
            job_queue.put(None)
        for job_queue in job_queues:
            job_queue.join()
        for worker_thread in worker_threads:
            worker_thread.join()
        for worker_client in worker_clients:
            worker_client.close()
        for signer in signers:
            signer.close()
        result_queue.join()
        result_queue.put(None)
        result_queue.join()
        writer_thread.join()

    with counters_lock:
        total_snapshot = copy_counters(counters)
    if fatal_errors:
        completion_status = "failed"
    atomic_json(
        state_path,
        {
            "status": completion_status,
            "phase": phase_rows[-1]["phase"] if phase_rows else "",
            "phase_count": len(phases),
            "completed_phases": completed_phases,
            "elapsed_sec": time.monotonic() - experiment_started,
            "scheduled": total_snapshot.scheduled,
            "enqueued": total_snapshot.enqueued,
            "attempted": total_snapshot.attempted,
            "sent": total_snapshot.sent,
            "failed": total_snapshot.failed,
            "scheduler_skipped": total_snapshot.scheduler_skipped,
            "queue_dropped": total_snapshot.queue_dropped,
            "send_retry_attempts": total_snapshot.send_retries,
            "updated_at": utc_now(),
        },
    )
    write_run_info(
        run_info_path,
        completion_status,
        args,
        profile,
        phases,
        total_snapshot,
        phase_rows[-1]["phase"] if phase_rows else "",
        fatal_errors[0] if fatal_errors else "",
    )
    if fatal_errors:
        raise RuntimeError(fatal_errors[0])
    while args.hold_after_schedule and not stop_event.is_set():
        stop_event.wait(0.5)
    return 0 if completed_phases == len(phases) else 130


def validate_args(args: argparse.Namespace) -> tuple[WorkloadProfile, list[Phase], list[str]]:
    if args.mnemonic_file:
        args.mnemonic = args.mnemonic_file.read_text().strip()
        if not args.mnemonic:
            raise ValueError("mnemonic file is empty")
    if args.accounts < 2:
        raise ValueError("--accounts must be at least 2")
    if not args.dry_run:
        if not args.contracts_file.is_file():
            raise ValueError("contracts file missing; run deploy_contracts.py first")
        if args.log_dir is None:
            raise ValueError("--log-dir is required for sending")
        for name in ("tx_sender.csv", "tx_phases.csv", "tx_phase_state.json", "run_info.json"):
            if (args.log_dir / name).exists():
                raise ValueError(f"sender output exists: {args.log_dir / name}; choose a new log directory")
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
    if (
        args.send_workers < 1
        or args.sign_workers < 1
        or args.rpc_targets < 1
        or args.queue_depth < 1
    ):
        raise ValueError(
            "send workers, sign workers, RPC targets, and queue depth must be positive"
        )
    if args.signer_max_tasks < 0:
        raise ValueError("signer max tasks must be non-negative")
    if args.send_retries < 0:
        raise ValueError("send retries must be non-negative")
    if (
        args.phase_drain_timeout < 0
        or args.phase_drain_interval <= 0
        or args.phase_settle_seconds < 0
        or not 0 <= args.phase_drain_min_commit_pct <= 100
    ):
        raise ValueError(
            "phase drain timeout/settle must be non-negative, interval positive, "
            "and minimum commit percentage in [0, 100]"
        )
    if args.phase_drain_timeout > 0 and args.receipt_state_file is None:
        raise ValueError("--receipt-state-file is required when phase drain is enabled")
    if args.gas_price_floor_gwei < 0 or args.gas_price_multiplier < 1:
        raise ValueError("gas price floor must be non-negative and multiplier at least one")
    profile = load_profile(args.profile)
    phases = parse_phases(args.phases)
    if args.rpc_url:
        rpc_urls = list(dict.fromkeys(args.rpc_url))
    elif args.dry_run:
        rpc_urls = [f"auto-discover:{args.compose_file} (up to {args.rpc_targets})"]
    else:
        rpc_urls = discover_execution_rpc_urls(args.compose_file, args.rpc_targets)
    return profile, phases, rpc_urls


def main() -> int:
    faulthandler.enable(all_threads=True)
    args = parse_args()
    validated = False
    try:
        profile, phases, rpc_urls = validate_args(args)
        validated = True
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "profile": profile.name,
                        "generator_version": GENERATOR_VERSION,
                        "transaction_types": [item.__dict__ for item in profile.transaction_types],
                        "transaction_envelopes": [
                            item.__dict__ for item in profile.envelopes
                        ],
                        "setup_contracts": [
                            {
                                "name": item.name,
                                "gas_limit": item.gas_limit,
                                "init_code_bytes": len(item.init_code),
                                "init_code_sha256": hashlib.sha256(
                                    item.init_code
                                ).hexdigest(),
                            }
                            for item in profile.setup_contracts
                        ],
                        "phases": [phase.__dict__ for phase in phases],
                        "duration_sec": sum(phase.duration for phase in phases),
                        "send_workers": args.send_workers,
                        "sign_workers": args.sign_workers,
                        "signer_max_tasks": args.signer_max_tasks,
                        "rpc_targets": args.rpc_targets,
                        "queue_depth": args.queue_depth,
                        "send_retries": args.send_retries,
                        "receipt_state_file": (
                            str(args.receipt_state_file) if args.receipt_state_file else ""
                        ),
                        "phase_drain_timeout_sec": args.phase_drain_timeout,
                        "phase_drain_interval_sec": args.phase_drain_interval,
                        "phase_settle_seconds": args.phase_settle_seconds,
                        "phase_drain_min_commit_pct": (
                            args.phase_drain_min_commit_pct
                        ),
                        "phase_drain_required": args.phase_drain_required,
                        "rpc_urls": rpc_urls,
                    },
                    indent=2,
                )
            )
            return 0
        return run_schedule(args, profile, phases, rpc_urls)
    except Exception as exc:
        if args.log_dir and validated and not args.dry_run:
            try:
                run_info_path = args.log_dir / "run_info.json"
                if run_info_path.exists():
                    payload = json.loads(run_info_path.read_text())
                    payload.update(
                        {
                            "status": "failed",
                            "updated_at": utc_now(),
                            "error": normalized_error(exc),
                        }
                    )
                    atomic_json(run_info_path, payload)
                else:
                    profile = load_profile(args.profile)
                    phases = parse_phases(args.phases)
                    write_run_info(
                        run_info_path,
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
    if sys.argv[1:] == ["--internal-signer-worker"]:
        raise SystemExit(signer_worker_main())
    raise SystemExit(main())
