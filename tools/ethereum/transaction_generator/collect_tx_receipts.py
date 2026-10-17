#!/usr/bin/env python3
"""Track transaction inclusion and latency from a growing sender log."""

from __future__ import annotations

import argparse
import csv
import json
import signal
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from compose_endpoints import discover_execution_rpc_url


RECEIPT_FIELDS = [
    "timestamp",
    "elapsed_sec",
    "tx_hash",
    "phase",
    "target_rate",
    "tx_type",
    "tx_envelope",
    "transaction_type",
    "accepted_timestamp",
    "accepted_elapsed_sec",
    "commit_observed_timestamp",
    "commit_latency_observed_sec",
    "block_timestamp_unix",
    "block_timestamp_latency_sec",
    "block_number",
    "block_hash",
    "transaction_index",
    "status",
    "gas_limit",
    "gas_used",
    "effective_gas_price",
    "data_bytes",
]
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tx-log", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("-f", "--compose-file", type=Path, help="Discover the execution RPC from a SeedEmu PoS Compose file")
    target.add_argument("--rpc-url", help="Execution HTTP RPC")
    parser.add_argument("-i", "--interval", type=float, default=12.0)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--mode",
        choices=["receipts", "blocks"],
        default="receipts",
        help="blocks scans one receipt set per block and is intended for high transaction rates.",
    )
    parser.add_argument("--max-pending-polls", type=int, default=2000)
    parser.add_argument("--recent-blocks", type=int, default=8)
    parser.add_argument("--reconcile-age", type=float, default=120.0)
    parser.add_argument("--reconcile-interval", type=float, default=30.0)
    parser.add_argument("--sender-state", type=Path, help="Sender run_info.json; exit after successful sending and receipt drain")
    parser.add_argument("--drain-timeout", type=float, default=1800, help="Seconds to drain after sender completion, when --sender-state is set")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_timestamp(value: str) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def hex_to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, int):
        return value
    return int(str(value), 16)


def post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with NO_PROXY_OPENER.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def rpc_call(rpc_url: str, method: str, params: list[Any], timeout: float) -> Any:
    response = post_json(
        rpc_url,
        {"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        timeout,
    )
    if "error" in response:
        raise RuntimeError(response["error"])
    return response.get("result")


class TransactionLogTail:
    """Incrementally read complete CSV rows without repeatedly scanning the file."""

    def __init__(self, path: Path):
        self.path = path
        self.position = 0
        self.fieldnames: Optional[list[str]] = None

    def read(self) -> list[dict[str, str]]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        with self.path.open(newline="") as handle:
            if self.fieldnames is None:
                header = handle.readline()
                if not header or not header.endswith(("\n", "\r")):
                    return []
                self.fieldnames = next(csv.reader([header]))
                self.position = handle.tell()
            handle.seek(self.position)
            complete_lines: list[str] = []
            while True:
                row_position = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith(("\n", "\r")):
                    self.position = row_position
                    break
                complete_lines.append(line)
                self.position = handle.tell()
        return list(csv.DictReader(complete_lines, fieldnames=self.fieldnames))


def append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RECEIPT_FIELDS, extrasaction="ignore")
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)


def atomic_run_info(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def receipt_row(
    accepted: dict[str, str],
    receipt: dict[str, Any],
    block_timestamp: int,
    elapsed: float,
    observed_at: str,
    now_wall: float,
) -> dict[str, Any]:
    accepted_ts = parse_timestamp(accepted.get("timestamp", ""))
    return {
        "timestamp": observed_at,
        "elapsed_sec": f"{elapsed:.3f}",
        "tx_hash": accepted.get("tx_hash", ""),
        "phase": accepted.get("phase", ""),
        "target_rate": accepted.get("target_rate", ""),
        "tx_type": accepted.get("tx_type", ""),
        "tx_envelope": accepted.get("tx_envelope", ""),
        "transaction_type": accepted.get("transaction_type", ""),
        "accepted_timestamp": accepted.get("timestamp", ""),
        "accepted_elapsed_sec": accepted.get("elapsed_sec", ""),
        "commit_observed_timestamp": observed_at,
        "commit_latency_observed_sec": (
            f"{now_wall - accepted_ts:.3f}" if accepted_ts is not None else ""
        ),
        "block_timestamp_unix": block_timestamp or "",
        "block_timestamp_latency_sec": (
            f"{block_timestamp - accepted_ts:.3f}"
            if accepted_ts is not None and block_timestamp
            else ""
        ),
        "block_number": hex_to_int(receipt.get("blockNumber")),
        "block_hash": receipt.get("blockHash", ""),
        "transaction_index": hex_to_int(receipt.get("transactionIndex")),
        "status": hex_to_int(receipt.get("status")) if receipt.get("status") is not None else "",
        "gas_limit": accepted.get("gas_limit", accepted.get("gas", "")),
        "gas_used": hex_to_int(receipt.get("gasUsed")) if receipt.get("gasUsed") else "",
        "effective_gas_price": (
            hex_to_int(receipt.get("effectiveGasPrice"))
            if receipt.get("effectiveGasPrice")
            else ""
        ),
        "data_bytes": accepted.get("data_bytes", ""),
    }


def inclusion_from_block(
    tx_hash: str,
    block: dict[str, Any],
    receipt: Optional[dict[str, Any]],
    transaction_index: int,
) -> dict[str, Any]:
    if receipt:
        return receipt
    return {
        "transactionHash": tx_hash,
        "blockHash": block.get("hash", ""),
        "blockNumber": block.get("number", "0x0"),
        "transactionIndex": hex(transaction_index),
        "status": None,
    }


def stale_pending_hashes(
    pending_since: dict[str, float],
    now: float,
    minimum_age: float,
    limit: int,
) -> list[str]:
    eligible = (
        tx_hash
        for tx_hash, first_seen in pending_since.items()
        if now - first_seen >= minimum_age
    )
    return list(eligible)[: limit or None]


def main() -> int:
    args = parse_args()
    if args.interval <= 0 or args.timeout <= 0:
        print("--interval and --timeout must be positive")
        return 2
    if args.duration < 0 or args.drain_timeout <= 0:
        print("--duration must be nonnegative and --drain-timeout positive")
        return 2
    if args.output.exists() or (args.output.parent / "tx_receipts_run_info.json").exists():
        print("receipt output exists; choose a new output directory")
        return 2
    if (
        args.max_pending_polls < 0
        or args.recent_blocks < 1
        or args.reconcile_age < 0
        or args.reconcile_interval <= 0
    ):
        print("pending poll limit must be non-negative and recent block count positive")
        return 2

    rpc_url = args.rpc_url or discover_execution_rpc_url(args.compose_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run_info_path = args.output.parent / "tx_receipts_run_info.json"
    tail = TransactionLogTail(args.tx_log)
    pending: dict[str, dict[str, str]] = {}
    pending_since: dict[str, float] = {}
    known = 0
    committed = 0
    reconciled = 0
    reconciliation_rpc_calls = 0
    rpc_errors = 0
    blocks_scanned = 0
    receipt_sets_supported: Optional[bool] = None
    last_block: Optional[int] = None
    last_reconciliation = 0.0
    recent: deque[dict[str, tuple[dict[str, Any], int]]] = deque(maxlen=args.recent_blocks)
    stop = False

    def stop_handler(signum: int, frame: object) -> None:  # noqa: ARG001
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    started = time.monotonic()
    drain_started: Optional[float] = None

    def add_new_transactions() -> list[dict[str, Any]]:
        nonlocal known, committed
        rows: list[dict[str, Any]] = []
        recent_lookup = {
            tx_hash: value
            for block_items in recent
            for tx_hash, value in block_items.items()
        }
        for accepted in tail.read():
            if accepted.get("status") != "sent":
                continue
            tx_hash = accepted.get("tx_hash", "")
            if not tx_hash:
                continue
            known += 1
            inclusion = recent_lookup.get(tx_hash)
            if inclusion is None:
                pending[tx_hash] = accepted
                pending_since.setdefault(tx_hash, time.monotonic())
                continue
            receipt, block_timestamp = inclusion
            rows.append(
                receipt_row(
                    accepted,
                    receipt,
                    block_timestamp,
                    time.monotonic() - started,
                    utc_now(),
                    time.time(),
                )
            )
            committed += 1
        return rows

    def scan_receipt_hashes(
        tx_hashes: list[str],
        *,
        reconciliation: bool = False,
    ) -> list[dict[str, Any]]:
        nonlocal committed, reconciled, reconciliation_rpc_calls, rpc_errors
        rows: list[dict[str, Any]] = []
        for tx_hash in tx_hashes:
            if reconciliation:
                reconciliation_rpc_calls += 1
            try:
                receipt = rpc_call(
                    rpc_url, "eth_getTransactionReceipt", [tx_hash], args.timeout
                )
            except Exception:
                rpc_errors += 1
                continue
            if not receipt:
                continue
            block_hash = receipt.get("blockHash", "")
            block_timestamp = 0
            if block_hash:
                try:
                    block = rpc_call(
                        rpc_url, "eth_getBlockByHash", [block_hash, False], args.timeout
                    )
                    block_timestamp = hex_to_int(block.get("timestamp")) if block else 0
                except Exception:
                    rpc_errors += 1
            accepted = pending.pop(tx_hash)
            pending_since.pop(tx_hash, None)
            rows.append(
                receipt_row(
                    accepted,
                    receipt,
                    block_timestamp,
                    time.monotonic() - started,
                    utc_now(),
                    time.time(),
                )
            )
            committed += 1
            if reconciliation:
                reconciled += 1
        return rows

    def scan_receipts_mode() -> list[dict[str, Any]]:
        return scan_receipt_hashes(
            list(pending)[: args.max_pending_polls or None]
        )

    def reconcile_stale_pending() -> list[dict[str, Any]]:
        nonlocal last_reconciliation
        now = time.monotonic()
        if now - last_reconciliation < args.reconcile_interval:
            return []
        last_reconciliation = now
        return scan_receipt_hashes(
            stale_pending_hashes(
                pending_since,
                now,
                args.reconcile_age,
                args.max_pending_polls,
            ),
            reconciliation=True,
        )

    def scan_blocks_mode() -> list[dict[str, Any]]:
        nonlocal committed, rpc_errors, blocks_scanned, receipt_sets_supported, last_block
        rows: list[dict[str, Any]] = []
        try:
            current = hex_to_int(rpc_call(rpc_url, "eth_blockNumber", [], args.timeout))
        except Exception:
            rpc_errors += 1
            return rows
        if last_block is None:
            last_block = max(0, current - 2)
        for number in range(last_block + 1, current + 1):
            try:
                block = rpc_call(
                    rpc_url, "eth_getBlockByNumber", [hex(number), False], args.timeout
                )
            except Exception:
                rpc_errors += 1
                break
            if not block:
                continue
            receipts: list[dict[str, Any]] = []
            if receipt_sets_supported is not False:
                try:
                    receipts = rpc_call(
                        rpc_url, "eth_getBlockReceipts", [hex(number)], args.timeout
                    ) or []
                    receipt_sets_supported = True
                except Exception:
                    rpc_errors += 1
                    receipt_sets_supported = False
            receipt_map = {
                receipt.get("transactionHash", ""): receipt
                for receipt in receipts
                if receipt.get("transactionHash")
            }
            block_timestamp = hex_to_int(block.get("timestamp"))
            block_inclusions: dict[str, tuple[dict[str, Any], int]] = {}
            for index, raw_tx_hash in enumerate(block.get("transactions") or []):
                tx_hash = (
                    raw_tx_hash.get("hash", "")
                    if isinstance(raw_tx_hash, dict)
                    else str(raw_tx_hash)
                )
                receipt = inclusion_from_block(
                    tx_hash,
                    block,
                    receipt_map.get(tx_hash),
                    index,
                )
                block_inclusions[tx_hash] = (receipt, block_timestamp)
                accepted = pending.pop(tx_hash, None)
                if accepted is None:
                    continue
                pending_since.pop(tx_hash, None)
                rows.append(
                    receipt_row(
                        accepted,
                        receipt,
                        block_timestamp,
                        time.monotonic() - started,
                        utc_now(),
                        time.time(),
                    )
                )
                committed += 1
            recent.append(block_inclusions)
            blocks_scanned += 1
            last_block = number
        return rows

    def write_status(status: str) -> None:
        atomic_run_info(
            run_info_path,
            {
                "status": status,
                "mode": args.mode,
                "known_sent_transactions": known,
                "committed_transactions": committed,
                "pending_transactions": len(pending),
                "reconciled_transactions": reconciled,
                "reconciliation_rpc_calls": reconciliation_rpc_calls,
                "blocks_scanned": blocks_scanned,
                "receipt_sets_supported": receipt_sets_supported,
                "rpc_errors": rpc_errors,
                "updated_at": utc_now(),
            },
        )

    write_status("running")
    while not stop:
        elapsed = time.monotonic() - started
        if args.duration and elapsed >= args.duration:
            break
        rows = add_new_transactions()
        if args.mode == "blocks":
            rows.extend(scan_blocks_mode())
            rows.extend(reconcile_stale_pending())
        else:
            rows.extend(scan_receipts_mode())
        append_csv(args.output, rows)
        write_status("running")
        if args.sender_state:
            try:
                sender = json.loads(args.sender_state.read_text())
            except (OSError, ValueError):
                sender = {}
            if sender.get("status") in ("failed", "interrupted", "stopped"):
                write_status("sender_failed")
                return 1
            if sender.get("status") == "complete":
                expected = sender.get("sent")
                if type(expected) is not int or expected < 0:
                    write_status("invalid_sender_state")
                    return 1
                if known == expected and committed == expected and not pending:
                    write_status("complete")
                    return 0
                if drain_started is None:
                    drain_started = time.monotonic()
                if time.monotonic() - drain_started >= args.drain_timeout:
                    write_status("drain_timeout")
                    return 1
        deadline = time.monotonic() + args.interval
        while not stop:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))

    rows = add_new_transactions()
    if not stop:
        if args.mode == "blocks":
            rows.extend(scan_blocks_mode())
            rows.extend(reconcile_stale_pending())
        else:
            rows.extend(scan_receipts_mode())
    append_csv(args.output, rows)
    # A manual stop is not evidence that every sent transaction was confirmed.
    if args.sender_state:
        write_status("interrupted" if stop else "incomplete")
        return 130 if stop else 1
    write_status("stopped" if stop else "complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
