#!/usr/bin/env python3
"""Collect live Solana validator process counts during large experiments."""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path


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


def active_agave_count_process_scan(timeout: int) -> tuple[int, int, str]:
    output = scan_proc_cmdlines(timeout)
    active = 0
    for args in output.splitlines():
        if "agave-validator" not in args:
            continue
        if "--identity /opt/solana/config" not in args:
            continue
        active += 1
    return active, active, ""


def scan_proc_cmdlines(timeout: int) -> str:
    """Read /proc directly; spawning ps can time out on overloaded hosts."""
    deadline = time.monotonic() + max(1, timeout)
    lines: list[str] = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        if time.monotonic() > deadline:
            raise TimeoutError(f"/proc cmdline scan timed out after {timeout} seconds")
        try:
            with open(f"/proc/{entry.name}/cmdline", "rb") as handle:
                data = handle.read()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if not data:
            continue
        text = data.replace(b"\0", b" ").decode("utf-8", errors="ignore").strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def solana_container_names(project_name: str, timeout: int) -> list[str]:
    command = ["docker", "ps", "--format", "{{.Names}}"]
    if project_name:
        command[2:2] = ["--filter", f"label=com.docker.compose.project={project_name}"]
    output = run_text(command, timeout)
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip() and ("Solana-Bootstrap" in line or "Solana-Validator" in line)
    ]


def active_agave_count_docker_top(project_name: str, timeout: int) -> tuple[int, int, str]:
    names = solana_container_names(project_name, timeout)
    active = 0
    missing: list[str] = []
    per_container_timeout = max(5, min(timeout, 20))
    for name in names:
        try:
            output = run_text(
                ["timeout", str(per_container_timeout), "docker", "top", name, "-eo", "pid,ppid,stat,comm,args"],
                per_container_timeout + 5,
            )
        except subprocess.SubprocessError as exc:
            missing.append(f"{name}:docker_top_failed:{exc}")
            continue
        if "agave-validator" in output:
            active += 1
        else:
            missing.append(f"{name}:no_agave")
    return len(names), active, ";".join(missing[:50])


def active_agave_count_running(project_name: str, timeout: int) -> tuple[int, int, str]:
    running = len(solana_container_names(project_name, timeout))
    return running, running, ""


def sample(
    expected: int,
    min_active: int,
    timeout: int,
    mode: str,
    project_name: str,
    confirm_retries: int,
    confirm_delay: float,
) -> dict[str, str | int]:
    row = sample_once(expected, min_active, timeout, mode, project_name)
    if row["status"] == "ok" or confirm_retries <= 0:
        return row

    observations = [
        f"{row['status']}:{row['running_solana']}/{row['active_agave']}:{row.get('error', '')}"
    ]
    best = row
    for _ in range(confirm_retries):
        time.sleep(confirm_delay)
        retry = sample_once(expected, min_active, timeout, mode, project_name)
        observations.append(
            f"{retry['status']}:{retry['running_solana']}/{retry['active_agave']}:{retry.get('error', '')}"
        )
        if int(retry["running_solana"]) > int(best["running_solana"]) or int(retry["active_agave"]) > int(best["active_agave"]):
            best = retry
        if retry["status"] == "ok":
            retry["error"] = f"confirmed after retry; observations={' | '.join(observations)}"
            return retry

    best["error"] = f"{best.get('error', '')}; confirmed_short observations={' | '.join(observations)}".strip("; ")
    return best


def sample_once(
    expected: int,
    min_active: int,
    timeout: int,
    mode: str,
    project_name: str,
) -> dict[str, str | int]:
    try:
        if mode == "process-scan":
            running, active, detail = active_agave_count_process_scan(timeout)
        elif mode == "docker-top":
            running, active, detail = active_agave_count_docker_top(project_name, timeout)
        elif mode == "running":
            running, active, detail = active_agave_count_running(project_name, timeout)
        else:
            raise ValueError(f"unsupported active count mode: {mode}")
        status = "ok"
        if running < expected:
            status = "running_short"
        elif active < min_active:
            status = "active_short"
        return {
            "running_solana": running,
            "active_agave": active,
            "expected_validators": expected,
            "min_active_solana": min_active,
            "status": status,
            "error": detail if status != "ok" else "",
        }
    except Exception as exc:  # noqa: BLE001 - this is a best-effort collector.
        return {
            "running_solana": 0,
            "active_agave": 0,
            "expected_validators": expected,
            "min_active_solana": min_active,
            "status": "error",
            "error": str(exc),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-validators", type=int, required=True)
    parser.add_argument("--min-active", type=int, required=True)
    parser.add_argument("--docker-timeout", type=int, default=120)
    parser.add_argument("--mode", choices=["process-scan", "docker-top", "running"], default="process-scan")
    parser.add_argument("--compose-project", default="")
    parser.add_argument("--confirm-retries", type=int, default=2)
    parser.add_argument("--confirm-delay", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "sample",
        "timestamp",
        "elapsed_sec",
        "running_solana",
        "active_agave",
        "expected_validators",
        "min_active_solana",
        "status",
        "error",
    ]

    start = time.monotonic()
    index = 0
    with args.output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        while True:
            row = sample(
                args.expected_validators,
                args.min_active,
                args.docker_timeout,
                args.mode,
                args.compose_project,
                args.confirm_retries,
                args.confirm_delay,
            )
            row.update(
                {
                    "sample": index,
                    "timestamp": now_iso(),
                    "elapsed_sec": round(time.monotonic() - start, 3),
                }
            )
            writer.writerow(row)
            handle.flush()
            if args.once:
                return 0
            index += 1
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
