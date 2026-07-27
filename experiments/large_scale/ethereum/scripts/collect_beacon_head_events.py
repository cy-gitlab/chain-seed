#!/usr/bin/env python3
"""Collect high-resolution Beacon head events over persistent SSE connections."""

from __future__ import annotations

import argparse
import csv
import json
import queue
import signal
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from collect_consensus_benchmark import (
    BeaconNode,
    choose_indices,
    choose_sample,
    discover_beacons,
)


SCRIPT_DIR = Path(__file__).resolve().parent
ETHEREUM_DIR = SCRIPT_DIR.parent
DEFAULT_COMPOSE_FILE = ETHEREUM_DIR / "output" / "docker-compose.yml"
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

EVENT_FIELDS = [
    "observed_at",
    "observed_unix_ns",
    "observed_monotonic_ns",
    "elapsed_sec",
    "beacon_index",
    "beacon_name",
    "asn",
    "ip",
    "event",
    "slot",
    "block_root",
    "execution_optimistic",
]

CONNECTION_FIELDS = [
    "timestamp",
    "elapsed_sec",
    "beacon_index",
    "beacon_name",
    "ip",
    "attempt",
    "state",
    "error",
]

SAMPLED_FIELDS = ["index", "service", "name", "ip", "asn", "url"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--beacon-sample", type=int, default=64)
    parser.add_argument("--beacon-indices-file", type=Path, default=None)
    parser.add_argument("--topic", default="head")
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--read-timeout", type=float, default=45.0)
    parser.add_argument("--reconnect-delay", type=float, default=1.0)
    parser.add_argument("--min-ready-ratio", type=float, default=0.95)
    return parser.parse_args()


def utc_from_ns(unix_ns: int) -> str:
    return datetime.fromtimestamp(unix_ns / 1_000_000_000, timezone.utc).isoformat(
        timespec="microseconds"
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def write_sampled_beacons(path: Path, beacons: list[BeaconNode]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SAMPLED_FIELDS)
        writer.writeheader()
        for beacon in beacons:
            writer.writerow(
                {
                    "index": beacon.index,
                    "service": beacon.service,
                    "name": beacon.name,
                    "ip": beacon.ip,
                    "asn": beacon.asn,
                    "url": beacon.url,
                }
            )


def normalize_head_event(event_name: str, raw_data: str) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    slot = payload.get("slot", "")
    block_root = payload.get("block", payload.get("block_root", ""))
    if slot == "" or block_root == "":
        return None
    return {
        "event": event_name or "head",
        "slot": slot,
        "block_root": block_root,
        "execution_optimistic": payload.get("execution_optimistic", ""),
    }


def main() -> int:
    args = parse_args()
    if args.beacon_sample < 1:
        raise SystemExit("--beacon-sample must be positive")
    if args.duration < 0:
        raise SystemExit("--duration must be non-negative")
    if args.startup_timeout <= 0 or args.read_timeout <= 0 or args.reconnect_delay <= 0:
        raise SystemExit("timeouts and reconnect delay must be positive")
    if not 0 < args.min_ready_ratio <= 1:
        raise SystemExit("--min-ready-ratio must be in (0, 1]")
    if not args.topic:
        raise SystemExit("--topic must not be empty")

    discovered = discover_beacons(args.compose_file)
    if not discovered:
        raise SystemExit(f"no Beacon nodes found in {args.compose_file}")
    if args.beacon_indices_file is not None:
        beacons = choose_indices(discovered, args.beacon_indices_file)
    else:
        beacons = choose_sample(discovered, args.beacon_sample)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    events_path = args.output_dir / "head_events.csv"
    connections_path = args.output_dir / "connections.csv"
    run_info_path = args.output_dir / "run_info.json"
    if events_path.exists() or connections_path.exists():
        raise SystemExit(f"refusing to overwrite existing event data in {args.output_dir}")
    write_sampled_beacons(args.output_dir / "sampled_beacons.csv", beacons)

    started_monotonic_ns = time.monotonic_ns()
    stop_event = threading.Event()
    records: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
    state_lock = threading.Lock()
    connected: set[int] = set()
    connected_once: set[int] = set()
    counters = {
        "events": 0,
        "connection_attempts": 0,
        "disconnects": 0,
        "connection_errors": 0,
        "parse_errors": 0,
    }

    def elapsed_now() -> float:
        return (time.monotonic_ns() - started_monotonic_ns) / 1_000_000_000

    def connection_record(
        beacon: BeaconNode, attempt: int, state: str, error: str = ""
    ) -> None:
        records.put(
            (
                "connection",
                {
                    "timestamp": utc_now(),
                    "elapsed_sec": f"{elapsed_now():.6f}",
                    "beacon_index": beacon.index,
                    "beacon_name": beacon.name,
                    "ip": beacon.ip,
                    "attempt": attempt,
                    "state": state,
                    "error": error[:500],
                },
            )
        )

    def handle_event(beacon: BeaconNode, event_name: str, raw_data: str) -> None:
        normalized = normalize_head_event(event_name, raw_data)
        if normalized is None:
            with state_lock:
                counters["parse_errors"] += 1
            return
        observed_unix_ns = time.time_ns()
        observed_monotonic_ns = time.monotonic_ns()
        records.put(
            (
                "event",
                {
                    "observed_at": utc_from_ns(observed_unix_ns),
                    "observed_unix_ns": observed_unix_ns,
                    "observed_monotonic_ns": observed_monotonic_ns,
                    "elapsed_sec": (
                        f"{(observed_monotonic_ns - started_monotonic_ns) / 1_000_000_000:.9f}"
                    ),
                    "beacon_index": beacon.index,
                    "beacon_name": beacon.name,
                    "asn": beacon.asn,
                    "ip": beacon.ip,
                    **normalized,
                },
            )
        )
        with state_lock:
            counters["events"] += 1

    def consume_stream(beacon: BeaconNode) -> None:
        attempt = 0
        query = urllib.parse.urlencode({"topics": args.topic})
        endpoint = f"{beacon.url}/eth/v1/events?{query}"
        while not stop_event.is_set():
            attempt += 1
            with state_lock:
                counters["connection_attempts"] += 1
            request = urllib.request.Request(
                endpoint,
                headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
            )
            was_connected = False
            try:
                with NO_PROXY_OPENER.open(request, timeout=args.read_timeout) as response:
                    was_connected = True
                    with state_lock:
                        connected.add(beacon.index)
                        connected_once.add(beacon.index)
                    connection_record(beacon, attempt, "connected")
                    event_name = ""
                    data_lines: list[str] = []
                    for raw_line in response:
                        if stop_event.is_set():
                            break
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                        if not line:
                            if data_lines:
                                handle_event(beacon, event_name, "\n".join(data_lines))
                            event_name = ""
                            data_lines = []
                        elif line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                    if data_lines and not stop_event.is_set():
                        handle_event(beacon, event_name, "\n".join(data_lines))
            except Exception as exc:  # noqa: BLE001
                if not stop_event.is_set():
                    with state_lock:
                        counters["connection_errors"] += 1
                    connection_record(beacon, attempt, "error", str(exc))
            finally:
                if was_connected:
                    with state_lock:
                        connected.discard(beacon.index)
                        counters["disconnects"] += 1
                    connection_record(beacon, attempt, "disconnected")
            if not stop_event.wait(args.reconnect_delay):
                continue

    def stop_handler(signum: int, frame: object) -> None:  # noqa: ARG001
        stop_event.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    threads = [
        threading.Thread(
            target=consume_stream,
            args=(beacon,),
            name=f"beacon-events-{beacon.index}",
            daemon=True,
        )
        for beacon in beacons
    ]
    for thread in threads:
        thread.start()

    required_ready = max(1, int(len(beacons) * args.min_ready_ratio + 0.999999))
    startup_deadline = time.monotonic() + args.startup_timeout
    startup_ready = False
    failure = ""
    last_info_write = 0.0
    last_flush = 0.0

    def run_info(status: str) -> dict[str, Any]:
        with state_lock:
            snapshot = dict(counters)
            connected_count = len(connected)
            connected_once_count = len(connected_once)
        return {
            "status": status,
            "startup_ready": startup_ready,
            "failure": failure,
            "updated_at": utc_now(),
            "elapsed_sec": round(elapsed_now(), 6),
            "topic": args.topic,
            "beacons_discovered": len(discovered),
            "beacons_sampled": len(beacons),
            "required_ready": required_ready,
            "connected_now": connected_count,
            "connected_once": connected_once_count,
            "min_ready_ratio": args.min_ready_ratio,
            "startup_timeout_sec": args.startup_timeout,
            "read_timeout_sec": args.read_timeout,
            "reconnect_delay_sec": args.reconnect_delay,
            **snapshot,
        }

    with events_path.open("w", newline="") as event_handle, connections_path.open(
        "w", newline=""
    ) as connection_handle:
        event_writer = csv.DictWriter(event_handle, fieldnames=EVENT_FIELDS)
        connection_writer = csv.DictWriter(connection_handle, fieldnames=CONNECTION_FIELDS)
        event_writer.writeheader()
        connection_writer.writeheader()
        atomic_write_json(run_info_path, run_info("starting"))

        while not stop_event.is_set():
            now = time.monotonic()
            if args.duration and elapsed_now() >= args.duration:
                stop_event.set()
                break

            try:
                kind, record = records.get(timeout=0.25)
                if kind == "event":
                    event_writer.writerow(record)
                else:
                    connection_writer.writerow(record)
            except queue.Empty:
                pass

            with state_lock:
                connected_count = len(connected)
            if not startup_ready and connected_count >= required_ready:
                startup_ready = True
                atomic_write_json(run_info_path, run_info("running"))
            elif not startup_ready and now >= startup_deadline:
                failure = (
                    f"only {connected_count}/{len(beacons)} event streams connected; "
                    f"required {required_ready}"
                )
                stop_event.set()
                break

            if now - last_flush >= 2.0:
                event_handle.flush()
                connection_handle.flush()
                last_flush = now
            if now - last_info_write >= 15.0:
                atomic_write_json(
                    run_info_path, run_info("running" if startup_ready else "starting")
                )
                last_info_write = now

        for thread in threads:
            thread.join(timeout=args.read_timeout + args.reconnect_delay + 1.0)
        while True:
            try:
                kind, record = records.get_nowait()
            except queue.Empty:
                break
            if kind == "event":
                event_writer.writerow(record)
            else:
                connection_writer.writerow(record)

    final_status = "failed" if failure else "complete"
    atomic_write_json(run_info_path, run_info(final_status))
    if failure:
        print(failure)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
