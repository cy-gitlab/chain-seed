"""Temporary D61 generated-file adapters; pending native Ethereum Service options.

Called during compilation, before output is presented as runnable. No Docker calls.
"""
from __future__ import annotations
import json
import re
import shlex
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
import yaml


def read_deployment(compose):
    """Read the generated role counts without depending on another CLI script."""
    return json.loads(Path(compose).resolve().with_name("deployment.json").read_text())


def prepare_output(output, config):
    """Apply the paper settings once, as part of generating a runnable output."""
    output = Path(output)
    compose = output / "docker-compose.yml"
    _patch_validator_key_distribution(str(output), config["validator_clients"], config["keys_per_client"])
    payload = yaml.safe_load(compose.read_text())
    roles = {"Beacon": [], "Geth": [], "Validator": [], "BeaconSetup": []}
    for service in payload["services"].values():
        labels = service.get("labels", {})
        name = labels.get("org.seedsecuritylabs.seedemu.meta.displayname", "")
        match = re.fullmatch(r"Ethereum-POS-(BeaconSetup|Beacon|Geth|Validator)(?:-(\d+))?", name)
        if not match:
            continue
        role = match[1]
        roles[role].append(service)
        build = service["build"]
        directory = output / (build if isinstance(build, str) else build["context"])
        if role == "Geth":
            environment = service.get("environment", {})
            if isinstance(environment, list):
                environment = dict(item.split("=", 1) for item in environment)
            service["environment"] = environment
            environment["GOMEMLIMIT"] = f'{config["geth_gomemlimit_mib"]}MiB'
            start = _copied_file_path(directory, "/start.sh")
            text = start.read_text()
            text, n = re.subn(r"(?m)^(\s*geth\s+)(?!.*\binit\b)",
                             rf'\g<1>--cache={config["geth_cache_mib"]} --rpc.txfeecap 0 ', text)
            if n != 1:
                raise ValueError(f"Expected one Geth runtime command in {start}, found {n}")
            start.write_text(text)
        elif role == "Beacon":
            bootstrap = _copied_file_path(directory, "/tmp/beacon-bootstrapper")
            text, n = re.subn(r"(?<!\S)bn(?=\s)",
                             f'bn --beacon-processor-max-workers {config["worker_cap"]} '
                             '--metrics --metrics-address 0.0.0.0 --metrics-port 5054', bootstrap.read_text())
            if n != 1:
                raise ValueError(f"Expected one Lighthouse bn command in {bootstrap}")
            bootstrap.write_text(text)
        elif role == "BeaconSetup":
            start = _copied_file_path(directory, "/start.sh")
            text = start.read_text()
            old = 'GENESIS_TIME=`date +%s`'
            if text.count(old) != 1:
                raise ValueError(f"Cannot locate genesis time in {start}")
            start.write_text(text.replace(old, f'GENESIS_TIME=$(( $(date +%s) + {config["genesis_delay_seconds"]} ))'))
        if role in ("Beacon", "Validator"):
            dest = "/tmp/beacon-bootstrapper" if role == "Beacon" else "/tmp/vc_bootstrapper"
            bootstrap = _copied_file_path(directory, dest)
            text = bootstrap.read_text().replace('[ $count -gt 60 ]', '[ $count -gt 720 ]')
            bootstrap.write_text(text.replace('curl --http0.9 ', 'curl --connect-timeout 5 --max-time 120 --http0.9 '))
    expected = {"Beacon": config["beacons"], "Geth": config["beacons"],
                "Validator": config["validator_clients"], "BeaconSetup": 1}
    if any(len(roles[k]) != n for k, n in expected.items()):
        raise ValueError("Compiled role counts do not match the deployment plan")
    compose.write_text(yaml.safe_dump(payload, sort_keys=False))
    from tools.blockchain.lib.runtime_mounts import patch_output
    patch_output(output, compose, "/seedemu-node", 524288, False, False)
    (output / "deployment.json").write_text(json.dumps(config, indent=2) + "\n")
def _compose_service_blocks(compose_path: Path) -> List[List[str]]:
    blocks: List[List[str]] = []
    current_block: List[str] = []

    for line in compose_path.read_text().splitlines():
        stripped = line.strip()
        if line.startswith("    ") and not line.startswith("        ") and stripped.endswith(":"):
            if current_block:
                blocks.append(current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)
    if current_block:
        blocks.append(current_block)
    return blocks

def _build_dir_from_service_block(compose_path: Path, block: Sequence[str]) -> Optional[Path]:
    for line in block:
        stripped = line.strip()
        if stripped.startswith("build: "):
            build_value = stripped.split(":", 1)[1].strip()
            if build_value.startswith("./"):
                build_value = build_value[2:]
            return compose_path.parent / build_value
    return None

def _service_ip_from_block(block: Sequence[str]) -> Optional[str]:
    block_text = "\n".join(block)
    match = re.search(r"ipv4_address:\s*([0-9.]+)", block_text)
    return match.group(1) if match else None

def _beacon_service_build_dirs(compose_path: Path) -> List[Tuple[Optional[str], Path]]:
    service_dirs: List[Tuple[Optional[str], Path]] = []
    for block in _compose_service_blocks(compose_path):
        block_text = "\n".join(block)
        if "Ethereum-POS-BeaconSetup" in block_text:
            continue
        if "Ethereum-POS-Beacon" not in block_text:
            continue

        build_dir = _build_dir_from_service_block(compose_path, block)
        if build_dir is None:
            continue
        service_dirs.append((_service_ip_from_block(block), build_dir))
    return service_dirs

def _beacon_setup_service_build_dir(compose_path: Path) -> Optional[Path]:
    for block in _compose_service_blocks(compose_path):
        if "Ethereum-POS-BeaconSetup" not in "\n".join(block):
            continue
        return _build_dir_from_service_block(compose_path, block)
    return None

def _validator_service_build_dirs(compose_path: Path) -> List[Path]:
    service_dirs: List[Path] = []
    for block in _compose_service_blocks(compose_path):
        if "Ethereum-POS-Validator-" not in "\n".join(block):
            continue
        build_dir = _build_dir_from_service_block(compose_path, block)
        if build_dir is not None:
            service_dirs.append(build_dir)
    return service_dirs

def _copied_file_path(service_dir: Path, dest_path: str) -> Optional[Path]:
    dockerfile = service_dir / "Dockerfile"
    if not dockerfile.exists():
        return None

    for line in dockerfile.read_text().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "COPY" and parts[2] == dest_path:
            return service_dir / parts[1]
    return None

def _start_script_path(service_dir: Path) -> Optional[Path]:
    return _copied_file_path(service_dir, "/start.sh")

def _beacon_bootstrapper_path(service_dir: Path) -> Optional[Path]:
    return _copied_file_path(service_dir, "/tmp/beacon-bootstrapper")

def _patch_validator_key_distribution(
    output: str,
    validator_clients: int,
    keys_per_client: int,
) -> None:
    """Expand genesis keys without increasing Validator client containers."""
    assert keys_per_client >= 1, "validator_keys_per_client must be >= 1"

    compose_path = Path(output) / "docker-compose.yml"
    if not compose_path.exists():
        raise RuntimeError(f"missing generated compose file: {compose_path}")

    setup_dir = _beacon_setup_service_build_dir(compose_path)
    if setup_dir is None:
        raise RuntimeError("could not locate generated BeaconSetup service")
    mnemonic_path = _copied_file_path(setup_dir, "/tmp/mnemonic.yaml")
    if mnemonic_path is None or not mnemonic_path.exists():
        raise RuntimeError("could not locate generated BeaconSetup mnemonic.yaml")

    start_pattern = re.compile(r"^(\s*start:\s*)(\d+)(\s*)$", re.MULTILINE)
    count_pattern = re.compile(r"^(\s*count:\s*)(\d+)(\s*)$", re.MULTILINE)
    mnemonic_text = mnemonic_path.read_text()
    starts = [int(match.group(2)) for match in start_pattern.finditer(mnemonic_text)]
    counts = [int(match.group(2)) for match in count_pattern.finditer(mnemonic_text)]
    original_starts = list(range(validator_clients))
    scaled_starts = [index * keys_per_client for index in range(validator_clients)]

    if len(starts) != validator_clients or len(counts) != validator_clients:
        raise RuntimeError(
            "BeaconSetup mnemonic entry count does not match Validator clients: "
            f"starts={len(starts)}, counts={len(counts)}, clients={validator_clients}"
        )

    mnemonic_already_scaled = starts == scaled_starts and all(
        count == keys_per_client for count in counts
    )
    if not mnemonic_already_scaled:
        if starts != original_starts or any(count != 1 for count in counts):
            raise RuntimeError(
                "BeaconSetup mnemonic ranges are neither pristine nor configured for "
                f"{keys_per_client} keys per client"
            )
        start_index = iter(scaled_starts)
        mnemonic_text = start_pattern.sub(
            lambda match: f"{match.group(1)}{next(start_index)}{match.group(3)}",
            mnemonic_text,
        )
        mnemonic_text = count_pattern.sub(
            lambda match: f"{match.group(1)}{keys_per_client}{match.group(3)}",
            mnemonic_text,
        )

    validator_dirs = _validator_service_build_dirs(compose_path)
    if len(validator_dirs) != validator_clients:
        raise RuntimeError(
            "generated Validator service count mismatch: "
            f"found={len(validator_dirs)}, expected={validator_clients}"
        )

    source_min_pattern = re.compile(r"--source-min\s+(\d+)")
    source_max_pattern = re.compile(r"--source-max\s+(\d+)")
    validator_updates: List[Tuple[Path, str]] = []
    seen_client_indices: set[int] = set()

    for service_dir in validator_dirs:
        bootstrapper_path = _copied_file_path(service_dir, "/tmp/vc_bootstrapper")
        if bootstrapper_path is None or not bootstrapper_path.exists():
            raise RuntimeError(f"missing vc_bootstrapper in {service_dir}")
        original = bootstrapper_path.read_text()
        min_match = source_min_pattern.search(original)
        max_match = source_max_pattern.search(original)
        if min_match is None or max_match is None:
            raise RuntimeError(f"missing validator key range in {bootstrapper_path}")

        source_min = int(min_match.group(1))
        source_max = int(max_match.group(1))
        if source_max - source_min == 1 and source_min < validator_clients:
            client_index = source_min
        elif (
            source_min % keys_per_client == 0
            and source_max - source_min == keys_per_client
            and source_min // keys_per_client < validator_clients
        ):
            client_index = source_min // keys_per_client
        else:
            raise RuntimeError(
                f"unexpected validator key range [{source_min}, {source_max}) in "
                f"{bootstrapper_path}"
            )

        if client_index in seen_client_indices:
            raise RuntimeError(f"duplicate Validator client key range index: {client_index}")
        seen_client_indices.add(client_index)

        expected_min = client_index * keys_per_client
        expected_max = (client_index + 1) * keys_per_client
        patched = source_min_pattern.sub(f"--source-min {expected_min}", original, count=1)
        patched = source_max_pattern.sub(f"--source-max {expected_max}", patched, count=1)
        validator_updates.append((bootstrapper_path, patched))

    expected_indices = set(range(validator_clients))
    if seen_client_indices != expected_indices:
        missing = sorted(expected_indices - seen_client_indices)
        raise RuntimeError(f"Validator client key ranges are incomplete; missing={missing[:10]}")

    if not mnemonic_already_scaled:
        mnemonic_path.write_text(mnemonic_text)
    patched_bootstrappers = 0
    for bootstrapper_path, patched in validator_updates:
        if bootstrapper_path.read_text() == patched:
            continue
        bootstrapper_path.write_text(patched)
        patched_bootstrappers += 1

    summary = {
        "validator_clients": validator_clients,
        "keys_per_client": keys_per_client,
        "active_validators": validator_clients * keys_per_client,
        "beacon_setup_mnemonic_patched": not mnemonic_already_scaled,
        "validator_bootstrappers_patched": patched_bootstrappers,
    }
    (Path(output) / "validator-pressure-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(
        "Validator key distribution: "
        f"clients={validator_clients}, keys_per_client={keys_per_client}, "
        f"active_validators={validator_clients * keys_per_client}, "
        f"patched_bootstrappers={patched_bootstrappers}"
    )
