#!/usr/bin/env python3
"""Summarize high-resolution Beacon head visibility and companion run metrics."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional


SUMMARY_FIELDS = [
    "worker_cap",
    "beacons",
    "sampled_beacons",
    "duration_seconds",
    "eligible_blocks",
    "low_coverage_blocks",
    "competing_root_slots",
    "coverage_avg_pct",
    "coverage_p05_pct",
    "block_visibility_p50_ms",
    "block_visibility_p95_ms",
    "block_visibility_p99_ms",
    "full_spread_p95_ms",
    "blocks_visibility_gt_500ms_pct",
    "blocks_visibility_gt_1s_pct",
    "blocks_visibility_gt_2s_pct",
    "late_node_gt_1s_pct",
    "beacon_mem_avg_gib",
    "beacon_mem_p95_gib",
    "beacon_cpu_p95_percent",
    "attestation_inclusion_p95_slots",
    "validator_liveness_avg_pct",
    "validator_liveness_min_pct",
    "finality_lag_p95_epochs",
    "finality_lag_end_epochs",
    "tx_send_success_pct",
    "tx_committed",
    "tx_commit_p95_sec",
    "missed_slot_rate_pct",
    "reorg_rate_per_h",
]

BLOCK_FIELDS = [
    "slot",
    "block_root",
    "roots_observed",
    "nodes_observed",
    "coverage_pct",
    "first_observed_at",
    "last_observed_at",
    "first_elapsed_sec",
    "last_elapsed_sec",
    "visibility_p50_ms",
    "visibility_p95_ms",
    "full_spread_ms",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--warmup-seconds", type=float, default=300.0)
    parser.add_argument("--tail-seconds", type=float, default=24.0)
    parser.add_argument("--min-coverage-ratio", type=float, default=0.95)
    return parser.parse_args()


def read_rows(path: Path) -> Iterable[dict[str, str]]:
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


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


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


def ratio_pct(numerator: int, denominator: int) -> Optional[float]:
    return 100.0 * numerator / denominator if denominator else None


def rounded(value: Optional[float], digits: int = 3) -> Any:
    return "" if value is None else round(value, digits)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def in_window(elapsed: Optional[float], start: float, end: float) -> bool:
    return elapsed is not None and start <= elapsed <= end


def missed_slot_rate(data_dir: Path, start: float, end: float) -> Optional[float]:
    block_times: set[int] = set()
    for row in read_rows(data_dir / "block_metrics.csv"):
        if row.get("new_block") != "1":
            continue
        if not in_window(number(row.get("elapsed_sec")), start, end):
            continue
        timestamp = number(row.get("block_time_unix"))
        if timestamp is not None:
            block_times.add(int(timestamp))
    ordered = sorted(block_times)
    if len(ordered) < 2:
        return None
    missed = 0
    for previous, current in zip(ordered, ordered[1:]):
        slot_gap = max(1, round((current - previous) / 12.0))
        missed += max(0, slot_gap - 1)
    return ratio_pct(missed, len(ordered) + missed)


def main() -> int:
    args = parse_args()
    if args.warmup_seconds < 0 or args.tail_seconds < 0:
        raise SystemExit("warmup and tail seconds must be non-negative")
    if not 0.95 <= args.min_coverage_ratio <= 1:
        raise SystemExit("--min-coverage-ratio must be in [0.95, 1]")

    event_dir = args.data_dir / "block-visibility"
    event_path = event_dir / "head_events.csv"
    if not event_path.exists():
        raise SystemExit(f"missing event data: {event_path}")
    sampled = list(read_rows(event_dir / "sampled_beacons.csv"))
    expected_nodes = len(sampled)
    if expected_nodes < 2:
        raise SystemExit("at least two sampled Beacon nodes are required")

    run_info = read_json(event_dir / "run_info.json")
    maximum_elapsed = number(run_info.get("elapsed_sec"))
    if maximum_elapsed is None:
        maximum_elapsed = max(
            (
                value
                for row in read_rows(event_path)
                if (value := number(row.get("elapsed_sec"))) is not None
            ),
            default=0.0,
        )
    window_start = args.warmup_seconds
    window_end = maximum_elapsed - args.tail_seconds
    if window_end <= window_start:
        raise SystemExit(
            f"analysis window is empty: start={window_start}, end={window_end}"
        )

    observations: dict[int, dict[str, dict[int, int]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    elapsed_by_observation: dict[tuple[int, str, int], float] = {}
    timestamp_by_observation: dict[tuple[int, str, int], str] = {}
    for row in read_rows(event_path):
        elapsed = number(row.get("elapsed_sec"))
        monotonic_ns = number(row.get("observed_monotonic_ns"))
        if not in_window(elapsed, window_start, window_end) or monotonic_ns is None:
            continue
        try:
            slot = int(row["slot"])
            beacon_index = int(row["beacon_index"])
        except (KeyError, TypeError, ValueError):
            continue
        block_root = row.get("block_root", "")
        if not block_root:
            continue
        current = observations[slot][block_root].get(beacon_index)
        observed_ns = int(monotonic_ns)
        if current is None or observed_ns < current:
            observations[slot][block_root][beacon_index] = observed_ns
            elapsed_by_observation[(slot, block_root, beacon_index)] = float(elapsed)
            timestamp_by_observation[(slot, block_root, beacon_index)] = row.get(
                "observed_at", ""
            )

    minimum_nodes = max(2, math.ceil(expected_nodes * args.min_coverage_ratio))
    block_rows: list[dict[str, Any]] = []
    low_coverage_blocks = 0
    competing_root_slots = 0
    block_visibility: list[float] = []
    full_spreads: list[float] = []
    coverages: list[float] = []
    all_node_delays: list[float] = []

    for slot in sorted(observations):
        roots = observations[slot]
        if len(roots) > 1:
            competing_root_slots += 1
        dominant_root, by_node = max(
            roots.items(),
            key=lambda item: (
                len(item[1]),
                -min(item[1].values()),
                item[0],
            ),
        )
        if len(by_node) < minimum_nodes:
            low_coverage_blocks += 1
            continue
        ordered_ns = sorted(by_node.values())
        first_ns = ordered_ns[0]
        delays_ms = [(value - first_ns) / 1_000_000 for value in ordered_ns]
        p50_rank = math.ceil(expected_nodes * 0.50)
        p95_rank = math.ceil(expected_nodes * 0.95)
        visibility_p50 = delays_ms[p50_rank - 1]
        visibility_p95 = delays_ms[p95_rank - 1]
        full_spread = delays_ms[-1]
        coverage = 100.0 * len(by_node) / expected_nodes
        first_elapsed = min(
            elapsed_by_observation[(slot, dominant_root, index)] for index in by_node
        )
        last_elapsed = max(
            elapsed_by_observation[(slot, dominant_root, index)] for index in by_node
        )
        first_index = min(by_node, key=by_node.get)
        last_index = max(by_node, key=by_node.get)
        block_rows.append(
            {
                "slot": slot,
                "block_root": dominant_root,
                "roots_observed": len(roots),
                "nodes_observed": len(by_node),
                "coverage_pct": rounded(coverage),
                "first_observed_at": timestamp_by_observation.get(
                    (slot, dominant_root, first_index), ""
                ),
                "last_observed_at": timestamp_by_observation.get(
                    (slot, dominant_root, last_index), ""
                ),
                "first_elapsed_sec": rounded(first_elapsed, 6),
                "last_elapsed_sec": rounded(last_elapsed, 6),
                "visibility_p50_ms": rounded(visibility_p50),
                "visibility_p95_ms": rounded(visibility_p95),
                "full_spread_ms": rounded(full_spread),
            }
        )
        if visibility_p95 is not None:
            block_visibility.append(visibility_p95)
        full_spreads.append(full_spread)
        coverages.append(coverage)
        all_node_delays.extend(delays_ms)

    if not block_rows:
        raise SystemExit(
            f"no blocks reached the minimum coverage of {minimum_nodes}/{expected_nodes}"
        )
    write_csv(event_dir / "block_visibility.csv", BLOCK_FIELDS, block_rows)

    env = read_env(args.data_dir / "experiment.env")
    memory: list[float] = []
    cpu: list[float] = []
    for row in read_rows(args.data_dir / "memory_raw.csv"):
        if row.get("node_type") != "beacon":
            continue
        elapsed = number(row.get("elapsed_sec"))
        if not in_window(elapsed, window_start, window_end):
            continue
        mem_bytes = number(row.get("mem_usage_bytes"))
        cpu_percent = number(row.get("cpu_percent"))
        if mem_bytes is not None:
            memory.append(mem_bytes / (1024**3))
        if cpu_percent is not None:
            cpu.append(cpu_percent)

    attestations = [
        value
        for row in read_rows(args.data_dir / "consensus" / "attestation_inclusions.csv")
        if in_window(number(row.get("elapsed_sec")), window_start, window_end)
        and (value := number(row.get("inclusion_delay_slots"))) is not None
    ]
    liveness = [
        value
        for row in read_rows(args.data_dir / "consensus" / "validator_liveness.csv")
        if not (row.get("error") or "").strip()
        and in_window(number(row.get("elapsed_sec")), window_start, window_end)
        and (value := number(row.get("participation_percent"))) is not None
    ]
    finality_samples = [
        (elapsed, lag)
        for row in read_rows(args.data_dir / "consensus" / "finality_samples.csv")
        if not (row.get("error") or "").strip()
        and (elapsed := number(row.get("elapsed_sec"))) is not None
        and in_window(elapsed, window_start, window_end)
        and (lag := number(row.get("finality_lag"))) is not None
    ]
    finality_lags = [lag for _elapsed, lag in finality_samples]
    finality_end = finality_samples[-1][1] if finality_samples else None

    tx_total = 0
    tx_sent = 0
    for row in read_rows(args.data_dir / "tx-load" / "tx_sender.csv"):
        elapsed = number(row.get("elapsed_sec"))
        if not in_window(elapsed, window_start, window_end):
            continue
        tx_total += 1
        if row.get("status") == "sent":
            tx_sent += 1
    tx_commit_latencies = [
        value
        for row in read_rows(args.data_dir / "tx_receipts.csv")
        if in_window(number(row.get("accepted_elapsed_sec")), window_start, window_end)
        and (value := number(row.get("commit_latency_observed_sec"))) is not None
    ]
    tx_committed = len(tx_commit_latencies)

    reorg_events = [
        row
        for row in read_rows(args.data_dir / "consensus" / "reorg_events.csv")
        if in_window(number(row.get("elapsed_sec")), window_start, window_end)
    ]
    duration_hours = (window_end - window_start) / 3600.0
    reorg_rate = len(reorg_events) / duration_hours if duration_hours > 0 else None

    summary = {
        "worker_cap": env.get("lighthouse_processor_workers_cap", ""),
        "beacons": env.get("beacons", ""),
        "sampled_beacons": expected_nodes,
        "duration_seconds": rounded(window_end - window_start, 3),
        "eligible_blocks": len(block_rows),
        "low_coverage_blocks": low_coverage_blocks,
        "competing_root_slots": competing_root_slots,
        "coverage_avg_pct": rounded(
            sum(coverages) / len(coverages) if coverages else None
        ),
        "coverage_p05_pct": rounded(percentile(coverages, 5)),
        "block_visibility_p50_ms": rounded(percentile(block_visibility, 50)),
        "block_visibility_p95_ms": rounded(percentile(block_visibility, 95)),
        "block_visibility_p99_ms": rounded(percentile(block_visibility, 99)),
        "full_spread_p95_ms": rounded(percentile(full_spreads, 95)),
        "blocks_visibility_gt_500ms_pct": rounded(
            ratio_pct(sum(value > 500 for value in block_visibility), len(block_visibility))
        ),
        "blocks_visibility_gt_1s_pct": rounded(
            ratio_pct(sum(value > 1000 for value in block_visibility), len(block_visibility))
        ),
        "blocks_visibility_gt_2s_pct": rounded(
            ratio_pct(sum(value > 2000 for value in block_visibility), len(block_visibility))
        ),
        "late_node_gt_1s_pct": rounded(
            ratio_pct(sum(value > 1000 for value in all_node_delays), len(all_node_delays))
        ),
        "beacon_mem_avg_gib": rounded(
            sum(memory) / len(memory) if memory else None
        ),
        "beacon_mem_p95_gib": rounded(percentile(memory, 95)),
        "beacon_cpu_p95_percent": rounded(percentile(cpu, 95)),
        "attestation_inclusion_p95_slots": rounded(percentile(attestations, 95)),
        "validator_liveness_avg_pct": rounded(
            sum(liveness) / len(liveness) if liveness else None
        ),
        "validator_liveness_min_pct": rounded(min(liveness) if liveness else None),
        "finality_lag_p95_epochs": rounded(percentile(finality_lags, 95)),
        "finality_lag_end_epochs": rounded(finality_end),
        "tx_send_success_pct": rounded(ratio_pct(tx_sent, tx_total)),
        "tx_committed": tx_committed,
        "tx_commit_p95_sec": rounded(percentile(tx_commit_latencies, 95)),
        "missed_slot_rate_pct": rounded(
            missed_slot_rate(args.data_dir, window_start, window_end)
        ),
        "reorg_rate_per_h": rounded(reorg_rate),
    }
    details = {
        "analysis": {
            "window_start_sec": window_start,
            "window_end_sec": window_end,
            "warmup_seconds": args.warmup_seconds,
            "tail_seconds": args.tail_seconds,
            "min_coverage_ratio": args.min_coverage_ratio,
            "minimum_nodes": minimum_nodes,
            "event_run_info": run_info,
        },
        "summary": summary,
    }
    (event_dir / "summary.json").write_text(json.dumps(details, indent=2) + "\n")
    write_csv(event_dir / "summary.csv", SUMMARY_FIELDS, [summary])
    print(json.dumps(summary, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
