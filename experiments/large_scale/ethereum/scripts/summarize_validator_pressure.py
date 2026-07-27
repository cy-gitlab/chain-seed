#!/usr/bin/env python3
"""Append one D61 validator-pressure run to a compact sweep summary."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Iterable, Optional


FIELDS = [
    "validator_keys_per_client",
    "validator_clients",
    "active_validators",
    "beacons",
    "worker_cap",
    "status",
    "exit_code",
    "run_id",
    "data_dir",
    "duration_seconds",
    "tx_rate",
    "mem_avg_gib",
    "mem_p95_gib",
    "mem_growth_gib",
    "cpu_p95_percent",
    "block_prop_p95_sec",
    "block_prop_p99_sec",
    "head_lag_p95_slots",
    "head_lag_p99_slots",
    "head_lag_max_slots",
    "head_lag_gt1_pct",
    "head_lag_gt4_pct",
    "head_lag_gt8_pct",
    "head_api_errors",
    "attest_incl_p95_slots",
    "attest_incl_p99_slots",
    "attest_incl_max_slots",
    "validator_liveness_avg_pct",
    "validator_liveness_p05_pct",
    "validator_liveness_min_pct",
    "validator_liveness_max_missed",
    "validator_liveness_epochs",
    "validator_liveness_errors",
    "validator_liveness_last10_avg_pct",
    "finality_lag_p95_epochs",
    "finality_lag_max_epochs",
    "finality_lag_end_epochs",
    "finality_lag_gt4_pct",
    "finalized_epochs_last_hour",
    "finality_stagnation_end_sec",
    "missed_slot_rate_pct",
    "tx_total",
    "tx_sent",
    "tx_committed",
    "tx_pending",
    "tx_send_success_pct",
    "committed_tps",
    "tx_commit_p95_sec",
    "reorg_rate_per_h",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validator-keys-per-client", type=int, required=True)
    parser.add_argument("--validator-clients", type=int, required=True)
    parser.add_argument("--beacons", type=int, required=True)
    parser.add_argument("--worker-cap", type=int, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--tx-rate", type=float, required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--slot-seconds", type=float, default=12.0)
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
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if not math.isnan(parsed) else None


def percentile(values: list[float], pct: float) -> Optional[float]:
    clean = sorted(value for value in values if not math.isnan(value))
    if not clean:
        return None
    position = (len(clean) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    return clean[lower] * (upper - position) + clean[upper] * (position - lower)


def fmt(value: Optional[float], digits: int = 2) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def ratio_pct(numerator: int, denominator: int) -> Optional[float]:
    return 100.0 * numerator / denominator if denominator else None


def memory_metrics(data_dir: Path) -> tuple[list[float], list[float], Optional[float]]:
    memory: list[float] = []
    cpu: list[float] = []
    for row in read_rows(data_dir / "memory_raw.csv"):
        if row.get("node_type") != "beacon":
            continue
        mem_bytes = number(row.get("mem_usage_bytes"))
        cpu_percent = number(row.get("cpu_percent"))
        if mem_bytes is not None:
            memory.append(mem_bytes / (1024**3))
        if cpu_percent is not None:
            cpu.append(cpu_percent)

    steady: list[tuple[float, float]] = []
    for row in read_rows(data_dir / "memory_by_type.csv"):
        if row.get("node_type") != "beacon":
            continue
        elapsed = number(row.get("elapsed_sec"))
        mem_avg = number(row.get("mem_avg_bytes"))
        if elapsed is not None and mem_avg is not None and elapsed >= 2400:
            steady.append((elapsed, mem_avg / (1024**3)))
    growth = steady[-1][1] - steady[0][1] if steady else None
    return memory, cpu, growth


def propagation_metrics(data_dir: Path) -> tuple[list[float], list[float], int]:
    first_seen: dict[tuple[str, str], list[float]] = {}
    for row in read_rows(data_dir / "consensus" / "block_first_seen.csv"):
        elapsed = number(row.get("elapsed_sec"))
        if elapsed is None:
            continue
        key = (row.get("head_slot", ""), row.get("head_root", ""))
        first_seen.setdefault(key, []).append(elapsed)
    block_spreads = [max(values) - min(values) for values in first_seen.values() if len(values) > 1]

    head_ranges: dict[str, list[int]] = {}
    head_api_errors = 0
    for row in read_rows(data_dir / "consensus" / "beacon_head_samples.csv"):
        if (row.get("error") or "").strip():
            head_api_errors += 1
        head_slot = number(row.get("head_slot"))
        sample = row.get("sample", "")
        if head_slot is None or not sample:
            continue
        bounds = head_ranges.setdefault(sample, [int(head_slot), int(head_slot)])
        bounds[0] = min(bounds[0], int(head_slot))
        bounds[1] = max(bounds[1], int(head_slot))
    head_lags = [high - low for low, high in head_ranges.values()]
    return block_spreads, head_lags, head_api_errors


def missed_slot_rate(data_dir: Path, slot_seconds: float) -> Optional[float]:
    block_times: set[int] = set()
    for row in read_rows(data_dir / "block_metrics.csv"):
        if row.get("new_block") != "1":
            continue
        timestamp = number(row.get("block_time_unix"))
        if timestamp is not None:
            block_times.add(int(timestamp))
    ordered = sorted(block_times)
    if len(ordered) < 2:
        return None
    missed = 0
    for previous, current in zip(ordered, ordered[1:]):
        slot_gap = max(1, round((current - previous) / slot_seconds))
        missed += max(0, slot_gap - 1)
    expected = len(ordered) + missed
    return ratio_pct(missed, expected)


def finality_progress_metrics(
    data_dir: Path,
) -> tuple[list[float], Optional[float], Optional[int], Optional[float]]:
    samples: list[tuple[float, int, float]] = []
    for row in read_rows(data_dir / "consensus" / "finality_samples.csv"):
        if (row.get("error") or "").strip():
            continue
        elapsed = number(row.get("elapsed_sec"))
        finalized_epoch = number(row.get("finalized_epoch"))
        lag = number(row.get("finality_lag"))
        if elapsed is None or finalized_epoch is None or lag is None:
            continue
        samples.append((elapsed, int(finalized_epoch), lag))

    if not samples:
        return [], None, None, None

    samples.sort(key=lambda sample: sample[0])
    end_elapsed, end_finalized, end_lag = samples[-1]
    cutoff = end_elapsed - 3600.0
    hour_reference = samples[0]
    for sample in samples:
        if sample[0] > cutoff:
            break
        hour_reference = sample
    finalized_last_hour = end_finalized - hour_reference[1]

    tail_start = end_elapsed
    for elapsed, finalized_epoch, _ in reversed(samples):
        if finalized_epoch != end_finalized:
            break
        tail_start = elapsed

    return (
        [sample[2] for sample in samples],
        end_lag,
        finalized_last_hour,
        end_elapsed - tail_start,
    )


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir
    memory, cpu, memory_growth = memory_metrics(data_dir)
    block_spreads, head_lags, head_api_errors = propagation_metrics(data_dir)

    attestations = [
        value
        for row in read_rows(data_dir / "consensus" / "attestation_inclusions.csv")
        if (value := number(row.get("inclusion_delay_slots"))) is not None
    ]
    (
        finality_lags,
        finality_lag_end,
        finalized_epochs_last_hour,
        finality_stagnation_end,
    ) = finality_progress_metrics(data_dir)

    liveness_percentages: list[float] = []
    liveness_max_missed = 0
    liveness_errors = 0
    for row in read_rows(data_dir / "consensus" / "validator_liveness.csv"):
        if (row.get("error") or "").strip():
            liveness_errors += 1
            continue
        participation = number(row.get("participation_percent"))
        missed = number(row.get("missed_validators"))
        if participation is not None:
            liveness_percentages.append(participation)
        if missed is not None:
            liveness_max_missed = max(liveness_max_missed, int(missed))

    tx_total = 0
    tx_sent = 0
    for row in read_rows(data_dir / "tx-load" / "tx_sender.csv"):
        tx_total += 1
        if row.get("status") == "sent":
            tx_sent += 1
    tx_success = ratio_pct(tx_sent, tx_total)

    tx_commit_latencies = [
        value
        for row in read_rows(data_dir / "tx_receipts.csv")
        if (value := number(row.get("commit_latency_observed_sec"))) is not None
    ]
    receipt_info = read_json(data_dir / "tx_receipts_run_info.json")
    tx_committed = receipt_info.get("committed_transactions", len(tx_commit_latencies))
    tx_pending = receipt_info.get("pending_transactions", "")
    committed_tps = number(tx_committed)
    if committed_tps is not None and args.duration > 0:
        committed_tps /= args.duration

    reorg_events = list(read_rows(data_dir / "consensus" / "reorg_events.csv"))
    duration_hours = args.duration / 3600.0 if args.duration > 0 else 0.0
    reorg_rate = len(reorg_events) / duration_hours if duration_hours else None

    head_rounds = len(head_lags)
    row = {
        "validator_keys_per_client": args.validator_keys_per_client,
        "validator_clients": args.validator_clients,
        "active_validators": args.validator_clients * args.validator_keys_per_client,
        "beacons": args.beacons,
        "worker_cap": args.worker_cap,
        "status": args.status,
        "exit_code": args.exit_code,
        "run_id": args.run_id,
        "data_dir": str(data_dir.resolve()),
        "duration_seconds": fmt(args.duration, 0),
        "tx_rate": fmt(args.tx_rate, 3),
        "mem_avg_gib": fmt(sum(memory) / len(memory) if memory else None, 2),
        "mem_p95_gib": fmt(percentile(memory, 95), 2),
        "mem_growth_gib": fmt(memory_growth, 2),
        "cpu_p95_percent": fmt(percentile(cpu, 95), 1),
        "block_prop_p95_sec": fmt(percentile(block_spreads, 95), 2),
        "block_prop_p99_sec": fmt(percentile(block_spreads, 99), 2),
        "head_lag_p95_slots": fmt(percentile(head_lags, 95), 0),
        "head_lag_p99_slots": fmt(percentile(head_lags, 99), 0),
        "head_lag_max_slots": fmt(max(head_lags) if head_lags else None, 0),
        "head_lag_gt1_pct": fmt(ratio_pct(sum(value > 1 for value in head_lags), head_rounds), 2),
        "head_lag_gt4_pct": fmt(ratio_pct(sum(value > 4 for value in head_lags), head_rounds), 2),
        "head_lag_gt8_pct": fmt(ratio_pct(sum(value > 8 for value in head_lags), head_rounds), 2),
        "head_api_errors": head_api_errors,
        "attest_incl_p95_slots": fmt(percentile(attestations, 95), 0),
        "attest_incl_p99_slots": fmt(percentile(attestations, 99), 0),
        "attest_incl_max_slots": fmt(max(attestations) if attestations else None, 0),
        "validator_liveness_avg_pct": fmt(
            sum(liveness_percentages) / len(liveness_percentages) if liveness_percentages else None,
            4,
        ),
        "validator_liveness_p05_pct": fmt(percentile(liveness_percentages, 5), 4),
        "validator_liveness_min_pct": fmt(min(liveness_percentages) if liveness_percentages else None, 4),
        "validator_liveness_max_missed": liveness_max_missed if liveness_percentages else "",
        "validator_liveness_epochs": len(liveness_percentages),
        "validator_liveness_errors": liveness_errors,
        "validator_liveness_last10_avg_pct": fmt(
            sum(liveness_percentages[-10:]) / len(liveness_percentages[-10:])
            if liveness_percentages
            else None,
            4,
        ),
        "finality_lag_p95_epochs": fmt(percentile(finality_lags, 95), 0),
        "finality_lag_max_epochs": fmt(max(finality_lags) if finality_lags else None, 0),
        "finality_lag_end_epochs": fmt(finality_lag_end, 0),
        "finality_lag_gt4_pct": fmt(
            ratio_pct(sum(value > 4 for value in finality_lags), len(finality_lags)),
            4,
        ),
        "finalized_epochs_last_hour": (
            finalized_epochs_last_hour if finalized_epochs_last_hour is not None else ""
        ),
        "finality_stagnation_end_sec": fmt(finality_stagnation_end, 1),
        "missed_slot_rate_pct": fmt(missed_slot_rate(data_dir, args.slot_seconds), 4),
        "tx_total": tx_total,
        "tx_sent": tx_sent,
        "tx_committed": tx_committed,
        "tx_pending": tx_pending,
        "tx_send_success_pct": fmt(tx_success, 2),
        "committed_tps": fmt(committed_tps, 3),
        "tx_commit_p95_sec": fmt(percentile(tx_commit_latencies, 95), 1),
        "reorg_rate_per_h": fmt(reorg_rate, 2),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not args.output.exists() or args.output.stat().st_size == 0
    with args.output.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
