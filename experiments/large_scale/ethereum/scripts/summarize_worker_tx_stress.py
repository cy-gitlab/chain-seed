#!/usr/bin/env python3
"""Summarize phased mixed-transaction worker stress experiments."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


PHASE_SUMMARY_FIELDS = [
    "worker_cap",
    "phase",
    "target_tps",
    "duration_sec",
    "tx_attempted",
    "tx_sent",
    "tx_send_success_pct",
    "achieved_send_tps",
    "scheduler_skipped",
    "tx_committed",
    "tx_commit_pct",
    "committed_tps",
    "tx_execution_success_pct",
    "tx_commit_p95_sec",
    "blocks",
    "tx_per_block_avg",
    "tx_per_block_p95",
    "gas_used_avg_pct",
    "gas_used_p95_pct",
    "block_size_p95_kib",
    "block_visibility_p50_ms",
    "block_visibility_p95_ms",
    "block_visibility_p99_ms",
    "blocks_visibility_gt_2s_pct",
    "beacon_mem_avg_gib",
    "beacon_mem_p95_gib",
    "beacon_cpu_p95_percent",
    "processor_workers_active_p95",
    "processor_worker_saturation_pct",
    "processor_events_ignored",
    "processor_send_errors",
    "attestation_inclusion_p95_slots",
    "validator_liveness_avg_pct",
    "validator_liveness_min_pct",
    "finality_lag_p95_epochs",
    "missed_slot_rate_pct",
    "reorg_rate_per_h",
]
TYPE_SUMMARY_FIELDS = [
    "worker_cap",
    "phase",
    "target_tps",
    "tx_type",
    "attempted",
    "sent",
    "send_success_pct",
    "committed",
    "commit_pct",
    "execution_success_pct",
    "commit_p95_sec",
    "gas_used_avg",
    "data_bytes_avg",
]
PROCESSOR_SUMMARY_FIELDS = [
    "worker_cap",
    "phase",
    "target_tps",
    "work_type",
    "monitored_beacons",
    "workers_active_p95",
    "worker_saturation_pct",
    "scrape_errors",
    "events_received",
    "events_started",
    "events_ignored",
    "send_errors",
    "worker_tasks",
    "worker_time_avg_ms",
    "worker_time_p95_upper_sec",
    "queue_mean_length",
    "queue_p95_upper",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    return parser.parse_args()


def read_csv(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        return []
    handle = path.open(newline="")
    reader = csv.DictReader(handle)

    def iterator() -> Iterable[dict[str, str]]:
        try:
            yield from reader
        finally:
            handle.close()

    return iterator()


def read_gzip_csv(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        return []
    handle = gzip.open(path, "rt", newline="")
    reader = csv.DictReader(handle)

    def iterator() -> Iterable[dict[str, str]]:
        try:
            yield from reader
        finally:
            handle.close()

    return iterator()


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if not math.isnan(parsed) else None


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    ordered = sorted(value for value in values if not math.isnan(value))
    if not ordered:
        return None
    position = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def rounded(value: Optional[float], digits: int = 3) -> Any:
    return "" if value is None else round(value, digits)


def ratio_pct(numerator: float, denominator: float) -> Optional[float]:
    return 100.0 * numerator / denominator if denominator else None


def parse_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines() if path.exists() else []:
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def load_phases(data_dir: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    order: list[str] = []
    phases: dict[str, dict[str, Any]] = {}
    for row in read_csv(data_dir / "tx-load" / "tx_phases.csv"):
        start = parse_timestamp(row.get("start_timestamp", ""))
        end = parse_timestamp(row.get("end_timestamp", ""))
        if start is None or end is None:
            continue
        name = row["phase"]
        order.append(name)
        phases[name] = {
            **row,
            "start": start,
            "end": end,
            "duration": max(0.0, (end - start).total_seconds()),
            "target_tps": float(row["target_rate"]),
        }
    return order, phases


def locate_phase(
    timestamp: Optional[datetime], order: list[str], phases: dict[str, dict[str, Any]]
) -> str:
    if timestamp is None:
        return ""
    for name in order:
        if phases[name]["start"] <= timestamp <= phases[name]["end"]:
            return name
    return ""


def cumulative_delta(first: Optional[float], last: Optional[float]) -> float:
    if first is None or last is None:
        return 0.0
    return max(0.0, last - first)


def bucket_quantile(
    buckets: dict[str, float], count: float, quantile: float
) -> Optional[float]:
    if count <= 0 or not buckets:
        return None
    finite = sorted(
        (float(limit), value)
        for limit, value in buckets.items()
        if limit not in {"+Inf", "Inf", "inf"}
    )
    threshold = count * quantile
    for upper, cumulative in finite:
        if cumulative >= threshold:
            return upper
    return finite[-1][0] if finite else None


def missed_slot_rate(block_times: list[int]) -> Optional[float]:
    ordered = sorted(set(block_times))
    if len(ordered) < 2:
        return None
    missed = 0
    for previous, current in zip(ordered, ordered[1:]):
        slots = max(1, round((current - previous) / 12.0))
        missed += max(0, slots - 1)
    return ratio_pct(missed, len(ordered) + missed)


def transaction_stats(
    data_dir: Path,
    order: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    phase_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "attempted": 0,
            "sent": 0,
            "failed": 0,
            "scheduler_skipped": 0,
            "committed": 0,
            "execution_known": 0,
            "execution_success": 0,
            "latencies": [],
        }
    )
    type_stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "attempted": 0,
            "sent": 0,
            "committed": 0,
            "execution_known": 0,
            "execution_success": 0,
            "latencies": [],
            "gas_used": [],
            "data_bytes": [],
        }
    )
    valid = set(order)
    for row in read_csv(data_dir / "tx-load" / "tx_sender.csv"):
        phase = row.get("phase", "")
        if phase not in valid:
            continue
        tx_type = row.get("tx_type", "") or "unknown"
        item = phase_stats[phase]
        typed = type_stats[(phase, tx_type)]
        item["attempted"] += 1
        typed["attempted"] += 1
        data_bytes = number(row.get("data_bytes"))
        if data_bytes is not None:
            typed["data_bytes"].append(data_bytes)
        if row.get("status") == "sent":
            item["sent"] += 1
            typed["sent"] += 1
        else:
            item["failed"] += 1
    for phase in order:
        phase_stats[phase]["scheduler_skipped"] = int(
            phases_value(data_dir, phase, "scheduler_skipped")
        )

    for row in read_csv(data_dir / "tx_receipts.csv"):
        phase = row.get("phase", "")
        if phase not in valid:
            continue
        tx_type = row.get("tx_type", "") or "unknown"
        item = phase_stats[phase]
        typed = type_stats[(phase, tx_type)]
        item["committed"] += 1
        typed["committed"] += 1
        latency = number(row.get("commit_latency_observed_sec"))
        if latency is not None:
            item["latencies"].append(latency)
            typed["latencies"].append(latency)
        gas_used = number(row.get("gas_used"))
        if gas_used is not None:
            typed["gas_used"].append(gas_used)
        status = number(row.get("status"))
        if status is not None:
            item["execution_known"] += 1
            typed["execution_known"] += 1
            if status == 1:
                item["execution_success"] += 1
                typed["execution_success"] += 1
    return phase_stats, type_stats


def phases_value(data_dir: Path, phase: str, field: str) -> float:
    for row in read_csv(data_dir / "tx-load" / "tx_phases.csv"):
        if row.get("phase") == phase:
            return number(row.get(field)) or 0.0
    return 0.0


def block_stats(
    data_dir: Path,
    order: list[str],
    phases: dict[str, dict[str, Any]],
) -> dict[str, dict[str, list[float]]]:
    stats: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {
            "tx_count": [],
            "gas_pct": [],
            "size": [],
            "block_times": [],
            "count": [],
        }
    )
    for row in read_csv(data_dir / "block_metrics.csv"):
        if row.get("new_block") != "1" or row.get("error"):
            continue
        block_time = number(row.get("block_time_unix"))
        timestamp = (
            datetime.fromtimestamp(block_time, timezone.utc)
            if block_time is not None and block_time > 0
            else parse_timestamp(row.get("timestamp", ""))
        )
        phase = locate_phase(timestamp, order, phases)
        if not phase:
            continue
        item = stats[phase]
        item["count"].append(1.0)
        tx_count = number(row.get("tx_count"))
        gas_pct = number(row.get("gas_used_percent"))
        size = number(row.get("block_size_bytes"))
        if tx_count is not None:
            item["tx_count"].append(tx_count)
        if gas_pct is None:
            gas_used = number(row.get("gas_used"))
            gas_limit = number(row.get("gas_limit"))
            gas_pct = (
                100.0 * gas_used / gas_limit
                if gas_used is not None and gas_limit
                else None
            )
        if gas_pct is not None:
            item["gas_pct"].append(gas_pct)
        if size is not None:
            item["size"].append(size)
        if block_time is not None:
            item["block_times"].append(block_time)
    return stats


def visibility_stats(
    data_dir: Path,
    order: list[str],
    phases: dict[str, dict[str, Any]],
) -> dict[str, list[float]]:
    stats: dict[str, list[float]] = defaultdict(list)
    for row in read_csv(data_dir / "block-visibility" / "block_visibility.csv"):
        phase = locate_phase(
            parse_timestamp(row.get("first_observed_at", "")), order, phases
        )
        value = number(row.get("visibility_p95_ms"))
        if phase and value is not None:
            stats[phase].append(value)
    return stats


def timestamp_values(
    path: Path,
    value_field: str,
    order: list[str],
    phases: dict[str, dict[str, Any]],
    *,
    predicate: Optional[Any] = None,
) -> dict[str, list[float]]:
    values: dict[str, list[float]] = defaultdict(list)
    for row in read_csv(path):
        if predicate is not None and not predicate(row):
            continue
        phase = locate_phase(parse_timestamp(row.get("timestamp", "")), order, phases)
        value = number(row.get(value_field))
        if phase and value is not None:
            values[phase].append(value)
    return values


def processor_stats(
    data_dir: Path,
    order: list[str],
    phases: dict[str, dict[str, Any]],
    worker_cap: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    path = data_dir / "processor-metrics" / "processor_metrics.csv.gz"
    snapshots: dict[
        tuple[str, str, str], dict[str, tuple[Optional[float], Optional[float]]]
    ] = {}
    bucket_snapshots: dict[
        tuple[str, str, str], dict[str, dict[str, tuple[float, float]]]
    ] = {}
    active: dict[str, list[float]] = defaultdict(list)
    scrape_errors: dict[tuple[str, str], int] = defaultdict(int)
    nodes: dict[tuple[str, str], set[str]] = defaultdict(set)
    valid = set(order)
    first_work_type = ""

    for row in read_gzip_csv(path):
        phase = locate_phase(parse_timestamp(row.get("timestamp", "")), order, phases)
        if phase not in valid:
            continue
        work_type = row.get("work_type", "")
        node = row.get("beacon_index", "")
        if not first_work_type:
            first_work_type = work_type
        if work_type == first_work_type:
            worker_value = number(row.get("workers_active"))
            if worker_value is not None:
                active[phase].append(worker_value)
        if row.get("error"):
            scrape_errors[(phase, work_type)] += 1
        nodes[(phase, work_type)].add(node)
        key = (phase, work_type, node)
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
            value = number(row.get(field))
            if value is None:
                continue
            first, _ = state.get(field, (value, value))
            state[field] = (first, value)
        buckets = bucket_snapshots.setdefault(key, {"worker": {}, "queue": {}})
        for kind, field in (
            ("worker", "worker_time_buckets_json"),
            ("queue", "queue_length_buckets_json"),
        ):
            if not row.get(field):
                continue
            try:
                parsed = json.loads(row[field])
            except json.JSONDecodeError:
                continue
            for limit, raw_value in parsed.items():
                value = float(raw_value)
                first, _ = buckets[kind].get(limit, (value, value))
                buckets[kind][limit] = (first, value)

    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "deltas": defaultdict(float),
            "worker_buckets": defaultdict(float),
            "queue_buckets": defaultdict(float),
        }
    )
    for key, state in snapshots.items():
        phase, work_type, _node = key
        item = grouped[(phase, work_type)]
        for field, (first, last) in state.items():
            item["deltas"][field] += cumulative_delta(first, last)
        for limit, (first, last) in bucket_snapshots[key]["worker"].items():
            item["worker_buckets"][limit] += cumulative_delta(first, last)
        for limit, (first, last) in bucket_snapshots[key]["queue"].items():
            item["queue_buckets"][limit] += cumulative_delta(first, last)

    rows: list[dict[str, Any]] = []
    phase_rollup: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"events_ignored": 0.0, "send_errors": 0.0}
    )
    for phase in order:
        active_values = active[phase]
        saturation = (
            ratio_pct(sum(value >= worker_cap for value in active_values), len(active_values))
            if worker_cap and active_values
            else None
        )
        phase_rollup[phase]["workers_active_p95"] = percentile(active_values, 95)
        phase_rollup[phase]["worker_saturation_pct"] = saturation
        work_types = sorted(work_type for key_phase, work_type in grouped if key_phase == phase)
        for work_type in work_types:
            item = grouped[(phase, work_type)]
            deltas = item["deltas"]
            worker_count = deltas.get("worker_time_count", 0.0)
            queue_count = deltas.get("queue_observations", 0.0)
            phase_rollup[phase]["events_ignored"] += deltas.get("events_ignored", 0.0)
            phase_rollup[phase]["send_errors"] += deltas.get("send_errors", 0.0)
            rows.append(
                {
                    "worker_cap": worker_cap,
                    "phase": phase,
                    "target_tps": phases[phase]["target_tps"],
                    "work_type": work_type,
                    "monitored_beacons": len(nodes[(phase, work_type)]),
                    "workers_active_p95": rounded(percentile(active_values, 95)),
                    "worker_saturation_pct": rounded(saturation),
                    "scrape_errors": scrape_errors[(phase, work_type)],
                    "events_received": rounded(deltas.get("events_received", 0.0), 0),
                    "events_started": rounded(deltas.get("events_started", 0.0), 0),
                    "events_ignored": rounded(deltas.get("events_ignored", 0.0), 0),
                    "send_errors": rounded(deltas.get("send_errors", 0.0), 0),
                    "worker_tasks": rounded(worker_count, 0),
                    "worker_time_avg_ms": rounded(
                        1000.0 * deltas.get("worker_time_sum_sec", 0.0) / worker_count
                        if worker_count
                        else None
                    ),
                    "worker_time_p95_upper_sec": rounded(
                        bucket_quantile(item["worker_buckets"], worker_count, 0.95)
                    ),
                    "queue_mean_length": rounded(
                        deltas.get("queue_length_sum", 0.0) / queue_count
                        if queue_count
                        else None
                    ),
                    "queue_p95_upper": rounded(
                        bucket_quantile(item["queue_buckets"], queue_count, 0.95)
                    ),
                }
            )
    return rows, phase_rollup


def main() -> int:
    args = parse_args()
    order, phases = load_phases(args.data_dir)
    if not order:
        raise SystemExit("no completed transaction phases found")
    env = read_env(args.data_dir / "experiment.env")
    worker_cap = int(env.get("lighthouse_processor_workers_cap", "0") or 0)

    phase_tx, type_tx = transaction_stats(args.data_dir, order)
    blocks = block_stats(args.data_dir, order, phases)
    visibility = visibility_stats(args.data_dir, order, phases)
    memory = timestamp_values(
        args.data_dir / "memory_raw.csv",
        "mem_usage_bytes",
        order,
        phases,
        predicate=lambda row: row.get("node_type") == "beacon",
    )
    cpu = timestamp_values(
        args.data_dir / "memory_raw.csv",
        "cpu_percent",
        order,
        phases,
        predicate=lambda row: row.get("node_type") == "beacon",
    )
    attestations = timestamp_values(
        args.data_dir / "consensus" / "attestation_inclusions.csv",
        "inclusion_delay_slots",
        order,
        phases,
    )
    liveness = timestamp_values(
        args.data_dir / "consensus" / "validator_liveness.csv",
        "participation_percent",
        order,
        phases,
        predicate=lambda row: not row.get("error"),
    )
    finality = timestamp_values(
        args.data_dir / "consensus" / "finality_samples.csv",
        "finality_lag",
        order,
        phases,
        predicate=lambda row: not row.get("error"),
    )
    reorgs: dict[str, int] = defaultdict(int)
    for row in read_csv(args.data_dir / "consensus" / "reorg_events.csv"):
        phase = locate_phase(parse_timestamp(row.get("timestamp", "")), order, phases)
        if phase:
            reorgs[phase] += 1

    processor_rows, processor = processor_stats(
        args.data_dir, order, phases, worker_cap
    )
    phase_rows: list[dict[str, Any]] = []
    for phase in order:
        tx = phase_tx[phase]
        block = blocks[phase]
        visible = visibility[phase]
        duration = phases[phase]["duration"]
        execution_known = tx["execution_known"]
        phase_rows.append(
            {
                "worker_cap": worker_cap,
                "phase": phase,
                "target_tps": phases[phase]["target_tps"],
                "duration_sec": rounded(duration),
                "tx_attempted": tx["attempted"],
                "tx_sent": tx["sent"],
                "tx_send_success_pct": rounded(ratio_pct(tx["sent"], tx["attempted"])),
                "achieved_send_tps": rounded(tx["sent"] / duration if duration else None),
                "scheduler_skipped": tx["scheduler_skipped"],
                "tx_committed": tx["committed"],
                "tx_commit_pct": rounded(ratio_pct(tx["committed"], tx["sent"])),
                "committed_tps": rounded(tx["committed"] / duration if duration else None),
                "tx_execution_success_pct": rounded(
                    ratio_pct(tx["execution_success"], execution_known)
                ),
                "tx_commit_p95_sec": rounded(percentile(tx["latencies"], 95)),
                "blocks": len(block["count"]),
                "tx_per_block_avg": rounded(
                    sum(block["tx_count"]) / len(block["tx_count"])
                    if block["tx_count"]
                    else None
                ),
                "tx_per_block_p95": rounded(percentile(block["tx_count"], 95)),
                "gas_used_avg_pct": rounded(
                    sum(block["gas_pct"]) / len(block["gas_pct"])
                    if block["gas_pct"]
                    else None
                ),
                "gas_used_p95_pct": rounded(percentile(block["gas_pct"], 95)),
                "block_size_p95_kib": rounded(
                    (percentile(block["size"], 95) or 0.0) / 1024.0
                    if block["size"]
                    else None
                ),
                "block_visibility_p50_ms": rounded(percentile(visible, 50)),
                "block_visibility_p95_ms": rounded(percentile(visible, 95)),
                "block_visibility_p99_ms": rounded(percentile(visible, 99)),
                "blocks_visibility_gt_2s_pct": rounded(
                    ratio_pct(sum(value > 2000 for value in visible), len(visible))
                ),
                "beacon_mem_avg_gib": rounded(
                    sum(memory[phase]) / len(memory[phase]) / (1024**3)
                    if memory[phase]
                    else None
                ),
                "beacon_mem_p95_gib": rounded(
                    (percentile(memory[phase], 95) or 0.0) / (1024**3)
                    if memory[phase]
                    else None
                ),
                "beacon_cpu_p95_percent": rounded(percentile(cpu[phase], 95)),
                "processor_workers_active_p95": rounded(
                    processor[phase].get("workers_active_p95")
                ),
                "processor_worker_saturation_pct": rounded(
                    processor[phase].get("worker_saturation_pct")
                ),
                "processor_events_ignored": rounded(
                    processor[phase].get("events_ignored", 0.0), 0
                ),
                "processor_send_errors": rounded(
                    processor[phase].get("send_errors", 0.0), 0
                ),
                "attestation_inclusion_p95_slots": rounded(
                    percentile(attestations[phase], 95)
                ),
                "validator_liveness_avg_pct": rounded(
                    sum(liveness[phase]) / len(liveness[phase])
                    if liveness[phase]
                    else None
                ),
                "validator_liveness_min_pct": rounded(
                    min(liveness[phase]) if liveness[phase] else None
                ),
                "finality_lag_p95_epochs": rounded(percentile(finality[phase], 95)),
                "missed_slot_rate_pct": rounded(missed_slot_rate(block["block_times"])),
                "reorg_rate_per_h": rounded(
                    reorgs[phase] / (duration / 3600.0) if duration else None
                ),
            }
        )

    type_rows: list[dict[str, Any]] = []
    for phase in order:
        for phase_name, tx_type in sorted(type_tx):
            if phase_name != phase:
                continue
            item = type_tx[(phase_name, tx_type)]
            type_rows.append(
                {
                    "worker_cap": worker_cap,
                    "phase": phase,
                    "target_tps": phases[phase]["target_tps"],
                    "tx_type": tx_type,
                    "attempted": item["attempted"],
                    "sent": item["sent"],
                    "send_success_pct": rounded(
                        ratio_pct(item["sent"], item["attempted"])
                    ),
                    "committed": item["committed"],
                    "commit_pct": rounded(ratio_pct(item["committed"], item["sent"])),
                    "execution_success_pct": rounded(
                        ratio_pct(item["execution_success"], item["execution_known"])
                    ),
                    "commit_p95_sec": rounded(percentile(item["latencies"], 95)),
                    "gas_used_avg": rounded(
                        sum(item["gas_used"]) / len(item["gas_used"])
                        if item["gas_used"]
                        else None
                    ),
                    "data_bytes_avg": rounded(
                        sum(item["data_bytes"]) / len(item["data_bytes"])
                        if item["data_bytes"]
                        else None
                    ),
                }
            )

    write_csv(args.data_dir / "tx_stress_phase_summary.csv", PHASE_SUMMARY_FIELDS, phase_rows)
    write_csv(args.data_dir / "tx_type_phase_summary.csv", TYPE_SUMMARY_FIELDS, type_rows)
    write_csv(
        args.data_dir / "processor_tx_phase_summary.csv",
        PROCESSOR_SUMMARY_FIELDS,
        processor_rows,
    )
    payload = {
        "data_dir": str(args.data_dir.resolve()),
        "worker_cap": worker_cap,
        "phases": order,
        "outputs": [
            "tx_stress_phase_summary.csv",
            "tx_type_phase_summary.csv",
            "processor_tx_phase_summary.csv",
        ],
    }
    (args.data_dir / "tx_stress_summary.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
