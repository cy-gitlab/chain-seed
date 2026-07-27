#!/usr/bin/env python3
"""Apply fixed Geth cache, Go heap, and cgroup memory budgets."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - depends on local environment
    raise SystemExit("PyYAML is required to patch docker-compose.yml") from exc


GETH_COMMAND = re.compile(r"(?m)^(?P<prefix>[ \t]*geth[ \t]+)(?P<body>[^\r\n]+)$")
CACHE_FLAG = re.compile(r"(?<!\S)--cache(?:=|[ \t]+)(?P<value>[0-9]+)(?=\s|$)")
START_SCRIPT_COPY = re.compile(
    r"(?m)^COPY[ \t]+(?P<source>[^ \t]+)[ \t]+/start[.]sh[ \t]*$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-mb", type=int, default=0)
    parser.add_argument("--go-mem-limit-mb", type=int, default=0)
    parser.add_argument("--container-memory-mb", type=int, default=0)
    parser.add_argument("--expected-nodes", type=int, default=0)
    return parser.parse_args()


def patch_command(command: str, cache_mb: int) -> tuple[str, str]:
    if cache_mb == 0:
        return command, "unchanged"
    matches = list(CACHE_FLAG.finditer(command))
    if len(matches) > 1:
        raise ValueError("Geth command contains multiple --cache flags")
    if not matches:
        return command.replace("geth ", f"geth --cache={cache_mb} ", 1), "added"

    match = matches[0]
    current = int(match.group("value"))
    if current == cache_mb:
        return command, "unchanged"
    updated = command[: match.start()] + f"--cache={cache_mb}" + command[match.end() :]
    return updated, "updated"


def _is_geth_service(service: Any) -> bool:
    if not isinstance(service, dict):
        return False
    labels = service.get("labels", {})
    if isinstance(labels, dict):
        values = labels.values()
    elif isinstance(labels, list):
        values = labels
    else:
        values = []
    return any("Ethereum-POS-Geth" in str(value) for value in values)


def _set_environment(service: dict[str, Any], key: str, value: str) -> None:
    environment = service.get("environment")
    if environment is None:
        service["environment"] = [f"{key}={value}"]
        return
    if isinstance(environment, dict):
        environment[key] = value
        return
    if not isinstance(environment, list):
        raise SystemExit(f"unsupported environment format: {environment!r}")

    prefix = f"{key}="
    for index, item in enumerate(environment):
        if isinstance(item, str) and (item == key or item.startswith(prefix)):
            environment[index] = f"{key}={value}"
            return
    environment.append(f"{key}={value}")


def _patch_compose(
    compose_path: Path,
    go_mem_limit_mb: int,
    container_memory_mb: int,
) -> tuple[int, int, int]:
    with compose_path.open() as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise SystemExit(f"invalid compose file: {compose_path}")

    services = data["services"]
    matched = 0
    go_limited = 0
    cgroup_limited = 0
    for service in services.values():
        if not _is_geth_service(service):
            continue
        matched += 1
        if go_mem_limit_mb:
            _set_environment(service, "GOMEMLIMIT", f"{go_mem_limit_mb}MiB")
            go_limited += 1
        if container_memory_mb:
            service["mem_limit"] = f"{container_memory_mb}m"
            service["memswap_limit"] = f"{container_memory_mb}m"
            service["mem_swappiness"] = 0
            cgroup_limited += 1

    with compose_path.open("w") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, default_flow_style=False)
    return matched, go_limited, cgroup_limited


def main() -> int:
    args = parse_args()
    if args.cache_mb and args.cache_mb < 32:
        raise SystemExit("--cache-mb must be 0 or at least 32")
    if args.go_mem_limit_mb and args.go_mem_limit_mb < 64:
        raise SystemExit("--go-mem-limit-mb must be 0 or at least 64")
    if args.container_memory_mb and args.container_memory_mb < 128:
        raise SystemExit("--container-memory-mb must be 0 or at least 128")
    if (
        args.go_mem_limit_mb
        and args.container_memory_mb
        and args.go_mem_limit_mb >= args.container_memory_mb
    ):
        raise SystemExit("--go-mem-limit-mb must be smaller than --container-memory-mb")
    if not any((args.cache_mb, args.go_mem_limit_mb, args.container_memory_mb)):
        raise SystemExit("at least one Geth memory budget must be non-zero")
    if args.expected_nodes < 0:
        raise SystemExit("--expected-nodes must be non-negative")
    if not args.output.is_dir():
        raise SystemExit(f"output directory does not exist: {args.output}")
    compose_path = args.output / "docker-compose.yml"
    if not compose_path.is_file():
        raise SystemExit(f"compose file does not exist: {compose_path}")

    commands = 0
    changed_files = 0
    added = 0
    updated = 0
    unchanged = 0
    matched_files: list[str] = []

    for node_dir in sorted(args.output.glob("hnode_*")):
        if not node_dir.is_dir():
            continue
        dockerfile = node_dir / "Dockerfile"
        if not dockerfile.is_file():
            continue
        copy_match = START_SCRIPT_COPY.search(dockerfile.read_text())
        if not copy_match:
            continue
        path = node_dir / copy_match.group("source")
        if not path.is_file():
            raise SystemExit(f"Dockerfile references a missing start script: {path}")
        content = path.read_text()
        file_commands = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal commands, file_commands, added, updated, unchanged
            command, action = patch_command(match.group(0), args.cache_mb)
            commands += 1
            file_commands += 1
            if action == "added":
                added += 1
            elif action == "updated":
                updated += 1
            else:
                unchanged += 1
            return command

        patched = GETH_COMMAND.sub(replace, content)
        if file_commands == 0:
            continue
        if file_commands != 1:
            raise SystemExit(f"expected one Geth command in {path}, found {file_commands}")
        matched_files.append(str(path.relative_to(args.output)))
        if patched != content:
            path.write_text(patched)
            changed_files += 1

    if commands == 0:
        raise SystemExit(f"no Geth start commands found under {args.output}")
    if args.expected_nodes and commands != args.expected_nodes:
        raise SystemExit(
            f"expected {args.expected_nodes} Geth commands, found {commands}; "
            "refusing a partial cache patch"
        )

    compose_services, go_limited_services, cgroup_limited_services = _patch_compose(
        compose_path,
        args.go_mem_limit_mb,
        args.container_memory_mb,
    )
    if args.expected_nodes and compose_services != args.expected_nodes:
        raise SystemExit(
            f"expected {args.expected_nodes} Geth compose services, found "
            f"{compose_services}; refusing a partial memory patch"
        )

    summary = {
        "cache_mb": args.cache_mb,
        "go_mem_limit_mb": args.go_mem_limit_mb,
        "container_memory_mb": args.container_memory_mb,
        "expected_nodes": args.expected_nodes,
        "commands": commands,
        "compose_services": compose_services,
        "go_limited_services": go_limited_services,
        "cgroup_limited_services": cgroup_limited_services,
        "changed_files": changed_files,
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "matched_files": matched_files,
    }
    summary_path = args.output / "geth-memory-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({**summary, "summary": str(summary_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
