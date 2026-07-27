#!/usr/bin/env python3
"""Summarize single-variable memory effects from a D61 experiment run."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


SUMMARY_FIELDS = [
    "variant",
    "beacons",
    "historic_states",
    "cache_profile",
    "beacon_peers",
    "beacon_bootnode_peers",
    "beacon_bootnodes",
    "beacon_mem_avg_gib_steady",
    "beacon_mem_p95_gib_steady",
    "beacon_mem_avg_gib_last",
    "saved_gib_per_beacon_vs_baseline",
    "saved_pct_vs_baseline",
    "saved_gib_total_vs_baseline",
    "blocks_seen",
    "non_empty_blocks",
    "tx_sent",
    "tx_failed",
    "health_passed",
    "final_head_slot",
    "final_justified_epoch",
    "final_finalized_epoch",
    "observed_head_slot_gap_count",
]


def read_summary(run_dir: Path) -> List[Dict[str, str]]:
    path = run_dir / "summary.csv"
    if not path.exists():
        raise FileNotFoundError(f"missing summary.csv: {path}")
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def as_float(value: str) -> float:
    return float(value) if value not in ("", None) else 0.0


def as_int(value: str) -> int:
    return int(value) if value not in ("", None) else 0


def iter_block_metrics(path: Path) -> Iterable[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def summarize_finality(run_dir: Path, variant: str) -> Tuple[str, str, str, int]:
    rows = list(iter_block_metrics(run_dir / variant / "block_metrics.csv"))
    head_slots: List[int] = []
    final_head = ""
    final_justified = ""
    final_finalized = ""

    for row in rows:
        head = row.get("head_slot") or ""
        justified = row.get("justified_epoch") or ""
        finalized = row.get("finalized_epoch") or ""
        if head:
            final_head = head
            try:
                slot = int(head)
            except ValueError:
                continue
            if not head_slots or slot != head_slots[-1]:
                head_slots.append(slot)
        if justified:
            final_justified = justified
        if finalized:
            final_finalized = finalized

    gap_count = 0
    for previous, current in zip(head_slots, head_slots[1:]):
        if current > previous:
            gap_count += max(0, current - previous - 1)

    return final_head, final_justified, final_finalized, gap_count


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_md(path: Path, rows: List[Dict[str, str]]) -> None:
    cols = [
        "variant",
        "beacon_mem_avg_gib_steady",
        "saved_pct_vs_baseline",
        "saved_gib_total_vs_baseline",
        "blocks_seen",
        "non_empty_blocks",
        "tx_failed",
        "final_finalized_epoch",
        "observed_head_slot_gap_count",
    ]
    lines = ["# D61 Large Single-Variable Effects", ""]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
    path.write_text("\n".join(lines) + "\n")


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: {Path(argv[0]).name} RUN_DIR", file=sys.stderr)
        return 2

    run_dir = Path(argv[1]).resolve()
    rows = read_summary(run_dir)
    baseline = next((row for row in rows if row.get("variant") == "baseline_all"), None)
    if baseline is None:
        raise RuntimeError("summary.csv does not contain baseline_all")

    baseline_avg = as_float(baseline["beacon_mem_avg_gib_steady"])
    output_rows: List[Dict[str, str]] = []

    for row in rows:
        variant = row["variant"]
        avg = as_float(row["beacon_mem_avg_gib_steady"])
        beacons = as_int(row["beacons"])
        saved = baseline_avg - avg
        saved_pct = (saved / baseline_avg * 100.0) if baseline_avg else 0.0
        final_head, final_justified, final_finalized, gap_count = summarize_finality(run_dir, variant)

        output_rows.append(
            {
                "variant": variant,
                "beacons": row["beacons"],
                "historic_states": row["historic_states"],
                "cache_profile": row["cache_profile"],
                "beacon_peers": row["beacon_peers"],
                "beacon_bootnode_peers": row["beacon_bootnode_peers"],
                "beacon_bootnodes": row["beacon_bootnodes"],
                "beacon_mem_avg_gib_steady": row["beacon_mem_avg_gib_steady"],
                "beacon_mem_p95_gib_steady": row["beacon_mem_p95_gib_steady"],
                "beacon_mem_avg_gib_last": row["beacon_mem_avg_gib_last"],
                "saved_gib_per_beacon_vs_baseline": f"{saved:.4f}",
                "saved_pct_vs_baseline": f"{saved_pct:.2f}",
                "saved_gib_total_vs_baseline": f"{saved * beacons:.2f}",
                "blocks_seen": row["blocks_seen"],
                "non_empty_blocks": row["non_empty_blocks"],
                "tx_sent": row["tx_sent"],
                "tx_failed": row["tx_failed"],
                "health_passed": row["health_passed"],
                "final_head_slot": final_head,
                "final_justified_epoch": final_justified,
                "final_finalized_epoch": final_finalized,
                "observed_head_slot_gap_count": str(gap_count),
            }
        )

    write_csv(run_dir / "single_variable_effects.csv", output_rows)
    write_md(run_dir / "single_variable_effects.md", output_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
