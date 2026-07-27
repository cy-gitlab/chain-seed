#!/usr/bin/env python3
"""Derive per-phase API, processor, and cohort summaries from raw experiment data."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    return parser.parse_args()


def percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def fmt(value: Optional[float], digits: int = 3) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_csv(path: Path) -> Iterable[dict[str, str]]:
    with path.open(newline="") as handle:
        yield from csv.DictReader(handle)


def read_gzip_csv(path: Path) -> Iterable[dict[str, str]]:
    with gzip.open(path, "rt", newline="") as handle:
        yield from csv.DictReader(handle)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_phases(data_dir: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    rows = list(read_csv(data_dir / "api-load" / "api_phases.csv"))
    phases: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        name = row["phase"]
        order.append(name)
        phases[name] = {
            **row,
            "start": datetime.fromisoformat(row["start_timestamp"]),
            "end": datetime.fromisoformat(row["end_timestamp"]),
            "duration": float(row["end_elapsed_sec"]) - float(row["start_elapsed_sec"]),
        }
    return order, phases


def experiment_worker_cap(data_dir: Path) -> int:
    env_path = data_dir / "experiment.env"
    if not env_path.exists():
        return 0
    for line in env_path.read_text().splitlines():
        if line.startswith("lighthouse_processor_workers_cap="):
            return int(line.split("=", 1)[1])
    return 0


def summarize_api(data_dir: Path, order: list[str], phases: dict[str, dict[str, Any]]) -> None:
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "scheduled": 0,
            "completed": 0,
            "success": 0,
            "http_error": 0,
            "client_queue_full": 0,
            "transport_error": 0,
            "latencies": [],
            "bytes": 0,
        }
    )
    for row in read_gzip_csv(data_dir / "api-load" / "api_requests.csv.gz"):
        phase = row["phase"]
        item = stats[phase]
        item["scheduled"] += 1
        result = row["result"]
        if result == "client_queue_full":
            item["client_queue_full"] += 1
            continue
        item["completed"] += 1
        item["success" if result == "success" else "transport_error"] += 1
        if result == "http_error":
            item["transport_error"] -= 1
            item["http_error"] += 1
        latency = number(row["latency_ms"])
        if latency is not None:
            item["latencies"].append(latency)
        response_bytes = number(row["response_bytes"])
        if response_bytes is not None:
            item["bytes"] += int(response_bytes)

    rows: list[dict[str, Any]] = []
    for phase in order:
        item = stats[phase]
        duration = phases[phase]["duration"]
        scheduled = item["scheduled"]
        completed = item["completed"]
        rows.append(
            {
                "phase": phase,
                "rate_per_node": phases[phase]["rate_per_node"],
                "target_count": phases[phase]["target_count"],
                "configured_total_rate": phases[phase]["total_rate"],
                "observed_duration_sec": f"{duration:.3f}",
                "scheduled": scheduled,
                "completed": completed,
                "success": item["success"],
                "http_error": item["http_error"],
                "transport_error": item["transport_error"],
                "client_queue_full": item["client_queue_full"],
                "send_success_pct": fmt(100.0 * item["success"] / scheduled if scheduled else None),
                "completed_rps": fmt(completed / duration if duration > 0 else None),
                "latency_p50_ms": fmt(percentile(item["latencies"], 50)),
                "latency_p95_ms": fmt(percentile(item["latencies"], 95)),
                "latency_p99_ms": fmt(percentile(item["latencies"], 99)),
                "response_mib": fmt(item["bytes"] / (1024 * 1024)),
            }
        )
    write_csv(data_dir / "api_phase_summary.csv", rows)


def cumulative_delta(first: Optional[float], last: Optional[float]) -> float:
    if first is None or last is None:
        return 0.0
    return max(0.0, last - first)


def bucket_quantile(buckets: dict[str, float], count: float, quantile: float) -> Optional[float]:
    if count <= 0 or not buckets:
        return None
    finite = sorted(
        (float(le), value)
        for le, value in buckets.items()
        if le not in {"+Inf", "Inf", "inf"}
    )
    threshold = count * quantile
    for upper, cumulative in finite:
        if cumulative >= threshold:
            return upper
    return finite[-1][0] if finite else None


def summarize_processor(
    data_dir: Path,
    order: list[str],
    phases: dict[str, dict[str, Any]],
    worker_cap: int,
) -> None:
    active: dict[tuple[str, str], list[float]] = defaultdict(list)
    snapshots: dict[tuple[str, str, str, str], dict[str, tuple[Optional[float], Optional[float]]]] = {}
    bucket_snapshots: dict[
        tuple[str, str, str, str], dict[str, dict[str, tuple[float, float]]]
    ] = {}
    errors: dict[tuple[str, str], int] = defaultdict(int)
    valid_phases = set(order)

    for row in read_gzip_csv(data_dir / "processor-metrics" / "processor_metrics.csv.gz"):
        phase = row["phase"]
        if phase not in valid_phases:
            continue
        role = row["role"]
        work_type = row["work_type"]
        node = row["beacon_index"]
        if row["error"]:
            errors[(phase, role)] += 1
        if work_type == "api_request_p0":
            value = number(row["workers_active"])
            if value is not None:
                active[(phase, role)].append(value)

        key = (phase, role, work_type, node)
        state = snapshots.setdefault(key, {})
        for field in (
            "events_received",
            "events_started",
            "events_ignored",
            "send_errors",
            "worker_time_count",
            "worker_time_sum_sec",
            "queue_observations",
            "queue_length_sum",
        ):
            value = number(row[field])
            if value is None:
                continue
            first, _last = state.get(field, (value, value))
            state[field] = (first, value)

        bucket_state = bucket_snapshots.setdefault(key, {"worker": {}, "queue": {}})
        for bucket_name, field in (
            ("worker", "worker_time_buckets_json"),
            ("queue", "queue_length_buckets_json"),
        ):
            if not row[field]:
                continue
            for le, raw_value in json.loads(row[field]).items():
                value = float(raw_value)
                first, _last = bucket_state[bucket_name].get(le, (value, value))
                bucket_state[bucket_name][le] = (first, value)

    grouped: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"deltas": defaultdict(float), "worker_buckets": defaultdict(float), "queue_buckets": defaultdict(float)}
    )
    for (phase, role, work_type, node), state in snapshots.items():  # noqa: ARG001
        item = grouped[(phase, role, work_type)]
        for field, (first, last) in state.items():
            item["deltas"][field] += cumulative_delta(first, last)
        buckets = bucket_snapshots.get((phase, role, work_type, node), {})
        for le, (first, last) in buckets.get("worker", {}).items():
            item["worker_buckets"][le] += cumulative_delta(first, last)
        for le, (first, last) in buckets.get("queue", {}).items():
            item["queue_buckets"][le] += cumulative_delta(first, last)

    rows: list[dict[str, Any]] = []
    for phase in order:
        work_types = sorted({key[2] for key in grouped if key[0] == phase})
        for role in ("target", "control"):
            active_values = active[(phase, role)]
            saturation = (
                100.0 * sum(value >= worker_cap for value in active_values) / len(active_values)
                if active_values and worker_cap
                else None
            )
            for work_type in work_types:
                item = grouped.get((phase, role, work_type), {})
                deltas = item.get("deltas", {})
                worker_count = deltas.get("worker_time_count", 0.0)
                queue_count = deltas.get("queue_observations", 0.0)
                rows.append(
                    {
                        "worker_cap": worker_cap,
                        "phase": phase,
                        "rate_per_node": phases[phase]["rate_per_node"],
                        "role": role,
                        "work_type": work_type,
                        "workers_active_p95": fmt(percentile(active_values, 95)),
                        "worker_saturation_pct": fmt(saturation),
                        "scrape_errors": errors[(phase, role)],
                        "events_received": fmt(deltas.get("events_received", 0.0), 0),
                        "events_started": fmt(deltas.get("events_started", 0.0), 0),
                        "events_ignored": fmt(deltas.get("events_ignored", 0.0), 0),
                        "send_errors": fmt(deltas.get("send_errors", 0.0), 0),
                        "worker_tasks": fmt(worker_count, 0),
                        "worker_time_avg_ms": fmt(
                            1000.0 * deltas.get("worker_time_sum_sec", 0.0) / worker_count
                            if worker_count
                            else None
                        ),
                        "worker_time_p95_upper_sec": fmt(
                            bucket_quantile(item.get("worker_buckets", {}), worker_count, 0.95)
                        ),
                        "queue_mean_length": fmt(
                            deltas.get("queue_length_sum", 0.0) / queue_count
                            if queue_count
                            else None
                        ),
                        "queue_p95_upper": fmt(
                            bucket_quantile(item.get("queue_buckets", {}), queue_count, 0.95)
                        ),
                    }
                )
    write_csv(data_dir / "processor_phase_summary.csv", rows)


def locate_phase(timestamp: datetime, order: list[str], phases: dict[str, dict[str, Any]]) -> str:
    for phase in order:
        if phases[phase]["start"] <= timestamp <= phases[phase]["end"]:
            return phase
    return ""


def summarize_cohorts(data_dir: Path, order: list[str], phases: dict[str, dict[str, Any]]) -> None:
    roles: dict[str, str] = {}
    for row in read_csv(data_dir / "api-experiment" / "api_cohorts.csv"):
        roles[row["index"]] = row["role"]

    samples: dict[tuple[str, str, str], list[tuple[int, bool]]] = defaultdict(list)
    for row in read_csv(data_dir / "consensus" / "beacon_head_samples.csv"):
        phase = locate_phase(datetime.fromisoformat(row["timestamp"]), order, phases)
        role = roles.get(row["beacon_index"], "")
        if not phase or not role:
            continue
        slot = int(row["head_slot"]) if row["head_slot"].isdigit() else -1
        samples[(phase, role, row["sample"])].append((slot, bool(row["error"])))

    phase_roles: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"slots": [], "errors": 0, "requests": 0}
    )
    sample_max: dict[tuple[str, str], int] = {}
    for (phase, role, sample), values in samples.items():
        observed = [slot for slot, error in values if slot >= 0 and not error]
        if observed:
            sample_max[(phase, sample)] = max(sample_max.get((phase, sample), -1), max(observed))
    for (phase, role, sample), values in samples.items():
        item = phase_roles[(phase, role)]
        maximum = sample_max.get((phase, sample), -1)
        for slot, error in values:
            item["requests"] += 1
            if error or slot < 0:
                item["errors"] += 1
            elif maximum >= 0:
                item["slots"].append(float(maximum - slot))

    rows: list[dict[str, Any]] = []
    for phase in order:
        for role in ("target", "control"):
            item = phase_roles[(phase, role)]
            rows.append(
                {
                    "phase": phase,
                    "rate_per_node": phases[phase]["rate_per_node"],
                    "role": role,
                    "head_observations": item["requests"],
                    "head_error_pct": fmt(
                        100.0 * item["errors"] / item["requests"] if item["requests"] else None
                    ),
                    "head_lag_p95_slots": fmt(percentile(item["slots"], 95)),
                    "head_lag_max_slots": fmt(max(item["slots"]) if item["slots"] else None),
                }
            )
    write_csv(data_dir / "cohort_phase_summary.csv", rows)


def main() -> int:
    args = parse_args()
    order, phases = load_phases(args.data_dir)
    if not order:
        raise SystemExit("no completed API phases found")
    worker_cap = experiment_worker_cap(args.data_dir)
    summarize_api(args.data_dir, order, phases)
    summarize_processor(args.data_dir, order, phases, worker_cap)
    summarize_cohorts(args.data_dir, order, phases)
    print(
        json.dumps(
            {
                "data_dir": str(args.data_dir.resolve()),
                "worker_cap": worker_cap,
                "phases": order,
                "outputs": [
                    "api_phase_summary.csv",
                    "processor_phase_summary.csv",
                    "cohort_phase_summary.csv",
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
