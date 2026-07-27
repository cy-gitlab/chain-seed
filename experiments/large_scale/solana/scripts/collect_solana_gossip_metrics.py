#!/usr/bin/env python3
"""Collect Solana gossip-node counts from the bootstrap container."""

from __future__ import annotations

import argparse
import csv
import os
import re
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path


FIELDS = ["sample", "timestamp", "elapsed_sec", "gossip_nodes", "status", "error"]
GOSSIP_NODE_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+\s+\|")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_text(command: list[str], timeout: int) -> str:
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    return completed.stdout


def find_boot_container(pattern: str, timeout: int) -> str:
    explicit = os.environ.get("BOOT_CONTAINER_NAME", "").strip()
    if explicit:
        return explicit
    output = run_text(["docker", "ps", "--format", "{{.Names}}"], timeout)
    for name in output.splitlines():
        if pattern in name:
            return name
    raise RuntimeError(f"bootstrap container not found: {pattern}")


def gossip_node_count(boot: str, rpc_url: str, timeout: int) -> int:
    output = run_text(
        ["docker", "exec", boot, "solana", "--url", rpc_url, "gossip"],
        timeout,
    )
    return sum(1 for line in output.splitlines() if GOSSIP_NODE_RE.match(line))


def append_row(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-pattern", default="Solana-Bootstrap-000")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8899")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-timeout", type=int, default=120)
    parser.add_argument("--rpc-timeout", type=int, default=120)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.interval < 1 or args.docker_timeout < 1 or args.rpc_timeout < 1:
        raise SystemExit("interval and timeouts must be positive")

    stop_requested = False

    def handle_stop(signum, frame):  # type: ignore[no-untyped-def]
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    boot = ""
    started = time.monotonic()
    sample = 0

    while not stop_requested:
        sample += 1
        row: dict[str, object] = {
            "sample": sample,
            "timestamp": now_iso(),
            "elapsed_sec": round(time.monotonic() - started, 3),
            "gossip_nodes": "",
            "status": "ok",
            "error": "",
        }
        try:
            if not boot:
                boot = find_boot_container(args.boot_pattern, args.docker_timeout)
            row["gossip_nodes"] = gossip_node_count(boot, args.rpc_url, args.rpc_timeout)
        except Exception as exc:  # noqa: BLE001 - best-effort collector.
            boot = ""
            row["status"] = "error"
            row["error"] = str(exc)
        append_row(args.output, row)
        print(
            f"sample={sample} gossip_nodes={row['gossip_nodes']} status={row['status']}",
            flush=True,
        )

        if args.once:
            break
        deadline = time.monotonic() + args.interval
        while not stop_requested and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
