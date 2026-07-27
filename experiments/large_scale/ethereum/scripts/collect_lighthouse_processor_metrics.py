#!/usr/bin/env python3
"""Collect Beacon Processor Prometheus metrics from paired API cohorts."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import gzip
import json
import math
import re
import signal
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


DEFAULT_WORK_TYPES = (
    "api_request_p0,api_request_p1,gossip_block,gossip_attestation,"
    "gossip_attestation_batch,gossip_aggregate,gossip_aggregate_batch"
)
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SAMPLE_FIELDS = [
    "sample",
    "timestamp",
    "elapsed_sec",
    "phase",
    "role",
    "pair_id",
    "beacon_index",
    "beacon_name",
    "ip",
    "work_type",
    "workers_active",
    "events_received",
    "events_started",
    "events_ignored",
    "send_errors",
    "worker_time_count",
    "worker_time_sum_sec",
    "worker_time_buckets_json",
    "queue_observations",
    "queue_length_sum",
    "queue_length_buckets_json",
    "error",
]


@dataclass(frozen=True)
class MonitoredBeacon:
    role: str
    pair_id: int
    index: int
    name: str
    ip: str
    metrics_url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohorts", type=Path, required=True)
    parser.add_argument("--phase-state", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--samples", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=1.5)
    parser.add_argument("--work-types", default=DEFAULT_WORK_TYPES)
    parser.add_argument(
        "--expected-max-workers",
        type=int,
        default=0,
        help="Fail immediately if the active-worker gauge exceeds this value; 0 disables.",
    )
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="Fail unless every monitored Beacon returns the active-worker metric.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_beacons(path: Path) -> list[MonitoredBeacon]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    beacons = [
        MonitoredBeacon(
            role=row["role"],
            pair_id=int(row["pair_id"]),
            index=int(row["index"]),
            name=row["name"],
            ip=row["ip"],
            metrics_url=row["metrics_url"],
        )
        for row in rows
        if row.get("monitor") == "1"
    ]
    if not beacons:
        raise ValueError(f"no monitored Beacon rows in {path}")
    return sorted(beacons, key=lambda beacon: (beacon.pair_id, beacon.role))


def parse_labels(value: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for match in re.finditer(r'(\w+)="((?:\\.|[^"])*)"', value):
        labels[match.group(1)] = bytes(match.group(2), "utf-8").decode("unicode_escape")
    return labels


def parse_metrics(payload: str) -> dict[tuple[str, str, str], float]:
    metrics: dict[tuple[str, str, str], float] = {}
    for raw_line in payload.splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_:][A-Za-z0-9_:]*)(?:\{([^}]*)\})?\s+([^\s]+)", raw_line)
        if match is None:
            continue
        name, raw_labels, raw_value = match.groups()
        if not name.startswith("beacon_processor_"):
            continue
        try:
            number = float(raw_value)
        except ValueError:
            continue
        labels = parse_labels(raw_labels or "")
        metrics[(name, labels.get("type", ""), labels.get("le", ""))] = number
    return metrics


def fetch_metrics(beacon: MonitoredBeacon, timeout: float) -> tuple[MonitoredBeacon, str, str]:
    try:
        with NO_PROXY_OPENER.open(beacon.metrics_url, timeout=timeout) as response:
            return beacon, response.read().decode("utf-8", errors="replace"), ""
    except Exception as exc:  # noqa: BLE001
        return beacon, "", str(exc)[:300]


def metric_value(
    metrics: dict[tuple[str, str, str], float],
    name: str,
    work_type: str = "",
    le: str = "",
) -> Any:
    value = metrics.get((name, work_type, le))
    if value is None or math.isnan(value):
        return ""
    return value


def histogram_buckets(
    metrics: dict[tuple[str, str, str], float], prefix: str, work_type: str
) -> str:
    buckets = {
        le: value
        for (name, metric_type, le), value in metrics.items()
        if name == f"{prefix}_bucket" and metric_type == work_type
    }
    return json.dumps(buckets, separators=(",", ":"), sort_keys=True) if buckets else ""


def current_phase(path: Optional[Path]) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return str(json.loads(path.read_text()).get("phase", ""))
    except (OSError, json.JSONDecodeError):
        return ""


def write_run_info(
    output_dir: Path,
    status: str,
    samples: int,
    args: argparse.Namespace,
    beacons: list[MonitoredBeacon],
) -> None:
    payload = {
        "status": status,
        "samples": samples,
        "updated_at": utc_now(),
        "interval_sec": args.interval,
        "timeout_sec": args.timeout,
        "expected_max_workers": args.expected_max_workers,
        "monitored_beacons": [beacon.__dict__ for beacon in beacons],
        "work_types": [item.strip() for item in args.work_types.split(",") if item.strip()],
    }
    (output_dir / "processor_metrics_run_info.json").write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    args = parse_args()
    if (
        args.interval <= 0
        or args.timeout <= 0
        or args.duration < 0
        or args.samples < 0
        or args.expected_max_workers < 0
    ):
        raise SystemExit(
            "interval and timeout must be positive; duration and samples must be non-negative"
        )
    beacons = load_beacons(args.cohorts)
    work_types = [item.strip() for item in args.work_types.split(",") if item.strip()]
    if not work_types:
        raise SystemExit("--work-types is empty")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "processor_metrics.csv.gz"
    stop = False

    def stop_handler(signum: int, frame: object) -> None:  # noqa: ARG001
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    started = time.monotonic()
    sample = 0
    successful_beacons: set[int] = set()
    worker_cap_violation = ""
    write_run_info(args.output_dir, "running", sample, args, beacons)

    with gzip.open(output_path, "wt", newline="", compresslevel=1) as handle, concurrent.futures.ThreadPoolExecutor(
        max_workers=len(beacons)
    ) as executor:
        writer = csv.DictWriter(handle, fieldnames=SAMPLE_FIELDS)
        writer.writeheader()
        try:
            while not stop:
                elapsed = time.monotonic() - started
                if args.duration and elapsed >= args.duration:
                    break
                if args.samples and sample >= args.samples:
                    break
                sample += 1
                timestamp = utc_now()
                phase = current_phase(args.phase_state)
                futures = [executor.submit(fetch_metrics, beacon, args.timeout) for beacon in beacons]
                for future in concurrent.futures.as_completed(futures):
                    beacon, payload, error = future.result()
                    metrics = parse_metrics(payload) if payload else {}
                    active = metric_value(metrics, "beacon_processor_workers_active_total")
                    if not error and active != "":
                        successful_beacons.add(beacon.index)
                        if args.expected_max_workers and active > args.expected_max_workers:
                            worker_cap_violation = (
                                f"Beacon {beacon.index} reported {active:g} active workers, "
                                f"exceeding configured cap {args.expected_max_workers}"
                            )
                    for work_type in work_types:
                        writer.writerow(
                            {
                                "sample": sample,
                                "timestamp": timestamp,
                                "elapsed_sec": f"{elapsed:.3f}",
                                "phase": phase,
                                "role": beacon.role,
                                "pair_id": beacon.pair_id,
                                "beacon_index": beacon.index,
                                "beacon_name": beacon.name,
                                "ip": beacon.ip,
                                "work_type": work_type,
                                "workers_active": active,
                                "events_received": metric_value(
                                    metrics,
                                    "beacon_processor_work_events_rx_count",
                                    work_type,
                                ),
                                "events_started": metric_value(
                                    metrics,
                                    "beacon_processor_work_events_started_count",
                                    work_type,
                                ),
                                "events_ignored": metric_value(
                                    metrics,
                                    "beacon_processor_work_events_ignored_count",
                                    work_type,
                                ),
                                "send_errors": metric_value(
                                    metrics,
                                    "beacon_processor_send_error_per_work_type",
                                    work_type,
                                ),
                                "worker_time_count": metric_value(
                                    metrics,
                                    "beacon_processor_worker_time_count",
                                    work_type,
                                ),
                                "worker_time_sum_sec": metric_value(
                                    metrics,
                                    "beacon_processor_worker_time_sum",
                                    work_type,
                                ),
                                "worker_time_buckets_json": histogram_buckets(
                                    metrics, "beacon_processor_worker_time", work_type
                                ),
                                "queue_observations": metric_value(
                                    metrics,
                                    "beacon_processor_work_event_queue_length_count",
                                    work_type,
                                ),
                                "queue_length_sum": metric_value(
                                    metrics,
                                    "beacon_processor_work_event_queue_length_sum",
                                    work_type,
                                ),
                                "queue_length_buckets_json": histogram_buckets(
                                    metrics,
                                    "beacon_processor_work_event_queue_length",
                                    work_type,
                                ),
                                "error": error,
                            }
                        )
                handle.flush()
                write_run_info(args.output_dir, "running", sample, args, beacons)
                if worker_cap_violation:
                    break
                deadline = started + sample * args.interval
                while not stop:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.2, remaining))
        except Exception:
            write_run_info(args.output_dir, "failed", sample, args, beacons)
            raise

    if worker_cap_violation:
        write_run_info(args.output_dir, "failed", sample, args, beacons)
        print(worker_cap_violation)
        return 1
    missing = sorted({beacon.index for beacon in beacons} - successful_beacons)
    if args.require_all and missing:
        write_run_info(args.output_dir, "failed", sample, args, beacons)
        print(f"missing Beacon Processor metrics from Beacon indices: {missing}")
        return 1
    write_run_info(args.output_dir, "complete", sample, args, beacons)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
