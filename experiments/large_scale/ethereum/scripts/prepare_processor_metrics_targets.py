#!/usr/bin/env python3
"""Select deterministic, AS-diverse Beacon nodes for processor metrics."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from prepare_api_cohorts import (
    DEFAULT_COMPOSE_FILE,
    BeaconNode,
    discover_beacons,
    evenly_spaced_indices,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--sample", type=int, default=32)
    parser.add_argument("--metrics-port", type=int, default=5054)
    return parser.parse_args()


def select_as_diverse(beacons: list[BeaconNode], count: int) -> list[BeaconNode]:
    by_asn: dict[str, list[BeaconNode]] = defaultdict(list)
    for beacon in beacons:
        by_asn[beacon.asn].append(beacon)
    ordered_asns = sorted(
        by_asn,
        key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
    )
    selected: list[BeaconNode] = []
    round_index = 0
    while len(selected) < min(count, len(beacons)):
        eligible = [asn for asn in ordered_asns if round_index < len(by_asn[asn])]
        if not eligible:
            break
        remaining = count - len(selected)
        positions = sorted(evenly_spaced_indices(len(eligible), min(remaining, len(eligible))))
        selected.extend(by_asn[eligible[position]][round_index] for position in positions)
        round_index += 1
    return sorted(selected, key=lambda node: node.index)


def main() -> int:
    args = parse_args()
    if args.sample < 1:
        raise SystemExit("--sample must be positive")
    if not 1 <= args.metrics_port <= 65535:
        raise SystemExit("--metrics-port must be between 1 and 65535")
    beacons = discover_beacons(args.compose_file)
    if len(beacons) < args.sample:
        raise SystemExit(f"requested {args.sample} Beacon nodes, found {len(beacons)}")
    selected = select_as_diverse(beacons, args.sample)
    rows = [
        {
            "role": "sample",
            "pair_id": position,
            "monitor": 1,
            "index": node.index,
            "service": node.service,
            "name": node.name,
            "ip": node.ip,
            "asn": node.asn,
            "url": node.url,
            "metrics_url": f"http://{node.ip}:{args.metrics_port}/metrics",
        }
        for position, node in enumerate(selected)
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "compose_file": str(args.compose_file.resolve()),
        "beacons_discovered": len(beacons),
        "sampled_beacons": len(selected),
        "sampled_asns": len({node.asn for node in selected}),
        "metrics_port": args.metrics_port,
        "indices": [node.index for node in selected],
    }
    args.output.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
