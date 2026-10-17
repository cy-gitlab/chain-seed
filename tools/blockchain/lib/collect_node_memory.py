#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set


DEFAULT_COMPOSE_FILE = Path("output/docker-compose.yml")
DEFAULT_OUTPUT_DIR = Path("results")

RAW_HEADER = [
    "sample",
    "timestamp",
    "elapsed_sec",
    "container_id",
    "container_name",
    "compose_service",
    "node_type",
    "display_name",
    "asn",
    "eth_role",
    "main_cmd",
    "mem_usage_bytes",
    "mem_limit_bytes",
    "mem_percent",
    "cpu_percent",
    "pids",
    "mem_anon_bytes",
    "mem_file_bytes",
    "mem_kernel_bytes",
    "mem_shmem_bytes",
    "mem_sock_bytes",
    "memory_oom",
    "memory_oom_kill",
]

TYPE_HEADER = [
    "sample",
    "timestamp",
    "elapsed_sec",
    "node_type",
    "containers",
    "mem_total_bytes",
    "mem_avg_bytes",
    "mem_min_bytes",
    "mem_p50_bytes",
    "mem_p95_bytes",
    "mem_max_bytes",
    "mem_limit_total_bytes",
    "cpu_avg_percent",
    "pids_total",
    "mem_anon_total_bytes",
    "mem_file_total_bytes",
    "mem_kernel_total_bytes",
    "mem_shmem_total_bytes",
    "mem_sock_total_bytes",
    "memory_oom_total",
    "memory_oom_kill_total",
]


@dataclass
class ContainerInfo:
    container_id: str
    name: str
    labels: Dict[str, str]
    pid: int
    cgroup_path: str
    cmdline: str


@dataclass
class StatRow:
    container_id: str
    name: str
    mem_usage_bytes: int
    mem_limit_bytes: int
    mem_percent: float
    cpu_percent: float
    cpu_usage_usec: int
    pids: int
    mem_anon_bytes: int = 0
    mem_file_bytes: int = 0
    mem_kernel_bytes: int = 0
    mem_shmem_bytes: int = 0
    mem_sock_bytes: int = 0
    memory_oom: int = 0
    memory_oom_kill: int = 0


def run_cmd(args: Sequence[str]) -> str:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"{' '.join(args)} failed: {stderr}")
    return result.stdout


def parse_size_to_bytes(value: str) -> int:
    value = value.strip()
    if not value:
        return 0

    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?$", value)
    if not match:
        return 0

    number = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    units = {
        "b": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    return int(number * units.get(unit, 1))


def parse_percent(value: str) -> float:
    value = value.strip().rstrip("%")
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def parse_int(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        return 0


def load_compose_container_names(compose_file: Path) -> Set[str]:
    if not compose_file.exists():
        return set()

    names: Set[str] = set()
    pattern = re.compile(r"^\s*container_name:\s*(.+?)\s*$")
    for line in compose_file.read_text().splitlines():
        match = pattern.match(line)
        if not match:
            continue
        name = match.group(1).strip().strip('"').strip("'")
        if name:
            names.add(name)
    return names


def docker_ps_ids() -> List[str]:
    output = run_cmd(["docker", "ps", "--format", "{{.ID}}"])
    return [line.strip() for line in output.splitlines() if line.strip()]


def chunks(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def inspect_containers(container_ids: Sequence[str]) -> Dict[str, ContainerInfo]:
    infos: Dict[str, ContainerInfo] = {}
    inspect_format = "{{.Id}}\t{{.Name}}\t{{.State.Pid}}\t{{json .Config.Labels}}"
    for batch in chunks(list(container_ids), 300):
        output = run_cmd(["docker", "inspect", "--format", inspect_format, *batch])
        for line in output.splitlines():
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            full_id, raw_name, raw_pid, raw_labels = parts
            name = raw_name.lstrip("/")
            try:
                pid = int(raw_pid)
            except ValueError:
                pid = 0
            try:
                labels = json.loads(raw_labels) if raw_labels and raw_labels != "null" else {}
            except json.JSONDecodeError:
                labels = {}
            if not full_id or not name:
                continue
            info = ContainerInfo(
                container_id=full_id,
                name=name,
                labels=labels,
                pid=pid,
                cgroup_path=cgroup_path_for_pid(pid),
                cmdline="",
            )
            infos[full_id] = info
            infos[full_id[:12]] = info
            infos[name] = info
    return infos


def read_first_pid(cgroup_path: str) -> int:
    try:
        value = (Path("/sys/fs/cgroup") / cgroup_path / "cgroup.procs").read_text().splitlines()[0]
        return int(value)
    except (OSError, IndexError, ValueError):
        return 0


def read_cmdline(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def read_cgroup_cmdlines(cgroup_path: str, max_pids: int = 40) -> str:
    try:
        pids = (Path("/sys/fs/cgroup") / cgroup_path / "cgroup.procs").read_text().splitlines()
    except OSError:
        return ""

    cmdlines: List[str] = []
    for raw_pid in pids[:max_pids]:
        try:
            pid = int(raw_pid)
        except ValueError:
            continue
        cmdline = read_cmdline(pid)
        if cmdline:
            cmdlines.append(cmdline)
    return " || ".join(cmdlines)


def docker_id_from_cgroup_name(name: str) -> str:
    if name.startswith("docker-") and name.endswith(".scope"):
        return name[len("docker-") : -len(".scope")]
    return name


def scan_cgroup_containers() -> Dict[str, ContainerInfo]:
    infos: Dict[str, ContainerInfo] = {}
    roots = [
        Path("/sys/fs/cgroup/system.slice"),
        Path("/sys/fs/cgroup/docker"),
        Path("/sys/fs/cgroup"),
    ]
    candidates: List[Path] = []

    for root in roots:
        if not root.exists():
            continue
        candidates.extend(root.glob("docker-*.scope"))
        candidates.extend(path for path in root.iterdir() if path.is_dir() and re.fullmatch(r"[0-9a-f]{64}", path.name))

    seen: Set[str] = set()
    for path in candidates:
        memory_file = path / "memory.current"
        if not memory_file.exists():
            continue
        container_id = docker_id_from_cgroup_name(path.name)
        if container_id in seen:
            continue
        seen.add(container_id)
        cgroup_path = str(path.relative_to("/sys/fs/cgroup"))
        pid = read_first_pid(cgroup_path)
        cmdline = read_cgroup_cmdlines(cgroup_path)
        name = container_id[:12]
        info = ContainerInfo(
            container_id=container_id,
            name=name,
            labels={},
            pid=pid,
            cgroup_path=cgroup_path,
            cmdline=cmdline,
        )
        infos[container_id] = info
        infos[container_id[:12]] = info
        infos[name] = info
    return infos


def enrich_cgroup_infos(
    scanned: Dict[str, ContainerInfo],
    metadata: Dict[str, ContainerInfo],
) -> Dict[str, ContainerInfo]:
    """Attach a one-time Docker identity snapshot to live cgroup data."""
    infos: Dict[str, ContainerInfo] = {}
    seen: Set[str] = set()
    for scanned_info in scanned.values():
        if scanned_info.container_id in seen:
            continue
        seen.add(scanned_info.container_id)
        metadata_info = metadata.get(scanned_info.container_id)
        if metadata_info is None:
            info = scanned_info
        else:
            info = ContainerInfo(
                container_id=scanned_info.container_id,
                name=metadata_info.name,
                labels=metadata_info.labels,
                pid=scanned_info.pid,
                cgroup_path=scanned_info.cgroup_path,
                cmdline=scanned_info.cmdline,
            )
        infos[info.container_id] = info
        infos[info.container_id[:12]] = info
        infos[info.name] = info
    return infos


def cgroup_path_for_pid(pid: int) -> str:
    if pid <= 0:
        return ""

    cgroup_file = Path("/proc") / str(pid) / "cgroup"
    try:
        for line in cgroup_file.read_text().splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[0] == "0":
                return parts[2].lstrip("/")
    except OSError:
        return ""
    return ""


def read_cgroup_value(path: Path) -> Optional[int]:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    if value == "max":
        return 0
    try:
        return int(value)
    except ValueError:
        return None


def read_cgroup_key_values(path: Path) -> Dict[str, int]:
    values: Dict[str, int] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        key, separator, value = line.partition(" ")
        if not separator:
            continue
        try:
            values[key] = int(value)
        except ValueError:
            continue
    return values


def cgroup_file(info: ContainerInfo, filename: str) -> Path:
    return Path("/sys/fs/cgroup") / info.cgroup_path / filename


def cgroup_stat(info: ContainerInfo) -> Optional[StatRow]:
    if not info.cgroup_path:
        return None

    usage = read_cgroup_value(cgroup_file(info, "memory.current"))
    if usage is None:
        return None

    limit = read_cgroup_value(cgroup_file(info, "memory.max")) or 0
    pids = read_cgroup_value(cgroup_file(info, "pids.current")) or 0
    memory_stat = read_cgroup_key_values(cgroup_file(info, "memory.stat"))
    memory_events = read_cgroup_key_values(cgroup_file(info, "memory.events"))
    cpu_usage_usec = read_cpu_usage_usec(info)
    mem_percent = round((usage / limit) * 100, 4) if limit else 0.0
    return StatRow(
        container_id=info.container_id[:12],
        name=info.name,
        mem_usage_bytes=usage,
        mem_limit_bytes=limit,
        mem_percent=mem_percent,
        cpu_percent=0.0,
        cpu_usage_usec=cpu_usage_usec,
        pids=pids,
        mem_anon_bytes=memory_stat.get("anon", 0),
        mem_file_bytes=memory_stat.get("file", 0),
        mem_kernel_bytes=memory_stat.get("kernel", 0),
        mem_shmem_bytes=memory_stat.get("shmem", 0),
        mem_sock_bytes=memory_stat.get("sock", 0),
        memory_oom=memory_events.get("oom", 0),
        memory_oom_kill=memory_events.get("oom_kill", 0),
    )


def read_cpu_usage_usec(info: ContainerInfo) -> int:
    path = cgroup_file(info, "cpu.stat")
    try:
        for line in path.read_text().splitlines():
            key, _, value = line.partition(" ")
            if key == "usage_usec":
                return int(value)
    except (OSError, ValueError):
        return 0
    return 0


def cgroup_stats(infos: Dict[str, ContainerInfo]) -> Dict[str, StatRow]:
    stats: Dict[str, StatRow] = {}
    seen: Set[str] = set()
    for info in infos.values():
        if info.container_id in seen:
            continue
        seen.add(info.container_id)
        row = cgroup_stat(info)
        if row is None:
            continue
        stats[info.name] = row
        stats[info.container_id] = row
        stats[info.container_id[:12]] = row
    return stats


def nsenter_stat(info: ContainerInfo) -> Optional[StatRow]:
    if info.pid <= 0:
        return None

    result = subprocess.run(
        [
            "nsenter",
            "-t",
            str(info.pid),
            "-m",
            "--",
            "sh",
            "-lc",
            "cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.max /sys/fs/cgroup/pids.current 2>/dev/null",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    values = result.stdout.splitlines()
    if len(values) < 3:
        return None

    usage = int(values[0])
    limit = 0 if values[1] == "max" else int(values[1])
    pids = int(values[2])
    mem_percent = round((usage / limit) * 100, 4) if limit else 0.0
    return StatRow(
        container_id=info.container_id[:12],
        name=info.name,
        mem_usage_bytes=usage,
        mem_limit_bytes=limit,
        mem_percent=mem_percent,
        cpu_percent=0.0,
        cpu_usage_usec=0,
        pids=pids,
    )


def nsenter_stats(infos: Dict[str, ContainerInfo]) -> Dict[str, StatRow]:
    stats: Dict[str, StatRow] = {}
    seen: Set[str] = set()
    for info in infos.values():
        if info.container_id in seen:
            continue
        seen.add(info.container_id)
        row = nsenter_stat(info)
        if row is None:
            continue
        stats[info.name] = row
        stats[info.container_id] = row
        stats[info.container_id[:12]] = row
    return stats


def docker_stats() -> Dict[str, StatRow]:
    fmt = "{{.ID}}\t{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}\t{{.PIDs}}"
    output = run_cmd(["docker", "stats", "--no-stream", "--format", fmt])
    stats: Dict[str, StatRow] = {}

    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 6:
            continue
        container_id, name, mem_usage, mem_percent, cpu_percent, pids = fields
        usage, _, limit = mem_usage.partition("/")
        row = StatRow(
            container_id=container_id.strip(),
            name=name.strip(),
            mem_usage_bytes=parse_size_to_bytes(usage),
            mem_limit_bytes=parse_size_to_bytes(limit),
            mem_percent=parse_percent(mem_percent),
            cpu_percent=parse_percent(cpu_percent),
            cpu_usage_usec=0,
            pids=parse_int(pids),
        )
        stats[row.name] = row
        stats[row.container_id] = row
    return stats


def label(labels: Dict[str, str], key: str) -> str:
    return labels.get(key, "")


def classify_container(name: str, labels: Dict[str, str], cmdline: str = "") -> str:
    meta_class = label(labels, "org.seedsecuritylabs.seedemu.meta.class")
    display_name = label(labels, "org.seedsecuritylabs.seedemu.meta.displayname")
    role = label(labels, "org.seedsecuritylabs.seedemu.meta.role")
    service = label(labels, "com.docker.compose.service")
    combined = " ".join([name, service, meta_class, display_name, role, cmdline])

    if "Ethereum-POS-Geth" in combined:
        return "geth"
    if "Ethereum-POS-BeaconSetup" in combined:
        return "beacon_setup"
    if "Ethereum-POS-Beacon" in combined:
        return "beacon"
    if "Ethereum-POS-Validator" in combined:
        return "validator"
    if "Faucet" in combined:
        return "faucet"
    if "UtilityServer" in combined or "EthUtilityServer" in combined:
        return "utility"
    if name == "seedemu_ethexplorer_backend" or service == "seedemu-ethexplorer-backend":
        return "etherview_backend"
    if name == "seedemu_ethexplorer_web" or service == "seedemu-ethexplorer-web":
        return "etherview_web"
    if name in {"clickhouse", "postgres", "alloy", "redis", "littlebigtable", "rawbigtable"}:
        return "etherview_db"
    if name == "seedemu_internet_map" or service == "seedemu_internet_map":
        return "internet_map"
    if name.startswith("rs_ix_"):
        return "route_server"
    if name.startswith("brdnode_") or role == "Router":
        return "router"
    if "bc eth1indexer" in cmdline or "bc rewards-exporter" in cmdline or "bc statistics" in cmdline:
        return "etherview_backend"
    if "/app/explorer" in cmdline or "frontend-data-updater" in cmdline or "validator-tagger" in cmdline:
        return "etherview_web"
    if "node /app/.output/server/index.mjs" in cmdline:
        return "etherview_web"
    if "postgres" in cmdline or "redis-server" in cmdline or "clickhouse" in cmdline or "little_bigtable" in cmdline:
        return "etherview_db"
    if "/faucet/" in cmdline:
        return "faucet"
    if "/utility_server/" in cmdline:
        return "utility"
    if "node ./bin/main.js" in cmdline:
        return "internet_map"
    if re.search(r"(^|\s|/)geth(\s|$)", cmdline):
        return "geth"
    if re.search(r"(^|\s|/)lighthouse\b.*\sbn(\s|$)", cmdline):
        return "beacon"
    if re.search(r"(^|\s|/)lighthouse\b.*\svc(\s|$)", cmdline):
        return "validator"
    if "beacon_bootnode_http_server.py" in cmdline or "eth-beacon-genesis" in cmdline:
        return "beacon_setup"
    if re.search(r"(^|\s|/)bird(\s|$)", cmdline):
        return "router"
    return "other"


def percentile(sorted_values: List[int], pct: float) -> int:
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return int(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def append_csv(path: Path, header: Sequence[str], rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header), extrasaction="ignore")
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)


def write_latest(path: Path, header: Sequence[str], rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def collect_sample(
    sample: int,
    started_at: float,
    compose_names: Set[str],
    scope: str,
    backend: str,
    cpu_state: Dict[str, tuple[int, float]],
    metadata_infos: Optional[Dict[str, ContainerInfo]] = None,
) -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    if backend in {"cgroup-scan", "cgroup-scan-enriched"}:
        infos = scan_cgroup_containers()
        if backend == "cgroup-scan-enriched":
            infos = enrich_cgroup_infos(infos, metadata_infos or {})
        ids = sorted({info.container_id for info in infos.values()})
        stats = cgroup_stats(infos)
    else:
        ids = docker_ps_ids()
        infos = inspect_containers(ids)
    if backend == "docker-stats":
        stats = docker_stats()
    elif backend == "nsenter":
        stats = nsenter_stats(infos)
    elif backend == "cgroup-docker":
        stats = cgroup_stats(infos)
    elif backend not in {"cgroup-scan", "cgroup-scan-enriched"}:
        raise ValueError(f"unknown backend: {backend}")
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    sample_time = time.time()
    elapsed_sec = round(sample_time - started_at, 3)

    raw_rows: List[Dict[str, object]] = []
    for container_id in ids:
        info = infos.get(container_id)
        if info is None:
            continue
        if backend != "cgroup-scan" and scope == "compose" and compose_names and info.name not in compose_names:
            continue
        stat = stats.get(info.name) or stats.get(container_id) or stats.get(info.container_id[:12])
        if stat is None:
            continue
        cpu_percent = stat.cpu_percent
        if stat.cpu_usage_usec > 0:
            key = info.container_id
            previous = cpu_state.get(key)
            if previous is not None:
                prev_usage_usec, prev_time = previous
                delta_usage_sec = max(0.0, (stat.cpu_usage_usec - prev_usage_usec) / 1_000_000)
                delta_wall_sec = max(0.001, sample_time - prev_time)
                cpu_percent = round((delta_usage_sec / delta_wall_sec) * 100, 3)
            cpu_state[key] = (stat.cpu_usage_usec, sample_time)
        labels = info.labels
        raw_rows.append(
            {
                "sample": sample,
                "timestamp": timestamp,
                "elapsed_sec": elapsed_sec,
                "container_id": info.container_id[:12],
                "container_name": info.name,
                "compose_service": label(labels, "com.docker.compose.service"),
                "node_type": classify_container(info.name, labels, info.cmdline),
                "display_name": label(labels, "org.seedsecuritylabs.seedemu.meta.displayname"),
                "asn": label(labels, "org.seedsecuritylabs.seedemu.meta.asn"),
                "eth_role": label(labels, "org.seedsecuritylabs.seedemu.meta.ethereum.role"),
                "main_cmd": info.cmdline,
                "mem_usage_bytes": stat.mem_usage_bytes,
                "mem_limit_bytes": stat.mem_limit_bytes,
                "mem_percent": stat.mem_percent,
                "cpu_percent": cpu_percent,
                "pids": stat.pids,
                "mem_anon_bytes": stat.mem_anon_bytes,
                "mem_file_bytes": stat.mem_file_bytes,
                "mem_kernel_bytes": stat.mem_kernel_bytes,
                "mem_shmem_bytes": stat.mem_shmem_bytes,
                "mem_sock_bytes": stat.mem_sock_bytes,
                "memory_oom": stat.memory_oom,
                "memory_oom_kill": stat.memory_oom_kill,
            }
        )

    grouped: Dict[str, List[Dict[str, object]]] = {}
    for row in raw_rows:
        grouped.setdefault(str(row["node_type"]), []).append(row)

    type_rows: List[Dict[str, object]] = []
    for node_type, rows in sorted(grouped.items()):
        mem_values = sorted(int(row["mem_usage_bytes"]) for row in rows)
        cpu_values = [float(row["cpu_percent"]) for row in rows]
        pids_values = [int(row["pids"]) for row in rows]
        type_rows.append(
            {
                "sample": sample,
                "timestamp": timestamp,
                "elapsed_sec": elapsed_sec,
                "node_type": node_type,
                "containers": len(rows),
                "mem_total_bytes": sum(mem_values),
                "mem_avg_bytes": int(sum(mem_values) / len(mem_values)) if mem_values else 0,
                "mem_min_bytes": mem_values[0] if mem_values else 0,
                "mem_p50_bytes": percentile(mem_values, 0.50),
                "mem_p95_bytes": percentile(mem_values, 0.95),
                "mem_max_bytes": mem_values[-1] if mem_values else 0,
                "mem_limit_total_bytes": sum(int(row["mem_limit_bytes"]) for row in rows),
                "cpu_avg_percent": round(sum(cpu_values) / len(cpu_values), 3) if cpu_values else 0,
                "pids_total": sum(pids_values),
                "mem_anon_total_bytes": sum(int(row["mem_anon_bytes"]) for row in rows),
                "mem_file_total_bytes": sum(int(row["mem_file_bytes"]) for row in rows),
                "mem_kernel_total_bytes": sum(int(row["mem_kernel_bytes"]) for row in rows),
                "mem_shmem_total_bytes": sum(int(row["mem_shmem_bytes"]) for row in rows),
                "mem_sock_total_bytes": sum(int(row["mem_sock_bytes"]) for row in rows),
                "memory_oom_total": sum(int(row["memory_oom"]) for row in rows),
                "memory_oom_kill_total": sum(int(row["memory_oom_kill"]) for row in rows),
            }
        )

    return raw_rows, type_rows


def write_run_info(output_dir: Path, args: argparse.Namespace, status: str, samples: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "samples": samples,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "interval_sec": args.interval,
        "requested_samples": args.samples,
        "scope": args.scope,
        "backend": args.backend,
        "expected_process_counts": {
            "beacon": args.expect_beacons,
            "geth": args.expect_geth,
            "validator": args.expect_validators,
        },
        "compose_file": str(args.compose_file),
        "raw_csv": str(output_dir / "memory_raw.csv"),
        "by_type_csv": str(output_dir / "memory_by_type.csv"),
    }
    (output_dir / "run_info.json").write_text(json.dumps(payload, indent=2) + "\n")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect container resources from a compiled blockchain deployment."
    )
    parser.add_argument(
        "-f",
        "--compose-file",
        type=Path,
        default=DEFAULT_COMPOSE_FILE,
        help=f"Compose file used to scope containers. Default: {DEFAULT_COMPOSE_FILE}",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for CSV output. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=int,
        default=300,
        help="Seconds between samples. Default: 300",
    )
    parser.add_argument(
        "-n",
        "--samples",
        type=int,
        default=0,
        help="Number of samples to collect. 0 means run until interrupted. Default: 0",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect one sample and exit.",
    )
    parser.add_argument(
        "--backend",
        choices=[
            "cgroup-scan",
            "cgroup-scan-enriched",
            "cgroup-docker",
            "nsenter",
            "docker-stats",
        ],
        default="cgroup-scan",
        help=(
            "Collection backend. cgroup-scan does not call Docker and is fastest; "
            "cgroup-scan-enriched takes one Docker metadata snapshot and then "
            "scans cgroups; cgroup-docker queries Docker every sample; "
            "nsenter is diagnostic only. "
            "Default: cgroup-scan"
        ),
    )
    parser.add_argument(
        "--scope",
        choices=["compose", "all"],
        default="compose",
        help="compose: only containers named in compose file; all: every running container. Default: compose",
    )
    parser.add_argument("--expect-beacons", type=int, default=0)
    parser.add_argument("--expect-geth", type=int, default=0)
    parser.add_argument("--expect-validators", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.interval < 1:
        print("--interval must be >= 1", file=sys.stderr)
        return 2
    if args.samples < 0:
        print("--samples must be >= 0", file=sys.stderr)
        return 2
    if min(args.expect_beacons, args.expect_geth, args.expect_validators) < 0:
        print("expected process counts must be >= 0", file=sys.stderr)
        return 2
    if args.once:
        args.samples = 1

    output_dir = args.output_dir
    raw_path = output_dir / "memory_raw.csv"
    type_path = output_dir / "memory_by_type.csv"
    latest_raw_path = output_dir / "memory_latest_raw.csv"
    latest_type_path = output_dir / "memory_latest_by_type.csv"
    compose_names = load_compose_container_names(args.compose_file)

    stop_requested = False

    def handle_stop(signum, frame):  # type: ignore[no-untyped-def]
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_run_info(output_dir, args, "running", 0)
    started_at = time.time()
    sample = 0
    cpu_state: Dict[str, tuple[int, float]] = {}
    metadata_infos: Dict[str, ContainerInfo] = {}
    if args.backend == "cgroup-scan-enriched":
        metadata_infos = inspect_containers(docker_ps_ids())

    print(f"writing raw samples to {raw_path}")
    print(f"writing type aggregates to {type_path}")
    print(
        f"scope={args.scope} backend={args.backend} "
        f"compose_containers={len(compose_names)} interval={args.interval}s"
    )

    try:
        while not stop_requested:
            sample += 1
            raw_rows, type_rows = collect_sample(
                sample,
                started_at,
                compose_names,
                args.scope,
                args.backend,
                cpu_state,
                metadata_infos,
            )
            append_csv(raw_path, RAW_HEADER, raw_rows)
            append_csv(type_path, TYPE_HEADER, type_rows)
            write_latest(latest_raw_path, RAW_HEADER, raw_rows)
            write_latest(latest_type_path, TYPE_HEADER, type_rows)
            write_run_info(output_dir, args, "running", sample)
            print(
                f"sample={sample} containers={len(raw_rows)} types={len(type_rows)} "
                f"timestamp={datetime.now().astimezone().isoformat(timespec='seconds')}",
                flush=True,
            )

            observed = {
                str(row["node_type"]): int(row["containers"]) for row in type_rows
            }
            expected = {
                "beacon": args.expect_beacons,
                "geth": args.expect_geth,
                "validator": args.expect_validators,
            }
            mismatches = [
                f"{node_type}={observed.get(node_type, 0)} expected={count}"
                for node_type, count in expected.items()
                if count and observed.get(node_type, 0) != count
            ]
            if mismatches:
                raise RuntimeError("node process count mismatch: " + ", ".join(mismatches))

            if args.samples and sample >= args.samples:
                break

            deadline = time.time() + args.interval
            while not stop_requested:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                time.sleep(min(1.0, remaining))
    except Exception as exc:
        write_run_info(output_dir, args, "failed", sample)
        print(f"collection failed: {exc}", file=sys.stderr)
        return 1

    write_run_info(output_dir, args, "complete", sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
