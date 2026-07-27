#!/usr/bin/env python3
"""Generate phased, rate-controlled read-only Beacon API load."""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import json
import random
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ETHEREUM_DIR = SCRIPT_DIR.parent
DEFAULT_PROFILE = ETHEREUM_DIR / "configs" / "api_observer_read_v1.json"
DEFAULT_PHASES = (
    "baseline:0:1800,load_1x:0.2:1800,recovery_1x:0:600,"
    "load_5x:1:1800,recovery_5x:0:600,load_10x:2:1800,"
    "recovery_10x:0:600,load_20x:4:1800"
)


@dataclass(frozen=True)
class Target:
    index: int
    name: str
    ip: str


@dataclass(frozen=True)
class RequestSpec:
    name: str
    path: str
    weight: float


@dataclass(frozen=True)
class Phase:
    name: str
    rate_per_node: float
    duration_sec: float


RAW_FIELDS = [
    "timestamp",
    "elapsed_sec",
    "phase",
    "beacon_index",
    "beacon_name",
    "request_name",
    "path",
    "status_code",
    "latency_ms",
    "response_bytes",
    "result",
    "error",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohorts", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--phases", default=DEFAULT_PHASES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--concurrency-per-node", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--queue-factor", type=int, default=4)
    parser.add_argument("--drain-timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=61)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_targets(path: Path) -> list[Target]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    targets = [
        Target(index=int(row["index"]), name=row["name"], ip=row["ip"])
        for row in rows
        if row.get("role") == "target"
    ]
    if not targets:
        raise ValueError(f"no target rows in {path}")
    return sorted(targets, key=lambda target: target.index)


def load_profile(path: Path) -> tuple[str, list[RequestSpec]]:
    payload = json.loads(path.read_text())
    requests = [
        RequestSpec(
            name=str(item["name"]),
            path=str(item["path"]),
            weight=float(item["weight"]),
        )
        for item in payload.get("requests", [])
    ]
    if not requests or any(request.weight <= 0 for request in requests):
        raise ValueError("profile must contain positive-weight requests")
    if any(not request.path.startswith("/") for request in requests):
        raise ValueError("all request paths must start with /")
    return str(payload.get("name", path.stem)), requests


def parse_phases(value: str) -> list[Phase]:
    phases: list[Phase] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(":")
        if len(parts) != 3:
            raise ValueError(f"invalid phase {raw!r}; expected NAME:RATE_PER_NODE:DURATION")
        name, rate, duration = parts
        phase = Phase(name=name, rate_per_node=float(rate), duration_sec=float(duration))
        if not name or phase.rate_per_node < 0 or phase.duration_sec <= 0:
            raise ValueError(f"invalid phase values: {raw!r}")
        phases.append(phase)
    if not phases:
        raise ValueError("phase schedule is empty")
    return phases


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


async def http_get(target: Target, port: int, path: str, timeout: float) -> tuple[int, int]:
    async def request() -> tuple[int, int]:
        reader, writer = await asyncio.open_connection(target.ip, port)
        try:
            wire = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {target.ip}:{port}\r\n"
                "Accept: application/json\r\n"
                "Connection: close\r\n\r\n"
            )
            writer.write(wire.encode("ascii"))
            await writer.drain()
            header = await reader.readuntil(b"\r\n\r\n")
            status_line = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            parts = status_line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                raise RuntimeError(f"invalid HTTP status line: {status_line[:100]}")
            response_bytes = len(header)
            while True:
                chunk = await reader.read(64 * 1024)
                if not chunk:
                    break
                response_bytes += len(chunk)
            return int(parts[1]), response_bytes
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    return await asyncio.wait_for(request(), timeout=timeout)


async def run(args: argparse.Namespace) -> int:
    if not 1 <= args.port <= 65535:
        raise ValueError("--port must be between 1 and 65535")
    if args.concurrency_per_node < 1 or args.queue_factor < 1:
        raise ValueError("concurrency and queue factor must be positive")
    if args.timeout <= 0 or args.drain_timeout < 0:
        raise ValueError("timeouts must be positive")

    targets = load_targets(args.cohorts)
    profile_name, requests = load_profile(args.profile)
    phases = parse_phases(args.phases)
    total_duration = sum(phase.duration_sec for phase in phases)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    raw_path = args.output_dir / "api_requests.csv.gz"
    phase_path = args.output_dir / "api_phases.csv"
    state_path = args.output_dir / "api_phase_state.json"
    run_info_path = args.output_dir / "api_load_run_info.json"
    started_monotonic = time.monotonic()
    started_at = utc_now()
    stopping = asyncio.Event()
    queues = {
        target.index: asyncio.Queue(maxsize=args.concurrency_per_node * args.queue_factor)
        for target in targets
    }
    counters = {"scheduled": 0, "completed": 0, "success": 0, "failed": 0, "client_dropped": 0}

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopping.set)
        except NotImplementedError:
            pass

    with gzip.open(raw_path, "wt", newline="", compresslevel=1) as raw_handle, phase_path.open(
        "w", newline=""
    ) as phase_handle:
        raw_writer = csv.DictWriter(raw_handle, fieldnames=RAW_FIELDS)
        raw_writer.writeheader()
        phase_fields = [
            "phase",
            "rate_per_node",
            "target_count",
            "total_rate",
            "duration_sec",
            "start_timestamp",
            "start_elapsed_sec",
            "end_timestamp",
            "end_elapsed_sec",
        ]
        phase_writer = csv.DictWriter(phase_handle, fieldnames=phase_fields)
        phase_writer.writeheader()

        def record(
            *,
            phase: str,
            target: Target,
            request: RequestSpec,
            status_code: int | str,
            latency_ms: float,
            response_bytes: int,
            result: str,
            error: str,
        ) -> None:
            raw_writer.writerow(
                {
                    "timestamp": utc_now(),
                    "elapsed_sec": f"{time.monotonic() - started_monotonic:.3f}",
                    "phase": phase,
                    "beacon_index": target.index,
                    "beacon_name": target.name,
                    "request_name": request.name,
                    "path": request.path,
                    "status_code": status_code,
                    "latency_ms": f"{latency_ms:.3f}",
                    "response_bytes": response_bytes,
                    "result": result,
                    "error": error[:300],
                }
            )

        async def worker(target: Target) -> None:
            queue = queues[target.index]
            while True:
                item = await queue.get()
                if item is None:
                    queue.task_done()
                    return
                phase_name, request = item
                request_started = time.monotonic()
                status_code: int | str = ""
                response_bytes = 0
                result = "error"
                error = ""
                try:
                    status_code, response_bytes = await http_get(
                        target, args.port, request.path, args.timeout
                    )
                    result = "success" if 200 <= status_code < 300 else "http_error"
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                latency_ms = (time.monotonic() - request_started) * 1000.0
                counters["completed"] += 1
                counters["success" if result == "success" else "failed"] += 1
                record(
                    phase=phase_name,
                    target=target,
                    request=request,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    response_bytes=response_bytes,
                    result=result,
                    error=error,
                )
                queue.task_done()

        workers = [
            asyncio.create_task(worker(target))
            for target in targets
            for _ in range(args.concurrency_per_node)
        ]

        async def produce(target: Target, phase: Phase, deadline: float) -> None:
            rng = random.Random(args.seed + target.index)
            next_request = time.monotonic()
            queue = queues[target.index]
            population = list(range(len(requests)))
            weights = [request.weight for request in requests]
            interval = 1.0 / phase.rate_per_node
            while not stopping.is_set() and time.monotonic() < deadline:
                now = time.monotonic()
                if now < next_request:
                    await asyncio.sleep(min(next_request - now, 0.1))
                    continue
                request = requests[rng.choices(population, weights=weights, k=1)[0]]
                counters["scheduled"] += 1
                try:
                    queue.put_nowait((phase.name, request))
                except asyncio.QueueFull:
                    counters["client_dropped"] += 1
                    record(
                        phase=phase.name,
                        target=target,
                        request=request,
                        status_code="",
                        latency_ms=0.0,
                        response_bytes=0,
                        result="client_queue_full",
                        error="request was not sent because the per-node client queue was full",
                    )
                next_request += interval
                if next_request < now - interval:
                    next_request = now + interval

        write_json_atomic(
            run_info_path,
            {
                "status": "running",
                "started_at": started_at,
                "profile": profile_name,
                "profile_path": str(args.profile.resolve()),
                "target_count": len(targets),
                "concurrency_per_node": args.concurrency_per_node,
                "timeout_sec": args.timeout,
                "total_scheduled_duration_sec": total_duration,
                "phases": [phase.__dict__ for phase in phases],
                "counters": counters,
            },
        )

        for phase in phases:
            if stopping.is_set():
                break
            phase_started = time.monotonic()
            phase_start_timestamp = utc_now()
            phase_start_elapsed = phase_started - started_monotonic
            write_json_atomic(
                state_path,
                {
                    "phase": phase.name,
                    "rate_per_node": phase.rate_per_node,
                    "target_count": len(targets),
                    "total_rate": phase.rate_per_node * len(targets),
                    "started_at": phase_start_timestamp,
                    "start_elapsed_sec": phase_start_elapsed,
                },
            )
            deadline = phase_started + phase.duration_sec
            producers = []
            if phase.rate_per_node > 0:
                producers = [
                    asyncio.create_task(produce(target, phase, deadline)) for target in targets
                ]
            while not stopping.is_set() and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.5, remaining))
            if producers:
                await asyncio.gather(*producers, return_exceptions=True)

            phase_writer.writerow(
                {
                    "phase": phase.name,
                    "rate_per_node": phase.rate_per_node,
                    "target_count": len(targets),
                    "total_rate": phase.rate_per_node * len(targets),
                    "duration_sec": phase.duration_sec,
                    "start_timestamp": phase_start_timestamp,
                    "start_elapsed_sec": f"{phase_start_elapsed:.3f}",
                    "end_timestamp": utc_now(),
                    "end_elapsed_sec": f"{time.monotonic() - started_monotonic:.3f}",
                }
            )
            phase_handle.flush()
            raw_handle.flush()
            write_json_atomic(
                run_info_path,
                {
                    "status": "running",
                    "started_at": started_at,
                    "profile": profile_name,
                    "target_count": len(targets),
                    "current_phase": phase.name,
                    "counters": counters,
                },
            )

        if args.drain_timeout > 0:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(queue.join() for queue in queues.values())),
                    timeout=args.drain_timeout,
                )
            except asyncio.TimeoutError:
                pass

        for queue in queues.values():
            for _ in range(args.concurrency_per_node):
                await queue.put(None)
        await asyncio.gather(*workers, return_exceptions=True)

    finished_at = utc_now()
    write_json_atomic(
        state_path,
        {
            "phase": "complete",
            "rate_per_node": 0,
            "target_count": len(targets),
            "total_rate": 0,
            "finished_at": finished_at,
        },
    )
    write_json_atomic(
        run_info_path,
        {
            "status": "stopped" if stopping.is_set() else "complete",
            "started_at": started_at,
            "finished_at": finished_at,
            "profile": profile_name,
            "profile_path": str(args.profile.resolve()),
            "target_count": len(targets),
            "concurrency_per_node": args.concurrency_per_node,
            "timeout_sec": args.timeout,
            "total_scheduled_duration_sec": total_duration,
            "phases": [phase.__dict__ for phase in phases],
            "counters": counters,
        },
    )
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
