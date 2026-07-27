#!/usr/bin/env python3
"""Restart D61 Ethereum containers whose client process fell back to a shell."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROLE_PATTERN = re.compile(r"Ethereum-POS-(?:Geth|Beacon|Validator)-\d+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-raw", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run(args: list[str]) -> str:
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args)} failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout


def candidate_ids(path: Path) -> list[str]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return []
    latest_sample = max(int(row["sample"]) for row in rows)
    return sorted(
        {
            row["container_id"]
            for row in rows
            if int(row["sample"]) == latest_sample
            and row["node_type"] == "other"
            and "/start.sh" in row["main_cmd"]
        }
    )


def inspect_services(container_ids: list[str]) -> list[dict[str, str]]:
    if not container_ids:
        return []
    payload: list[dict[str, Any]] = json.loads(run(["docker", "inspect", *container_ids]))
    services: list[dict[str, str]] = []
    for item in payload:
        labels = item.get("Config", {}).get("Labels", {}) or {}
        display_name = str(
            labels.get("org.seedsecuritylabs.seedemu.meta.displayname", "")
        )
        service = str(labels.get("com.docker.compose.service", ""))
        if service and ROLE_PATTERN.fullmatch(display_name):
            services.append(
                {
                    "container_id": str(item.get("Id", ""))[:12],
                    "display_name": display_name,
                    "service": service,
                }
            )
    return sorted(services, key=lambda item: item["service"])


def main() -> int:
    args = parse_args()
    services = inspect_services(candidate_ids(args.memory_raw))
    result = {
        "compose_file": str(args.compose_file.resolve()),
        "restart_count": len(services),
        "services": services,
        "dry_run": args.dry_run,
    }
    print(json.dumps(result, indent=2))
    if services and not args.dry_run:
        run(
            [
                "docker",
                "compose",
                "-f",
                str(args.compose_file),
                "restart",
                *[item["service"] for item in services],
            ]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
