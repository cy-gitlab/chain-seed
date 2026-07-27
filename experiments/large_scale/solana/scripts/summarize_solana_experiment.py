#!/usr/bin/env python3
"""Summarize and validate a D61 large-scale Solana experiment."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Optional, Sequence


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Experiment run dir or solana-large data dir.")
    parser.add_argument("--expected-validators", type=int, default=0)
    parser.add_argument("--min-active", type=int, default=0)
    parser.add_argument("--min-gossip-nodes", type=int, default=0)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-text", type=Path)
    parser.add_argument("--fail-on-fail", action="store_true")
    return parser.parse_args(argv)


def resolve_data_dir(path: Path) -> Path:
    path = path.resolve()
    if path.name == "solana-large":
        return path
    if (path / "solana-large").is_dir():
        return path / "solana-large"
    return path


def read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def to_int(value: Any) -> Optional[int]:
    try:
        text = str(value).strip()
        if text == "":
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> Optional[float]:
    try:
        text = str(value).strip()
        if text == "":
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def int_series(rows: list[dict[str, str]], field: str) -> list[int]:
    values: list[int] = []
    for row in rows:
        value = to_int(row.get(field))
        if value is not None:
            values.append(value)
    return values


def float_series(rows: list[dict[str, str]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = to_float(row.get(field))
        if value is not None:
            values.append(value)
    return values


def first_last_delta(values: list[int]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    if not values:
        return None, None, None
    if len(values) == 1:
        return values[0], values[0], 0
    return values[0], values[-1], values[-1] - values[0]


def longest_below_window(rows: list[dict[str, str]], field: str, threshold: int) -> dict[str, Any]:
    longest_samples = 0
    current_samples = 0
    longest_start: Optional[str] = None
    longest_end: Optional[str] = None
    current_start: Optional[str] = None
    current_last: Optional[str] = None

    for row in rows:
        value = to_int(row.get(field))
        timestamp = row.get("timestamp", "")
        if value is not None and value < threshold:
            if current_samples == 0:
                current_start = timestamp
            current_samples += 1
            current_last = timestamp
            if current_samples > longest_samples:
                longest_samples = current_samples
                longest_start = current_start
                longest_end = current_last
        else:
            current_samples = 0
            current_start = None
            current_last = None

    return {
        "longest_below_samples": longest_samples,
        "longest_below_start": longest_start,
        "longest_below_end": longest_end,
    }


def summarize_active(data_dir: Path, expected: int, min_active: int) -> dict[str, Any]:
    rows = read_rows(data_dir / "active_validators.csv")
    active = int_series(rows, "active_agave")
    running = int_series(rows, "running_solana")
    below = [value for value in active if min_active > 0 and value < min_active]

    summary: dict[str, Any] = {
        "samples": len(active),
        "running_samples": len(running),
        "active_min": min(active) if active else None,
        "active_max": max(active) if active else None,
        "active_last": active[-1] if active else None,
        "running_min": min(running) if running else None,
        "running_max": max(running) if running else None,
        "running_last": running[-1] if running else None,
        "below_min_active_samples": len(below),
        "all_active_samples_ok": bool(active) and (min_active <= 0 or not below),
        "expected_validators": expected,
        "min_active": min_active,
    }
    summary.update(longest_below_window(rows, "active_agave", min_active) if min_active > 0 else {})
    return summary


def summarize_blocks(data_dir: Path) -> dict[str, Any]:
    rows = read_rows(data_dir / "block_metrics.csv")
    slots = int_series(rows, "slot")
    heights = int_series(rows, "block_height")
    tx_counts = int_series(rows, "transaction_count")
    voting_validators = int_series(rows, "voting_validators")
    delinquent_validators = int_series(rows, "delinquent_validators")
    slot_start, slot_end, slot_delta = first_last_delta(slots)
    height_start, height_end, height_delta = first_last_delta(heights)
    tx_start, tx_end, tx_delta = first_last_delta(tx_counts)
    return {
        "samples": len(rows),
        "slot_start": slot_start,
        "slot_end": slot_end,
        "slot_delta": slot_delta,
        "block_height_start": height_start,
        "block_height_end": height_end,
        "block_height_delta": height_delta,
        "transaction_count_start": tx_start,
        "transaction_count_end": tx_end,
        "transaction_count_delta": tx_delta,
        "voting_validators_min": min(voting_validators) if voting_validators else None,
        "voting_validators_max": max(voting_validators) if voting_validators else None,
        "voting_validators_last": voting_validators[-1] if voting_validators else None,
        "delinquent_validators_min": min(delinquent_validators) if delinquent_validators else None,
        "delinquent_validators_max": max(delinquent_validators) if delinquent_validators else None,
        "delinquent_validators_last": delinquent_validators[-1] if delinquent_validators else None,
        "slot_progressed": slot_delta is not None and slot_delta > 0,
        "transactions_recorded": tx_delta is not None and tx_delta > 0,
    }


def summarize_tx_load(data_dir: Path) -> dict[str, Any]:
    rows = read_rows(data_dir / "tx-load" / "tx_sender.csv")
    statuses: dict[str, int] = {}
    for row in rows:
        status = (row.get("status") or "unknown").strip() or "unknown"
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "samples": len(rows),
        "sent": statuses.get("sent", 0),
        "failed": statuses.get("failed", 0),
        "statuses": statuses,
        "sent_any": statuses.get("sent", 0) > 0,
    }


def summarize_gossip(data_dir: Path, min_gossip_nodes: int) -> dict[str, Any]:
    rows = read_rows(data_dir / "gossip_metrics.csv")
    counts = int_series(rows, "gossip_nodes")
    below = [value for value in counts if min_gossip_nodes > 0 and value < min_gossip_nodes]
    return {
        "samples": len(counts),
        "gossip_nodes_min": min(counts) if counts else None,
        "gossip_nodes_max": max(counts) if counts else None,
        "gossip_nodes_last": counts[-1] if counts else None,
        "below_min_gossip_samples": len(below),
        "all_gossip_samples_ok": min_gossip_nodes <= 0 or (bool(counts) and not below),
        "min_gossip_nodes": min_gossip_nodes,
    }


def summarize_memory(data_dir: Path) -> dict[str, Any]:
    rows = read_rows(data_dir / "memory_latest_by_type.csv")
    by_type = {row.get("node_type", ""): row for row in rows}
    solana = by_type.get("solana_validator", {})
    bootstrap = by_type.get("solana_bootstrap", {})
    return {
        "samples": len(rows),
        "solana_validator_containers": to_int(solana.get("containers")),
        "solana_validator_avg_bytes": to_int(solana.get("mem_avg_bytes")),
        "solana_validator_p95_bytes": to_int(solana.get("mem_p95_bytes")),
        "solana_validator_max_bytes": to_int(solana.get("mem_max_bytes")),
        "solana_bootstrap_containers": to_int(bootstrap.get("containers")),
        "solana_bootstrap_avg_bytes": to_int(bootstrap.get("mem_avg_bytes")),
        "has_solana_memory": bool(solana or bootstrap),
    }


def summarize_host_memory(data_dir: Path) -> dict[str, Any]:
    rows = read_rows(data_dir / "host_memory.csv")
    available = int_series(rows, "mem_available_kib")
    swap_free = int_series(rows, "swap_free_kib")
    load1 = float_series(rows, "load1")
    return {
        "samples": len(rows),
        "mem_available_min_kib": min(available) if available else None,
        "mem_available_last_kib": available[-1] if available else None,
        "swap_free_min_kib": min(swap_free) if swap_free else None,
        "swap_free_last_kib": swap_free[-1] if swap_free else None,
        "load1_max": max(load1) if load1 else None,
        "load1_last": load1[-1] if load1 else None,
    }


def summarize_duration(data_dir: Path, env: dict[str, str]) -> dict[str, Any]:
    target = to_float(env.get("duration_seconds"))
    interval_values = [
        to_float(env.get("memory_interval")),
        to_float(env.get("block_interval")),
        to_float(env.get("active_interval")),
        to_float(env.get("gossip_interval")),
    ]
    max_interval = max([value for value in interval_values if value is not None] or [0.0])

    elapsed_values: list[float] = []
    for filename in (
        "active_validators.csv",
        "block_metrics.csv",
        "memory_by_type.csv",
        "gossip_metrics.csv",
    ):
        elapsed_values.extend(float_series(read_rows(data_dir / filename), "elapsed_sec"))

    max_elapsed = max(elapsed_values) if elapsed_values else None
    tolerance = max(300.0, 2.0 * max_interval)
    required = max(0.0, target - tolerance) if target is not None else None
    covered = (
        target is None
        or target <= 0
        or (max_elapsed is not None and required is not None and max_elapsed >= required)
    )
    return {
        "target_seconds": target,
        "max_elapsed_seconds": max_elapsed,
        "tolerance_seconds": tolerance,
        "required_elapsed_seconds": required,
        "covered_target_duration": covered,
    }


def summarize_run_log(data_dir: Path) -> dict[str, Any]:
    run_dir = data_dir.parent
    log_path = run_dir / "run.log"
    text = log_path.read_text(errors="ignore") if log_path.exists() else ""
    return {
        "run_log": str(log_path) if log_path.exists() else None,
        "completed": "large Solana experiment completed" in text,
        "failed": "large Solana experiment failed" in text,
    }


def build_summary(data_dir: Path, expected: int, min_active: int, min_gossip_nodes: int) -> dict[str, Any]:
    env = read_key_values(data_dir / "experiment.env")
    if expected <= 0:
        expected = to_int(env.get("validators")) or to_int(env.get("VALIDATORS")) or 0
    if min_active <= 0:
        min_active = to_int(env.get("min_active_solana")) or expected
    if min_gossip_nodes <= 0:
        min_gossip_nodes = to_int(env.get("min_gossip_nodes")) or 0

    summary: dict[str, Any] = {
        "data_dir": str(data_dir),
        "run_dir": str(data_dir.parent),
        "expected_validators": expected,
        "min_active": min_active,
        "final_health_passed": (data_dir / "final-health-passed.log").exists(),
        "final_settle_health_passed": (data_dir / "final-settle-health-passed.log").exists(),
        "stall_detected": (data_dir / "stall-detected.log").exists(),
        "active_stall_detected": (data_dir / "active-stall-detected.log").exists(),
        "host_memory_watchdog_triggered": any(data_dir.glob("host-memory-watchdog-triggered.*")),
        "run_log": summarize_run_log(data_dir),
        "active": summarize_active(data_dir, expected, min_active),
        "blocks": summarize_blocks(data_dir),
        "tx_load": summarize_tx_load(data_dir),
        "gossip": summarize_gossip(data_dir, min_gossip_nodes),
        "memory": summarize_memory(data_dir),
        "host_memory": summarize_host_memory(data_dir),
        "duration": summarize_duration(data_dir, env),
    }

    checks = {
        "run_not_failed": not summary["run_log"]["failed"],
        "final_health_passed": summary["final_health_passed"],
        "active_samples_present": summary["active"]["samples"] > 0,
        "all_active_samples_ok": summary["active"]["all_active_samples_ok"],
        "slot_progressed": summary["blocks"]["slot_progressed"],
        "transactions_sent": summary["tx_load"]["sent_any"],
        "transactions_recorded": summary["blocks"]["transactions_recorded"],
        "gossip_samples_ok": summary["gossip"]["all_gossip_samples_ok"],
        "memory_collected": summary["memory"]["has_solana_memory"],
        "duration_covered": summary["duration"]["covered_target_duration"],
        "no_slot_stall": not summary["stall_detected"],
        "no_active_stall": not summary["active_stall_detected"],
        "no_host_memory_watchdog": not summary["host_memory_watchdog_triggered"],
    }
    summary["checks"] = checks
    summary["status"] = "pass" if all(checks.values()) else "fail"
    return summary


def bytes_to_gib(value: Optional[int]) -> str:
    if value is None:
        return "n/a"
    return f"{value / (1024 ** 3):.2f} GiB"


def kib_to_gib(value: Optional[int]) -> str:
    if value is None:
        return "n/a"
    return f"{value / (1024 ** 2):.2f} GiB"


def render_text(summary: dict[str, Any]) -> str:
    active = summary["active"]
    blocks = summary["blocks"]
    tx = summary["tx_load"]
    gossip = summary["gossip"]
    memory = summary["memory"]
    host_memory = summary["host_memory"]
    duration = summary["duration"]
    checks = summary["checks"]
    lines = [
        f"status={summary['status']}",
        f"data_dir={summary['data_dir']}",
        f"expected_validators={summary['expected_validators']}",
        f"min_active={summary['min_active']}",
        (
            "active_samples={samples} active_min={active_min} active_max={active_max} "
            "active_last={active_last} below_min_samples={below_min_active_samples}"
        ).format(**active),
        (
            "running_min={running_min} running_max={running_max} running_last={running_last}"
        ).format(**active),
        (
            "slot_start={slot_start} slot_end={slot_end} slot_delta={slot_delta} "
            "tx_count_delta={transaction_count_delta}"
        ).format(**blocks),
        (
            "voting_validators_min={voting_validators_min} voting_validators_max={voting_validators_max} "
            "voting_validators_last={voting_validators_last} delinquent_validators_max={delinquent_validators_max}"
        ).format(**blocks),
        f"tx_sender_sent={tx['sent']} tx_sender_failed={tx['failed']}",
        (
            "gossip_samples={samples} gossip_min={gossip_nodes_min} "
            "gossip_max={gossip_nodes_max} gossip_last={gossip_nodes_last} "
            "below_min_gossip_samples={below_min_gossip_samples}"
        ).format(**gossip),
        (
            "solana_validator_containers={containers} avg_mem={avg} p95_mem={p95} max_mem={max_mem}"
        ).format(
            containers=memory["solana_validator_containers"],
            avg=bytes_to_gib(memory["solana_validator_avg_bytes"]),
            p95=bytes_to_gib(memory["solana_validator_p95_bytes"]),
            max_mem=bytes_to_gib(memory["solana_validator_max_bytes"]),
        ),
        f"bootstrap_avg_mem={bytes_to_gib(memory['solana_bootstrap_avg_bytes'])}",
        (
            "host_mem_available_min={available_min} host_mem_available_last={available_last} "
            "host_swap_free_min={swap_min} host_swap_free_last={swap_last} "
            "host_load1_max={load_max} host_load1_last={load_last}"
        ).format(
            available_min=kib_to_gib(host_memory["mem_available_min_kib"]),
            available_last=kib_to_gib(host_memory["mem_available_last_kib"]),
            swap_min=kib_to_gib(host_memory["swap_free_min_kib"]),
            swap_last=kib_to_gib(host_memory["swap_free_last_kib"]),
            load_max=host_memory["load1_max"],
            load_last=host_memory["load1_last"],
        ),
        f"host_memory_watchdog_triggered={summary['host_memory_watchdog_triggered']}",
        (
            "duration_target={target_seconds} duration_max_elapsed={max_elapsed_seconds} "
            "duration_required_elapsed={required_elapsed_seconds}"
        ).format(**duration),
        "checks:",
    ]
    lines.extend(f"  {name}={value}" for name, value in checks.items())
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    data_dir = resolve_data_dir(args.path)
    summary = build_summary(data_dir, args.expected_validators, args.min_active, args.min_gossip_nodes)

    output_json = args.output_json or data_dir / "experiment_summary.json"
    output_text = args.output_text or data_dir / "experiment_summary.txt"
    output_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    output_text.write_text(render_text(summary))
    print(render_text(summary), end="")

    if args.fail_on_fail and summary["status"] != "pass":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
