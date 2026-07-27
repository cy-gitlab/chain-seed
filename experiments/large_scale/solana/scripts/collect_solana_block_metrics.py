#!/usr/bin/env python3
"""Collect Solana slot/block/transaction counters from the bootstrap RPC."""

from __future__ import annotations

import argparse
import csv
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence


HEADER = [
    "sample",
    "timestamp",
    "elapsed_sec",
    "slot",
    "slot_delta",
    "block_height",
    "block_height_delta",
    "transaction_count",
    "transaction_count_delta",
    "voting_validators",
    "delinquent_validators",
]


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


def run_cmd_combined(args: list[str], timeout: int = 20) -> str:
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.returncode != 0:
        raise RuntimeError(output or f"command failed: {args}")
    return output


def find_boot_container(pattern: str, docker_timeout: int) -> str:
    explicit = os.environ.get("BOOT_CONTAINER_NAME", "").strip()
    if explicit:
        return explicit
    output = run_cmd(["docker", "ps", "--format", "{{.Names}}"], timeout=docker_timeout)
    for name in output.splitlines():
        if pattern in name:
            return name
    raise RuntimeError(f"bootstrap container not found: {pattern}")


def solana(boot: str, rpc_url: str, command: list[str], timeout: int) -> str:
    return run_cmd(["docker", "exec", boot, "solana", "--url", rpc_url, *command], timeout=timeout)


def parse_int(text: str) -> Optional[int]:
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None


def parse_labeled_int(text: str, labels: Sequence[str]) -> Optional[int]:
    for label in labels:
        pattern = rf"^\s*{re.escape(label)}\s*:\s*([0-9,]+)\s*$"
        match = re.search(pattern, text or "", re.IGNORECASE | re.MULTILINE)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def log_slot(boot: str, docker_timeout: int, tail: int) -> Optional[int]:
    output = run_cmd_combined(
        ["docker", "logs", "--tail", str(tail), boot],
        timeout=docker_timeout,
    )
    slots: list[int] = []
    for pattern in (
        r"\bpoh_recorder\b[^\n]*\bslot=([0-9]+)i?\b",
        r"\boptimistic_slot\b[^\n]*\bslot=([0-9]+)i?\b",
        r"\bleader_slot_[^\n]*\bslot=([0-9]+)i?\b",
    ):
        slots.extend(int(match) for match in re.findall(pattern, output))
    return max(slots) if slots else None


def epoch_info(
    boot: str,
    rpc_url: str,
    rpc_timeout: int,
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    output = solana(boot, rpc_url, ["epoch-info"], timeout=rpc_timeout)
    slot = parse_labeled_int(output, ["Slot", "Absolute Slot"])
    height = parse_labeled_int(output, ["Block Height", "Block height"])
    tx_count = parse_labeled_int(output, ["Transaction Count", "Transaction count"])
    return slot, height, tx_count


def validator_counts(boot: str, rpc_url: str, rpc_timeout: int) -> tuple[Optional[int], Optional[int]]:
    try:
        output = solana(boot, rpc_url, ["validators"], timeout=max(40, rpc_timeout))
    except Exception:
        return None, None
    voting_match = re.search(r"([0-9]+)\s+current validators?", output, re.IGNORECASE)
    if voting_match is None:
        voting_match = re.search(r"current validators?:\s*([0-9]+)", output, re.IGNORECASE)
    delinquent_match = re.search(r"([0-9]+)\s+delinquent validators?", output, re.IGNORECASE)
    if delinquent_match is None:
        delinquent_match = re.search(r"delinquent validators?:\s*([0-9]+)", output, re.IGNORECASE)
    voting = int(voting_match.group(1)) if voting_match else None
    delinquent = int(delinquent_match.group(1)) if delinquent_match else 0
    return voting, delinquent


def append_row(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Solana block metrics.")
    parser.add_argument("--boot-pattern", default="Solana-Bootstrap-000")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8899")
    parser.add_argument("-o", "--output", type=Path, default=Path("block_metrics.csv"))
    parser.add_argument("-i", "--interval", type=int, default=12)
    parser.add_argument("-n", "--samples", type=int, default=0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--docker-timeout", type=int, default=120, help="Timeout for Docker CLI calls.")
    parser.add_argument(
        "--rpc-timeout",
        type=int,
        default=60,
        help="Timeout for each solana CLI RPC query inside the bootstrap container.",
    )
    parser.add_argument(
        "--validators-every",
        type=int,
        default=0,
        help="Collect expensive `solana validators` counts every N samples; 0 disables it.",
    )
    parser.add_argument(
        "--log-slot-fallback",
        action="store_true",
        help="When RPC slot queries fail, read recent validator logs and use their latest slot as liveness evidence.",
    )
    parser.add_argument(
        "--log-slot-only",
        action="store_true",
        help="Only collect the latest slot from validator logs. Intended as a low-cost sidecar fallback.",
    )
    parser.add_argument(
        "--log-tail",
        type=int,
        default=500,
        help="Number of recent docker log lines to scan for slot fallback.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.interval < 1:
        print("--interval must be >= 1", file=sys.stderr)
        return 2
    if args.samples < 0:
        print("--samples must be >= 0", file=sys.stderr)
        return 2
    if args.docker_timeout < 1:
        print("--docker-timeout must be >= 1", file=sys.stderr)
        return 2
    if args.rpc_timeout < 1:
        print("--rpc-timeout must be >= 1", file=sys.stderr)
        return 2
    if args.validators_every < 0:
        print("--validators-every must be >= 0", file=sys.stderr)
        return 2
    if args.log_tail < 1:
        print("--log-tail must be >= 1", file=sys.stderr)
        return 2
    if args.once:
        args.samples = 1

    stop_requested = False

    def handle_stop(signum, frame):  # type: ignore[no-untyped-def]
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    started = time.time()
    sample = 0
    prev_slot: Optional[int] = None
    prev_height: Optional[int] = None
    prev_tx: Optional[int] = None
    boot: Optional[str] = None

    print(f"writing Solana block metrics to {args.output}", flush=True)
    while not stop_requested:
        sample += 1
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        elapsed = round(time.time() - started, 3)

        slot: Optional[int] = None
        height: Optional[int] = None
        tx_count: Optional[int] = None
        validators: Optional[int] = None
        delinquent: Optional[int] = None
        errors: list[str] = []

        try:
            if boot is None:
                boot = find_boot_container(args.boot_pattern, args.docker_timeout)
        except Exception as exc:
            boot = None
            errors.append(f"find_boot_container={exc}")

        if boot is not None and not args.log_slot_only:
            try:
                slot, height, tx_count = epoch_info(boot, args.rpc_url, args.rpc_timeout)
            except Exception as exc:
                errors.append(f"epoch-info={exc}")
                for name, command in (
                    ("slot", ["slot"]),
                    ("block-height", ["block-height"]),
                    ("transaction-count", ["transaction-count"]),
                ):
                    try:
                        value = parse_int(solana(boot, args.rpc_url, command, timeout=args.rpc_timeout))
                    except Exception as fallback_exc:
                        errors.append(f"{name}={fallback_exc}")
                        continue
                    if name == "slot":
                        slot = value
                    elif name == "block-height":
                        height = value
                    else:
                        tx_count = value

            if args.validators_every > 0 and sample % args.validators_every == 0:
                validators, delinquent = validator_counts(boot, args.rpc_url, args.rpc_timeout)

        if boot is not None and (args.log_slot_only or (args.log_slot_fallback and slot is None)):
            try:
                fallback_slot = log_slot(boot, args.docker_timeout, args.log_tail)
            except Exception as exc:
                errors.append(f"log-slot={exc}")
            else:
                if fallback_slot is not None:
                    slot = fallback_slot

        if args.log_slot_only and slot is None:
            boot = None
            print(f"sample={sample} failed: {'; '.join(errors)}", file=sys.stderr, flush=True)
            if args.samples and sample >= args.samples:
                break
            deadline = time.time() + args.interval
            while not stop_requested and time.time() < deadline:
                time.sleep(min(1.0, deadline - time.time()))
            continue

        row = {
            "sample": sample,
            "timestamp": timestamp,
            "elapsed_sec": elapsed,
            "slot": slot if slot is not None else "",
            "slot_delta": (slot - prev_slot) if slot is not None and prev_slot is not None else "",
            "block_height": height if height is not None else "",
            "block_height_delta": (height - prev_height) if height is not None and prev_height is not None else "",
            "transaction_count": tx_count if tx_count is not None else "",
            "transaction_count_delta": (tx_count - prev_tx) if tx_count is not None and prev_tx is not None else "",
            "voting_validators": validators if validators is not None else "",
            "delinquent_validators": delinquent if delinquent is not None else "",
        }
        if slot is not None:
            prev_slot = slot
        if height is not None:
            prev_height = height
        if tx_count is not None:
            prev_tx = tx_count
        append_row(args.output, row)
        if slot is not None or height is not None or tx_count is not None:
            print(
                f"sample={sample} slot={row['slot']} block_height={row['block_height']} "
                f"tx={row['transaction_count']} validators={row['voting_validators']} "
                f"delinquent={row['delinquent_validators']}",
                flush=True,
            )
        else:
            boot = None
            print(f"sample={sample} failed: {'; '.join(errors)}", file=sys.stderr, flush=True)

        if args.samples and sample >= args.samples:
            break

        deadline = time.time() + args.interval
        while not stop_requested and time.time() < deadline:
            time.sleep(min(1.0, deadline - time.time()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
