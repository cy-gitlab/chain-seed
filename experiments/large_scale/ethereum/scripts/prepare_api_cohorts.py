#!/usr/bin/env python3
"""Create deterministic paired target/control Beacon cohorts for API experiments."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
ETHEREUM_DIR = SCRIPT_DIR.parent
DEFAULT_COMPOSE_FILE = ETHEREUM_DIR / "output" / "docker-compose.yml"


@dataclass(frozen=True)
class BeaconNode:
    index: int
    service: str
    name: str
    ip: str
    asn: str

    @property
    def url(self) -> str:
        return f"http://{self.ip}:8000"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=30)
    parser.add_argument("--control-count", type=int, default=30)
    parser.add_argument("--monitor-per-role", type=int, default=8)
    parser.add_argument("--metrics-port", type=int, default=5054)
    return parser.parse_args()


def compose_service_blocks(compose_file: Path) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current: Optional[str] = None
    in_services = False

    for raw_line in compose_file.read_text().splitlines():
        if raw_line == "services:":
            in_services = True
            continue
        if not in_services:
            continue
        if raw_line and not raw_line.startswith(" "):
            break
        match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", raw_line)
        if match:
            current = match.group(1)
            blocks[current] = []
            continue
        if current is not None:
            blocks[current].append(raw_line)

    return {name: "\n".join(lines) for name, lines in blocks.items()}


def discover_beacons(compose_file: Path) -> list[BeaconNode]:
    beacons: list[BeaconNode] = []
    for service, block in compose_service_blocks(compose_file).items():
        if "Ethereum-POS-Beacon-" not in block or "Ethereum-POS-BeaconSetup" in block:
            continue
        display = re.search(
            r"org\.seedsecuritylabs\.seedemu\.meta\.displayname:\s*"
            r"(Ethereum-POS-Beacon-(\d+))",
            block,
        )
        ip = re.search(r"ipv4_address:\s*([0-9.]+)", block)
        asn = re.search(r"org\.seedsecuritylabs\.seedemu\.meta\.asn:\s*'?([0-9]+)'?", block)
        container = re.search(r"container_name:\s*(\S+)", block)
        if display is None or ip is None:
            continue
        beacons.append(
            BeaconNode(
                index=int(display.group(2)),
                service=service,
                name=container.group(1) if container else display.group(1),
                ip=ip.group(1),
                asn=asn.group(1) if asn else "",
            )
        )
    return sorted(beacons, key=lambda node: node.index)


def evenly_spaced_indices(size: int, count: int) -> set[int]:
    if count <= 0:
        return set()
    if count >= size:
        return set(range(size))
    if count == 1:
        return {0}
    return {round(i * (size - 1) / (count - 1)) for i in range(count)}


def main() -> int:
    args = parse_args()
    if args.target_count < 1 or args.control_count < 1:
        raise SystemExit("target and control counts must be positive")
    if args.target_count != args.control_count:
        raise SystemExit("paired design requires --target-count == --control-count")
    if args.monitor_per_role < 1:
        raise SystemExit("--monitor-per-role must be positive")
    if not 1 <= args.metrics_port <= 65535:
        raise SystemExit("--metrics-port must be between 1 and 65535")

    beacons = discover_beacons(args.compose_file)
    pair_count = args.target_count
    if len(beacons) < pair_count * 2:
        raise SystemExit(
            f"need at least {pair_count * 2} Beacon nodes for {pair_count} pairs; "
            f"found {len(beacons)}"
        )

    by_asn: dict[str, list[BeaconNode]] = defaultdict(list)
    for beacon in beacons:
        by_asn[beacon.asn].append(beacon)
    eligible_asns = sorted(
        (asn for asn, nodes in by_asn.items() if len(nodes) >= 2),
        key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
    )
    if len(eligible_asns) < pair_count:
        raise SystemExit(
            f"need {pair_count} ASes with at least two Beacon nodes; found {len(eligible_asns)}"
        )

    selected_asn_positions = sorted(evenly_spaced_indices(len(eligible_asns), pair_count))
    selected_asns = [eligible_asns[position] for position in selected_asn_positions]
    monitor_pairs = evenly_spaced_indices(pair_count, min(args.monitor_per_role, pair_count))
    rows: list[dict[str, object]] = []
    controls: list[BeaconNode] = []
    targets: list[BeaconNode] = []

    # Pair nodes inside one AS so both roles share the same emulated network location.
    for pair_id, asn in enumerate(selected_asns):
        control, target = sorted(by_asn[asn], key=lambda node: node.index)[:2]
        controls.append(control)
        targets.append(target)
        for role, node in (("control", control), ("target", target)):
            rows.append(
                {
                    "role": role,
                    "pair_id": pair_id,
                    "monitor": int(pair_id in monitor_pairs),
                    "index": node.index,
                    "service": node.service,
                    "name": node.name,
                    "ip": node.ip,
                    "asn": node.asn,
                    "url": node.url,
                    "metrics_url": f"http://{node.ip}:{args.metrics_port}/metrics",
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cohort_path = args.output_dir / "api_cohorts.csv"
    with cohort_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    (args.output_dir / "api_target_beacon_indices.txt").write_text(
        "\n".join(str(node.index) for node in targets) + "\n"
    )
    (args.output_dir / "api_control_beacon_indices.txt").write_text(
        "\n".join(str(node.index) for node in controls) + "\n"
    )
    monitored = [row for row in rows if row["monitor"] == 1]
    (args.output_dir / "api_monitored_beacon_indices.txt").write_text(
        "\n".join(str(row["index"]) for row in monitored) + "\n"
    )
    selected_indices = {node.index for node in controls + targets}
    reference = next(
        (beacon for beacon in beacons if beacon.index not in selected_indices),
        controls[0],
    )
    (args.output_dir / "api_reference_beacon_index.txt").write_text(f"{reference.index}\n")

    summary = {
        "compose_file": str(args.compose_file.resolve()),
        "beacons_discovered": len(beacons),
        "eligible_asns": len(eligible_asns),
        "target_count": len(targets),
        "control_count": len(controls),
        "monitor_per_role": len(monitor_pairs),
        "metrics_port": args.metrics_port,
        "reference_beacon_index": reference.index,
        "targets": [asdict(node) for node in targets],
        "controls": [asdict(node) for node in controls],
    }
    (args.output_dir / "api_cohorts.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key not in {"targets", "controls"}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
