#!/usr/bin/env python3
"""Patch generated D61 Solana output for large-scale experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "PyYAML is required. Use the repository Python environment, for example "
        "PYTHON_BIN=/home/bruce/miniconda3/envs/seedpy310/bin/python."
    ) from exc


SUMMARY_NAME = "solana-output-patch-summary.json"
GENESIS_SHARED_DIR_NAME = "solana_genesis_shared"
GENESIS_SHARED_TARGET = "/opt/solana/config/genesis-shared"
GENESIS_VALIDATOR_ACCOUNTS_FILE = "validator-accounts.yml"
START_BARRIER_DIR_NAME = "solana_start_barrier"
START_BARRIER_TARGET = "/opt/solana/config/start-barrier"
PATCH_HELPER_DIR_NAMES = {GENESIS_SHARED_DIR_NAME, START_BARRIER_DIR_NAME}
LOW_MEMORY_AGAVE_OPTIONS = (
    ("--accounts-db-cache-limit-mb", "64"),
    ("--unified-scheduler-handler-threads", "2"),
    ("--limit-ledger-size", "50000000"),
    ("--snapshot-interval-slots", "0"),
)
AGGRESSIVE_LOW_MEMORY_AGAVE_OPTIONS = (
    ("--accounts-db-cache-limit-mb", "32"),
    ("--unified-scheduler-handler-threads", "1"),
    ("--accounts-index-bins", "8"),
    ("--accounts-index-scan-results-limit-mb", "16"),
    ("--rpc-threads", "2"),
    ("--rpc-blocking-threads", "2"),
    ("--contact-debug-interval", "600000"),
    ("--log-messages-bytes-limit", "1024"),
    ("--accounts-db-access-storages-method", "file"),
)
ULTRA_LOW_MEMORY_AGAVE_OPTIONS = (
    ("--rpc-threads", "1"),
    ("--rpc-blocking-threads", "1"),
    ("--rpc-niceness-adjustment", "10"),
    ("--snapshot-packager-niceness-adjustment", "10"),
    ("--tpu-coalesce-ms", "5"),
)
LOW_MEMORY_AGAVE_FLAGS = (
    "--disable-banking-trace",
    "--skip-startup-ledger-verification",
)
AGGRESSIVE_LOW_MEMORY_AGAVE_FLAGS = (
    "--no-incremental-snapshots",
)
ULTRA_LOW_MEMORY_AGAVE_FLAGS = ()
VALIDATOR_RPC_FLAGS_TO_STRIP = (
    "--rpc-port",
    "--rpc-bind-address",
    "--private-rpc",
    "--rpc-threads",
    "--rpc-blocking-threads",
    "--rpc-niceness-adjustment",
    "--full-rpc-api",
    "--enable-rpc-transaction-history",
)
CORE_VALIDATOR_HEAVY_RPC_FLAGS_TO_STRIP = (
    "--private-rpc",
    "--full-rpc-api",
    "--enable-rpc-transaction-history",
)
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ED25519_Q = 2**255 - 19
ED25519_D = (-121665 * pow(121666, ED25519_Q - 2, ED25519_Q)) % ED25519_Q
ED25519_I = pow(2, (ED25519_Q - 1) // 4, ED25519_Q)
ED25519_BASE_Y = (4 * pow(5, ED25519_Q - 2, ED25519_Q)) % ED25519_Q


def _ed25519_recover_x(y: int, sign: int) -> int:
    xx = (y * y - 1) * pow(ED25519_D * y * y + 1, ED25519_Q - 2, ED25519_Q)
    x = pow(xx % ED25519_Q, (ED25519_Q + 3) // 8, ED25519_Q)
    if (x * x - xx) % ED25519_Q != 0:
        x = (x * ED25519_I) % ED25519_Q
    if (x & 1) != sign:
        x = ED25519_Q - x
    return x


ED25519_BASE = (_ed25519_recover_x(ED25519_BASE_Y, 0), ED25519_BASE_Y)


class PatchError(RuntimeError):
    pass


def _ed25519_add(point_a: tuple[int, int], point_b: tuple[int, int]) -> tuple[int, int]:
    ax, ay = point_a
    bx, by = point_b
    denominator = pow(1 + ED25519_D * ax * bx * ay * by, ED25519_Q - 2, ED25519_Q)
    x = ((ax * by + ay * bx) * denominator) % ED25519_Q
    denominator = pow(1 - ED25519_D * ax * bx * ay * by, ED25519_Q - 2, ED25519_Q)
    y = ((ay * by + ax * bx) * denominator) % ED25519_Q
    return x, y


def _ed25519_scalarmult(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _ed25519_add(result, addend)
        addend = _ed25519_add(addend, addend)
        scalar >>= 1
    return result


def _ed25519_encode(point: tuple[int, int]) -> bytes:
    x, y = point
    encoded = bytearray(y.to_bytes(32, "little"))
    encoded[31] |= (x & 1) << 7
    return bytes(encoded)


@lru_cache(maxsize=None)
def _ed25519_public_from_seed(seed: bytes) -> bytes:
    digest = bytearray(hashlib.sha512(seed).digest()[:32])
    digest[0] &= 248
    digest[31] &= 63
    digest[31] |= 64
    scalar = int.from_bytes(digest, "little")
    return _ed25519_encode(_ed25519_scalarmult(ED25519_BASE, scalar))


@lru_cache(maxsize=None)
def _solana_keypair(label: str) -> tuple[int, ...]:
    # Deterministic keys keep generated output reproducible. This is only for a
    # private emulator network, not for production wallets.
    seed = hashlib.sha256(f"seedemu-d61-solana:{label}".encode("utf-8")).digest()
    public_key = _ed25519_public_from_seed(seed)
    return tuple(seed + public_key)


@lru_cache(maxsize=None)
def _base58_encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = BASE58_ALPHABET[remainder] + encoded
    leading_zeroes = len(data) - len(data.lstrip(b"\0"))
    return BASE58_ALPHABET[0] * leading_zeroes + (encoded or BASE58_ALPHABET[0])


def _solana_pubkey(label: str) -> str:
    keypair = _solana_keypair(label)
    return _base58_encode(bytes(keypair[32:]))


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise PatchError(f"invalid compose file: {path}")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    with path.open("w") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, default_flow_style=False)


def _service_text(name: str, service: dict[str, Any]) -> str:
    labels = service.get("labels")
    if isinstance(labels, dict):
        label_text = " ".join(str(value) for value in labels.values())
    elif isinstance(labels, list):
        label_text = " ".join(str(value) for value in labels)
    else:
        label_text = ""
    return " ".join(
        [
            name,
            str(service.get("container_name", "")),
            label_text,
        ]
    )


def _service_source_dir(service: dict[str, Any]) -> str | None:
    build = service.get("build")
    if isinstance(build, dict) and build.get("context"):
        return Path(str(build["context"])).name
    if isinstance(build, str):
        return Path(build).name

    volumes = service.get("volumes", [])
    if not isinstance(volumes, list):
        return None
    for volume in volumes:
        source = None
        if isinstance(volume, dict):
            source = volume.get("source")
        elif isinstance(volume, str):
            source = volume.split(":", 1)[0]
        if source and Path(str(source)).name not in PATCH_HELPER_DIR_NAMES:
            return Path(str(source)).name
    return None


def _validator_script_indices(output_dir: Path) -> dict[Path, int]:
    data = _read_yaml(output_dir / "docker-compose.yml")
    indices: dict[Path, int] = {}

    for name, service in data["services"].items():
        if not isinstance(service, dict):
            continue
        text = _service_text(name, service)
        match = re.search(r"Solana-Validator-(\d+)", text)
        if match is None:
            continue
        source_dir = _service_source_dir(service)
        if source_dir is None:
            continue
        indices[output_dir / source_dir] = int(match.group(1))

    return indices


def _extract_self_ip(text: str) -> str | None:
    match = re.search(r"^SELF_IP=([0-9.]+)\s*$", text, re.MULTILINE)
    if match is None:
        return None
    return match.group(1)


def _validator_self_ips(output_dir: Path, validator_indices: dict[Path, int]) -> dict[int, str]:
    self_ips: dict[int, str] = {}
    for validator_dir, validator_index in validator_indices.items():
        for path in validator_dir.iterdir():
            if not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            if "# seedemu-solana: validator joining the private cluster" not in text:
                continue
            self_ip = _extract_self_ip(text)
            if self_ip is not None:
                self_ips[validator_index] = self_ip
                break
    return self_ips


def _core_peer_ids(core_validator_count: int) -> list[str]:
    peer_ids = [_solana_pubkey("bootstrap:identity")]
    peer_ids.extend(
        _solana_pubkey(f"validator:{index:03d}:identity")
        for index in range(1, core_validator_count + 1)
    )
    return peer_ids


def _core_entrypoints(
    validator_self_ips: dict[int, str],
    core_validator_count: int,
    validator_index: int,
) -> list[str]:
    entrypoints: list[str] = []
    for index in range(1, core_validator_count + 1):
        if index == validator_index:
            continue
        self_ip = validator_self_ips.get(index)
        if self_ip is None:
            continue
        entrypoints.append(f"{self_ip}:8001")
    return entrypoints


def _bootstrap_script_dir(output_dir: Path) -> Path:
    data = _read_yaml(output_dir / "docker-compose.yml")

    for name, service in data["services"].items():
        if not isinstance(service, dict):
            continue
        if "Solana-Bootstrap" not in _service_text(name, service):
            continue
        source_dir = _service_source_dir(service)
        if source_dir is None:
            continue
        return output_dir / source_dir

    raise PatchError("could not find Solana bootstrap service in docker-compose.yml")


def _write_keypair(path: Path, label: str) -> bool:
    data = json.dumps(_solana_keypair(label), separators=(",", ":")) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == data:
        return False
    path.write_text(data)
    return True


def _write_validator_accounts_file(
    path: Path,
    validator_indices: dict[Path, int],
    validator_stake_lamports: int,
    validator_balance_lamports: int,
    core_validator_count: int,
    core_validator_stake_lamports: int,
) -> bool:
    accounts = []
    for _, validator_index in sorted(validator_indices.items(), key=lambda item: item[1]):
        prefix = f"validator:{validator_index:03d}"
        stake_lamports = (
            core_validator_stake_lamports
            if validator_index <= core_validator_count
            else validator_stake_lamports
        )
        accounts.append(
            {
                "identity_account": _solana_pubkey(f"{prefix}:identity"),
                "vote_account": _solana_pubkey(f"{prefix}:vote-account"),
                "stake_account": _solana_pubkey(f"{prefix}:stake-account"),
                "balance_lamports": validator_balance_lamports,
                "stake_lamports": stake_lamports,
            }
        )

    data = yaml.safe_dump({"validator_accounts": accounts}, sort_keys=False, default_flow_style=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == data:
        return False
    path.write_text(data)
    return True


def _insert_dockerfile_copies(node_dir: Path, copies: list[tuple[str, str]]) -> int:
    dockerfile = node_dir / "Dockerfile"
    if not dockerfile.exists():
        raise PatchError(f"Dockerfile not found for Solana node: {dockerfile}")

    text = dockerfile.read_text()
    new_lines = [f"COPY {source} {target}" for source, target in copies if f"COPY {source} {target}" not in text]
    if not new_lines:
        return 0

    lines = text.splitlines()
    insert_at = next((index for index, line in enumerate(lines) if line.startswith("CMD ")), len(lines))
    lines[insert_at:insert_at] = new_lines
    dockerfile.write_text("\n".join(lines) + "\n")
    return len(new_lines)


def _append_volume_once(service: dict[str, Any], mount: dict[str, Any]) -> bool:
    volumes = service.get("volumes")
    if volumes is None:
        service["volumes"] = [mount]
        return True
    if not isinstance(volumes, list):
        raise PatchError(f"unsupported service volumes format: {volumes!r}")
    for volume in volumes:
        if not isinstance(volume, dict):
            continue
        if volume.get("source") == mount["source"] and volume.get("target") == mount["target"]:
            changed = False
            for key, value in mount.items():
                if volume.get(key) != value:
                    volume[key] = value
                    changed = True
            if "read_only" in volume and "read_only" not in mount:
                del volume["read_only"]
                changed = True
            return changed
    volumes.append(mount)
    return True


def mount_genesis_shared_dir(output_dir: Path) -> int:
    compose_file = output_dir / "docker-compose.yml"
    data = _read_yaml(compose_file)
    updated = 0
    mount = {
        "type": "bind",
        "source": f"./{GENESIS_SHARED_DIR_NAME}",
        "target": GENESIS_SHARED_TARGET,
    }

    for name, service in data["services"].items():
        if not isinstance(service, dict):
            continue
        if "Solana-" not in _service_text(name, service):
            continue
        if _append_volume_once(service, mount):
            updated += 1

    _write_yaml(compose_file, data)
    return updated


def mount_start_barrier_dir(output_dir: Path) -> int:
    compose_file = output_dir / "docker-compose.yml"
    data = _read_yaml(compose_file)
    barrier_dir = output_dir / START_BARRIER_DIR_NAME
    barrier_dir.mkdir(parents=True, exist_ok=True)
    updated = 0
    mount = {
        "type": "bind",
        "source": f"./{START_BARRIER_DIR_NAME}",
        "target": START_BARRIER_TARGET,
    }

    for name, service in data["services"].items():
        if not isinstance(service, dict):
            continue
        if "Solana-" not in _service_text(name, service):
            continue
        if _append_volume_once(service, mount):
            updated += 1

    _write_yaml(compose_file, data)
    return updated


def install_genesis_voting_keypairs(
    output_dir: Path,
    validator_indices: dict[Path, int],
    validator_stake_lamports: int,
    validator_balance_lamports: int,
    core_validator_count: int,
    core_validator_stake_lamports: int,
) -> dict[str, int]:
    bootstrap_dir = _bootstrap_script_dir(output_dir)
    shared_dir = output_dir / GENESIS_SHARED_DIR_NAME
    keypair_files_written = 0
    dockerfile_copy_updates = 0
    shared_keypair_files_written = 0
    validator_accounts_files_written = 0

    bootstrap_copies: list[tuple[str, str]] = []
    if _write_keypair(shared_dir / "faucet.json", "faucet"):
        shared_keypair_files_written += 1
    if _write_keypair(bootstrap_dir / "solana_genesis_keys/faucet.json", "faucet"):
        keypair_files_written += 1
    bootstrap_copies.append(("solana_genesis_keys/faucet.json", "/opt/solana/config/faucet.json"))

    for key_name in ("identity", "vote-account", "stake-account"):
        if _write_keypair(shared_dir / f"bootstrap/{key_name}.json", f"bootstrap:{key_name}"):
            shared_keypair_files_written += 1
        source = f"solana_genesis_keys/bootstrap-{key_name}.json"
        target = f"/opt/solana/config/bootstrap-validator/{key_name}.json"
        if _write_keypair(bootstrap_dir / source, f"bootstrap:{key_name}"):
            keypair_files_written += 1
        bootstrap_copies.append((source, target))

    if _write_validator_accounts_file(
        shared_dir / GENESIS_VALIDATOR_ACCOUNTS_FILE,
        validator_indices,
        validator_stake_lamports,
        validator_balance_lamports,
        core_validator_count,
        core_validator_stake_lamports,
    ):
        validator_accounts_files_written += 1
    bootstrap_accounts_source = f"solana_genesis_keys/{GENESIS_VALIDATOR_ACCOUNTS_FILE}"
    if _write_validator_accounts_file(
        bootstrap_dir / bootstrap_accounts_source,
        validator_indices,
        validator_stake_lamports,
        validator_balance_lamports,
        core_validator_count,
        core_validator_stake_lamports,
    ):
        validator_accounts_files_written += 1
    bootstrap_copies.append((bootstrap_accounts_source, f"/opt/solana/config/{GENESIS_VALIDATOR_ACCOUNTS_FILE}"))

    for validator_dir, validator_index in sorted(validator_indices.items(), key=lambda item: item[1]):
        validator_copies: list[tuple[str, str]] = []
        for key_name in ("identity", "vote-account", "stake-account", "withdrawer"):
            key_label = f"validator:{validator_index:03d}:{key_name}"
            validator_source = f"solana_genesis_keys/{key_name}.json"
            validator_target = f"/opt/solana/config/validator/{key_name}.json"
            if _write_keypair(validator_dir / validator_source, key_label):
                keypair_files_written += 1
            validator_copies.append((validator_source, validator_target))

            if key_name == "withdrawer":
                continue
            shared_path = shared_dir / f"validators/validator-{validator_index:03d}/{key_name}.json"
            if _write_keypair(shared_path, key_label):
                shared_keypair_files_written += 1
            bootstrap_source = f"solana_genesis_keys/validator-{validator_index:03d}-{key_name}.json"
            bootstrap_target = (
                f"/opt/solana/config/genesis-validators/validator-{validator_index:03d}/{key_name}.json"
            )
            if _write_keypair(bootstrap_dir / bootstrap_source, key_label):
                keypair_files_written += 1
            bootstrap_copies.append((bootstrap_source, bootstrap_target))

        dockerfile_copy_updates += _insert_dockerfile_copies(validator_dir, validator_copies)

    dockerfile_copy_updates += _insert_dockerfile_copies(bootstrap_dir, bootstrap_copies)
    return {
        "genesis_keypair_files_written": keypair_files_written,
        "genesis_shared_keypair_files_written": shared_keypair_files_written,
        "genesis_validator_accounts_files_written": validator_accounts_files_written,
        "genesis_dockerfile_copy_updates": dockerfile_copy_updates,
        "genesis_voting_validators": len(validator_indices) + 1,
        "genesis_core_validators": min(core_validator_count, len(validator_indices)),
    }


def install_deterministic_runtime_keypairs(output_dir: Path, validator_indices: dict[Path, int]) -> dict[str, int]:
    """Install deterministic keypairs for legacy runtime-staked/non-voting runs.

    Legacy mode normally generates keypairs inside each container at first boot.
    That is incompatible with pre-rendered --gossip-validator/--repair-validator
    filters because those filters require the validators' identity pubkeys to be
    known before startup.
    """

    bootstrap_dir = _bootstrap_script_dir(output_dir)
    keypair_files_written = 0
    dockerfile_copy_updates = 0

    bootstrap_copies: list[tuple[str, str]] = []
    if _write_keypair(bootstrap_dir / "solana_deterministic_keys/faucet.json", "faucet"):
        keypair_files_written += 1
    bootstrap_copies.append(("solana_deterministic_keys/faucet.json", "/opt/solana/config/faucet.json"))

    for key_name in ("identity", "vote-account", "stake-account"):
        source = f"solana_deterministic_keys/bootstrap-{key_name}.json"
        target = f"/opt/solana/config/bootstrap-validator/{key_name}.json"
        if _write_keypair(bootstrap_dir / source, f"bootstrap:{key_name}"):
            keypair_files_written += 1
        bootstrap_copies.append((source, target))

    for validator_dir, validator_index in sorted(validator_indices.items(), key=lambda item: item[1]):
        validator_copies: list[tuple[str, str]] = []
        for key_name in ("identity", "vote-account", "stake-account", "withdrawer"):
            source = f"solana_deterministic_keys/{key_name}.json"
            target = f"/opt/solana/config/validator/{key_name}.json"
            if _write_keypair(validator_dir / source, f"validator:{validator_index:03d}:{key_name}"):
                keypair_files_written += 1
            validator_copies.append((source, target))
        dockerfile_copy_updates += _insert_dockerfile_copies(validator_dir, validator_copies)

    dockerfile_copy_updates += _insert_dockerfile_copies(bootstrap_dir, bootstrap_copies)
    return {
        "deterministic_runtime_keypairs": 1,
        "deterministic_runtime_keypair_files_written": keypair_files_written,
        "deterministic_runtime_keypair_dockerfile_copy_updates": dockerfile_copy_updates,
    }


def inject_ulimits(compose_file: Path, nofile: int) -> int:
    data = _read_yaml(compose_file)
    updated = 0

    for name, service in data["services"].items():
        if not isinstance(service, dict):
            continue
        if "Solana-" not in _service_text(name, service):
            continue
        if service.get("ulimits") == {"nofile": {"soft": nofile, "hard": nofile}}:
            continue
        service["ulimits"] = {"nofile": {"soft": nofile, "hard": nofile}}
        updated += 1

    _write_yaml(compose_file, data)
    return updated


def _ensure_genesis_option(
    lines: list[str],
    flag: str,
    value: int,
    insert_before_flags: tuple[str, ...],
) -> bool:
    desired = f"    {flag} {value} \\\n"
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{flag} "):
            if line != desired:
                lines[index] = desired
                return True
            return False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if any(stripped.startswith(insert_flag) for insert_flag in insert_before_flags):
            lines.insert(index, desired)
            return True
    raise PatchError(f"could not insert {flag} into bootstrap genesis script")


def _patch_genesis_epoch_config(
    path: Path,
    slots_per_epoch: int,
    disable_warmup_epochs: bool,
    target_tick_duration_ms: int,
    ticks_per_slot: int,
) -> bool:
    text = path.read_text(errors="ignore")
    if "# seedemu-solana: genesis / bootstrap validator" not in text:
        return False

    lines = text.splitlines(keepends=True)
    changed = False
    if disable_warmup_epochs:
        filtered_lines = [line for line in lines if line.strip() != "--enable-warmup-epochs \\"]
        if len(filtered_lines) != len(lines):
            lines = filtered_lines
            changed = True

    if _ensure_genesis_option(lines, "--slots-per-epoch", slots_per_epoch, ("--cluster-type ", "--hashes-per-tick ")):
        changed = True
    if target_tick_duration_ms > 0 and _ensure_genesis_option(
        lines,
        "--target-tick-duration",
        target_tick_duration_ms,
        ("--cluster-type ", "--hashes-per-tick ", "--slots-per-epoch "),
    ):
        changed = True
    if ticks_per_slot > 0 and _ensure_genesis_option(
        lines,
        "--ticks-per-slot",
        ticks_per_slot,
        ("--cluster-type ", "--hashes-per-tick ", "--slots-per-epoch "),
    ):
        changed = True

    if changed:
        path.write_text("".join(lines))
    return changed


def _ensure_agave_flag(lines: list[str], flag: str) -> bool:
    """Insert an agave-validator flag before --log if it is not already present."""
    if any(flag in line for line in lines):
        return False

    flag_line = f"  {flag} \\\n"
    for index, line in enumerate(lines):
        if line.strip().startswith("--log -"):
            lines.insert(index, flag_line)
            return True
    return False


def _ensure_agave_flag_each_command(lines: list[str], flag: str) -> bool:
    """Insert a flag into every agave-validator command block in a script."""
    changed = False
    command_start = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if "agave-validator \\" in line:
            command_start = index
        if line.strip().startswith("--log -"):
            if not any(flag in item for item in lines[command_start:index]):
                lines.insert(index, f"  {flag} \\\n")
                changed = True
                index += 1
        index += 1
    return changed


def _ensure_agave_option_each_command(lines: list[str], flag: str, value: str) -> bool:
    """Insert or update a value-bearing agave-validator option in each command."""
    changed = False
    command_start = 0
    index = 0
    desired = f"  {flag} {value} \\\n"
    while index < len(lines):
        line = lines[index]
        if "agave-validator \\" in line:
            command_start = index
        if line.strip().startswith("--log -"):
            existing = [
                item_index
                for item_index in range(command_start, index)
                if lines[item_index].strip().startswith(f"{flag} ")
            ]
            if existing:
                first = existing[0]
                if lines[first] != desired:
                    lines[first] = desired
                    changed = True
            else:
                lines.insert(index, desired)
                changed = True
                index += 1
        index += 1
    return changed


def _ensure_agave_raw_line_each_command(lines: list[str], raw_line: str) -> bool:
    """Insert an exact agave-validator option line into each command block."""
    changed = False
    command_start = 0
    index = 0
    desired = f"  {raw_line} \\\n"
    while index < len(lines):
        line = lines[index]
        if "agave-validator \\" in line:
            command_start = index
        if line.strip().startswith("--log -"):
            if desired not in lines[command_start:index]:
                lines.insert(index, desired)
                changed = True
                index += 1
        index += 1
    return changed


def _agave_low_memory_options(level: str) -> tuple[tuple[str, str], ...]:
    if level == "standard":
        return LOW_MEMORY_AGAVE_OPTIONS
    if level == "aggressive":
        return LOW_MEMORY_AGAVE_OPTIONS + AGGRESSIVE_LOW_MEMORY_AGAVE_OPTIONS
    if level == "ultra":
        return LOW_MEMORY_AGAVE_OPTIONS + AGGRESSIVE_LOW_MEMORY_AGAVE_OPTIONS + ULTRA_LOW_MEMORY_AGAVE_OPTIONS
    raise PatchError(f"unsupported Agave low-memory level: {level}")


def _agave_low_memory_flags(level: str) -> tuple[str, ...]:
    if level == "standard":
        return LOW_MEMORY_AGAVE_FLAGS
    if level == "aggressive":
        return LOW_MEMORY_AGAVE_FLAGS + AGGRESSIVE_LOW_MEMORY_AGAVE_FLAGS
    if level == "ultra":
        return LOW_MEMORY_AGAVE_FLAGS + AGGRESSIVE_LOW_MEMORY_AGAVE_FLAGS + ULTRA_LOW_MEMORY_AGAVE_FLAGS
    raise PatchError(f"unsupported Agave low-memory level: {level}")


def _patch_agave_low_memory_profile(text: str, level: str) -> tuple[str, bool]:
    """Apply conservative runtime knobs so many validators can share one host."""
    lines = text.splitlines(keepends=True)
    changed = False
    for flag, value in _agave_low_memory_options(level):
        if _ensure_agave_option_each_command(lines, flag, value):
            changed = True
    for flag in _agave_low_memory_flags(level):
        if _ensure_agave_flag_each_command(lines, flag):
            changed = True
    if not changed:
        return text, False
    return "".join(lines), True


def _strip_agave_lines(text: str, flags: tuple[str, ...]) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    changed = False
    for line in lines:
        stripped = line.strip()
        if any(stripped == flag or stripped == f"{flag} \\" or stripped.startswith(f"{flag} ") for flag in flags):
            changed = True
            continue
        new_lines.append(line)
    if not changed:
        return text, False
    return "".join(new_lines), True


def _patch_voting_validator_disable_rpc(text: str) -> tuple[str, bool]:
    """Disable per-validator JSON-RPC; bootstrap remains the experiment RPC endpoint."""
    return _strip_agave_lines(text, VALIDATOR_RPC_FLAGS_TO_STRIP)


def _patch_core_validator_light_public_rpc(text: str) -> tuple[str, bool]:
    """Keep core validator RPC advertised, but remove heavy RPC surfaces."""
    return _strip_agave_lines(text, CORE_VALIDATOR_HEAVY_RPC_FLAGS_TO_STRIP)


def _patch_validator_extra_entrypoints(text: str, entrypoints: list[str]) -> tuple[str, bool]:
    """Add extra core gossip entrypoints without constraining the gossip mesh."""
    if not entrypoints:
        return text, False
    lines = text.splitlines(keepends=True)
    changed = False
    for entrypoint in entrypoints:
        if _ensure_agave_raw_line_each_command(lines, f"--entrypoint {entrypoint}"):
            changed = True
    if not changed:
        return text, False
    return "".join(lines), True


def _patch_voting_validator_restricted_peers(text: str, peer_ids: list[str]) -> tuple[str, bool]:
    """Trust bootstrap/core RPC without constraining gossip mesh fanout."""
    text, stripped = _strip_agave_lines(text, ("--gossip-validator", "--repair-validator"))
    lines = text.splitlines(keepends=True)
    changed = stripped
    for peer_id in peer_ids:
        if _ensure_agave_raw_line_each_command(lines, f"--known-validator {peer_id}"):
            changed = True
    if peer_ids and _ensure_agave_flag_each_command(lines, "--only-known-rpc"):
        changed = True
    if not changed:
        return text, False
    return "".join(lines), True


def _parse_bootstrap_genesis_config(
    path: Path,
    slots_per_epoch: int,
    genesis_creation_time: str,
) -> dict[str, str]:
    text = path.read_text(errors="ignore")
    if "# seedemu-solana: genesis / bootstrap validator" not in text:
        raise PatchError(f"not a Solana bootstrap script: {path}")

    def read_flag(flag: str, default: str | None = None) -> str:
        match = re.search(rf"--{re.escape(flag)}\s+([^\\\s]+)", text)
        if match is not None:
            return match.group(1).strip().strip('"')
        if default is not None:
            return default
        raise PatchError(f"could not parse --{flag} from bootstrap script: {path}")

    return {
        "faucet_lamports": read_flag("faucet-lamports"),
        "hashes_per_tick": read_flag("hashes-per-tick"),
        "cluster_type": read_flag("cluster-type", "development"),
        "creation_time": read_flag("creation-time", genesis_creation_time),
        "slots_per_epoch": str(slots_per_epoch),
    }


def _start_barrier_script(identity_path: str, expected_validators: int) -> str:
    return f'''
START_BARRIER_DIR={START_BARRIER_TARGET}
EXPECTED_GENESIS_VALIDATORS={expected_validators}
GENESIS_START_DELAY="${{GENESIS_START_DELAY:-60}}"
mkdir -p "$START_BARRIER_DIR"
READY_ID=$(solana-keygen pubkey "{identity_path}" | tr -c 'A-Za-z0-9_.-' '_')
READY_FILE="$START_BARRIER_DIR/$READY_ID.ready"
touch "$READY_FILE"
echo "[seedemu-solana] genesis start barrier: marked ready $READY_ID; waiting for $EXPECTED_GENESIS_VALIDATORS validators."
while true; do
  READY_COUNT=$(find "$START_BARRIER_DIR" -maxdepth 1 -name '*.ready' -type f 2>/dev/null | wc -l | tr -d ' ')
  if [ "$READY_COUNT" -ge "$EXPECTED_GENESIS_VALIDATORS" ]; then
    break
  fi
  echo "[seedemu-solana] genesis start barrier: ready $READY_COUNT/$EXPECTED_GENESIS_VALIDATORS ..."
  sleep 1
done
echo "[seedemu-solana] genesis start barrier: all validators ready."
if [ -n "${{ENTRYPOINT:-}}" ]; then
  ENTRYPOINT_HOST="${{ENTRYPOINT%%:*}}"
  until ping -c 1 -W 1 "$ENTRYPOINT_HOST" >/dev/null 2>&1; do
    echo "[seedemu-solana] waiting for entrypoint host $ENTRYPOINT_HOST to be reachable ..."
    sleep 2
  done
fi
if [ "$GENESIS_START_DELAY" -gt 0 ]; then
  echo "[seedemu-solana] genesis start barrier: waiting $GENESIS_START_DELAY seconds for routing/gossip settle."
  sleep "$GENESIS_START_DELAY"
fi
'''


def _bootstrap_genesis_ledger_publish_script() -> str:
    return f'''
GENESIS_LEDGER_TAR="$GENESIS_SHARED_DIR/genesis-ledger.tar"
GENESIS_LEDGER_TAR_TMP="$GENESIS_SHARED_DIR/genesis-ledger.tar.tmp"
if [ ! -f "$GENESIS_LEDGER_TAR" ]; then
  echo "[seedemu-solana] initializing bootstrap genesis blockstore before releasing validators."
  agave-validator \\
    --identity "$LEDGER_DIR/identity.json" \\
    --vote-account "$LEDGER_DIR/vote-account.json" \\
    --ledger "$LEDGER_DIR" \\
    --rpc-port "$RPC_PORT" \\
    --rpc-bind-address 0.0.0.0 \\
    --rpc-faucet-address "127.0.0.1:$FAUCET_PORT" \\
    --gossip-host "$SELF_IP" \\
    --gossip-port "$GOSSIP_PORT" \\
    --dynamic-port-range "$DYNAMIC_PORT_RANGE" \\
    --snapshot-interval-slots 200 \\
    --no-incremental-snapshots \\
    --full-rpc-api \\
    --enable-rpc-transaction-history \\
    --allow-private-addr \\
    --no-poh-speed-test \\
    --no-os-network-limits-test \\
    --no-wait-for-vote-to-start-leader \\
    --no-genesis-fetch \\
    --log - &
  BOOTSTRAP_INIT_PID="$!"
  until solana --url "http://127.0.0.1:$RPC_PORT" cluster-version >/dev/null 2>&1; do
    if ! kill -0 "$BOOTSTRAP_INIT_PID" >/dev/null 2>&1; then
      wait "$BOOTSTRAP_INIT_PID"
      exit "$?"
    fi
    echo "[seedemu-solana] waiting for bootstrap genesis blockstore initialization ..."
    sleep 1
  done
  kill "$BOOTSTRAP_INIT_PID" >/dev/null 2>&1 || true
  wait "$BOOTSTRAP_INIT_PID" >/dev/null 2>&1 || true
  rm -f "$LEDGER_DIR/ledger.lock" "$LEDGER_DIR/rocksdb/LOCK"
  tar -C "$LEDGER_DIR" \\
    --exclude=identity.json \\
    --exclude=vote-account.json \\
    --exclude=stake-account.json \\
    --exclude=ledger.lock \\
    --exclude=rocksdb/LOCK \\
    -cf "$GENESIS_LEDGER_TAR_TMP" \\
    genesis.bin genesis.tar.bz2 rocksdb accounts_index
  mv "$GENESIS_LEDGER_TAR_TMP" "$GENESIS_LEDGER_TAR"
fi
cp "$LEDGER_DIR/genesis.bin" "$GENESIS_SHARED_DIR/genesis.bin"
echo "[seedemu-solana] bootstrap shared full genesis ledger ready."

'''


def _patch_bootstrap_genesis_voting(
    path: Path,
    genesis_creation_time: str,
    expected_validators: int,
    bootstrap_stake_lamports: int,
    bootstrap_balance_lamports: int,
    tpu_disable_quic: bool,
    tpu_enable_udp: bool,
    agave_low_memory_profile: bool,
    agave_low_memory_level: str,
) -> bool:
    text = path.read_text(errors="ignore")
    if "# seedemu-solana: genesis / bootstrap validator" not in text:
        return False

    changed = False
    if "GENESIS_SHARED_DIR=" not in text:
        insert_after = "mkdir -p \"$LEDGER_DIR\"\n"
        if insert_after not in text:
            return False
        text = text.replace(
            insert_after,
            insert_after
            + f"GENESIS_SHARED_DIR={GENESIS_SHARED_TARGET}\n"
            + "mkdir -p \"$GENESIS_SHARED_DIR\"\n",
            1,
        )
        changed = True

    if "GENESIS_VALIDATOR_ARGS=(" not in text:
        genesis_args = r'''

GENESIS_VALIDATOR_ARGS=(
  --bootstrap-validator "$LEDGER_DIR/identity.json" "$LEDGER_DIR/vote-account.json" "$LEDGER_DIR/stake-account.json"
)
VALIDATOR_ACCOUNTS_ARGS=()
if [ -f "$CONFIG_DIR/validator-accounts.yml" ]; then
  VALIDATOR_ACCOUNTS_ARGS+=(
    --validator-accounts-file "$CONFIG_DIR/validator-accounts.yml"
  )
fi
'''
        insert_after = (
            '[ -f "$LEDGER_DIR/stake-account.json" ] || solana-keygen new --no-passphrase -so  "$LEDGER_DIR/stake-account.json"\n'
        )
        if insert_after not in text:
            insert_after = (
                '[ -f "$LEDGER_DIR/stake-account.json" ] || solana-keygen new --no-passphrase -so  "$LEDGER_DIR/stake-account.json"\n'
            )
        if insert_after not in text:
            return False
        text = text.replace(insert_after, insert_after + genesis_args, 1)
        changed = True

    old_line = '    --bootstrap-validator "$LEDGER_DIR/identity.json" "$LEDGER_DIR/vote-account.json" "$LEDGER_DIR/stake-account.json" \\\n'
    new_line = '    "${GENESIS_VALIDATOR_ARGS[@]}" \\\n'
    if old_line in text:
        text = text.replace(old_line, new_line, 1)
        changed = True
    if '"${VALIDATOR_ACCOUNTS_ARGS[@]}"' not in text:
        text = text.replace(
            new_line,
            new_line
            + f"    --bootstrap-validator-lamports {bootstrap_balance_lamports} \\\n"
            + f"    --bootstrap-validator-stake-lamports {bootstrap_stake_lamports} \\\n"
            + '    "${VALIDATOR_ACCOUNTS_ARGS[@]}" \\\n',
            1,
        )
        changed = True

    if "--creation-time" not in text:
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.strip().startswith("--cluster-type "):
                lines.insert(index, f'    --creation-time "{genesis_creation_time}" \\\n')
                text = "".join(lines)
                changed = True
                break

    if "bootstrap shared full genesis ledger ready" not in text:
        shared_copy = _bootstrap_genesis_ledger_publish_script()
        marker = "fi\n\n\nSTART_BARRIER_DIR="
        if marker in text:
            text = text.replace(marker, "fi\n" + shared_copy + "\nSTART_BARRIER_DIR=", 1)
        else:
            marker = "fi\n\nSTART_BARRIER_DIR="
            if marker in text:
                text = text.replace(marker, "fi\n" + shared_copy + "START_BARRIER_DIR=", 1)
            else:
                marker = "# 3) Point the local CLI at this node's RPC."
                if marker not in text:
                    return False
                text = text.replace(marker, shared_copy + marker, 1)
        changed = True

    if "genesis start barrier" not in text:
        marker = "# 3) Point the local CLI at this node's RPC."
        if marker not in text:
            return False
        text = text.replace(
            marker,
            _start_barrier_script("$LEDGER_DIR/identity.json", expected_validators) + "\n" + marker,
            1,
        )
        changed = True

    lines = text.splitlines(keepends=True)
    if _ensure_agave_flag(lines, "--no-genesis-fetch"):
        changed = True
    if tpu_disable_quic and _ensure_agave_flag_each_command(lines, "--tpu-disable-quic"):
        changed = True
    if tpu_enable_udp and _ensure_agave_flag_each_command(lines, "--tpu-enable-udp"):
        changed = True

    text = "".join(lines)
    if agave_low_memory_profile:
        text, low_memory_changed = _patch_agave_low_memory_profile(text, agave_low_memory_level)
        changed = changed or low_memory_changed

    if changed:
        path.write_text(text)
    return changed


def _patch_validator_restart_loop(text: str, label: str, delay_var: str) -> tuple[str, bool]:
    if f"{label} agave-validator restart loop" in text:
        return text, False
    if "exec agave-validator \\\n" not in text:
        return text, False

    text = text.replace(
        "exec agave-validator \\\n",
        f'echo "[seedemu-solana] {label} agave-validator restart loop enabled."\n'
        "while true; do\n"
        "  agave-validator \\\n",
        1,
    )
    text = text.replace(
        "  --log -\n",
        "  --log -\n"
        '  status="$?"\n'
        f'  echo "[seedemu-solana] agave-validator exited with status ${{status}}; retrying in ${{{delay_var}:-15}}s ..."\n'
        f'  sleep "${{{delay_var}:-15}}"\n'
        "done\n",
        1,
    )
    return text, True


def _patch_voting_validator_light_rpc(text: str) -> tuple[str, bool]:
    """Keep the node voting, but avoid making every validator a heavy RPC node."""
    changed = False
    for line in (
        "  --full-rpc-api \\\n",
        "  --enable-rpc-transaction-history \\\n",
    ):
        if line in text:
            text = text.replace(line, "")
            changed = True

    if "  --private-rpc \\\n" not in text:
        marker = "  --rpc-bind-address 0.0.0.0 \\\n"
        if marker in text:
            text = text.replace(marker, marker + "  --private-rpc \\\n", 1)
            changed = True

    return text, changed


def _patch_genesis_voting_validator(
    path: Path,
    validator_index: int,
    genesis_config: dict[str, str],
    expected_validators: int,
    core_validator_count: int,
    bootstrap_stake_lamports: int,
    bootstrap_balance_lamports: int,
    tpu_disable_quic: bool,
    tpu_enable_udp: bool,
    agave_low_memory_profile: bool,
    agave_low_memory_level: str,
) -> bool:
    text = path.read_text(errors="ignore")
    if "# seedemu-solana: validator joining the private cluster" not in text:
        return False

    changed = False
    genesis_setup = f"""# Use the bootstrap-generated genesis ledger. This validator's identity,
# vote account, and stake account were already included in that shared genesis,
# so large runs do not need per-validator airdrop/create-vote/delegate RPC setup.
GENESIS_SHARED_DIR={GENESIS_SHARED_TARGET}
GENESIS_LEDGER_TAR="$GENESIS_SHARED_DIR/genesis-ledger.tar"
GENESIS_LEDGER_MARKER="$LEDGER_DIR/.seedemu-genesis-ledger-ready"
if [ ! -f "$GENESIS_LEDGER_MARKER" ]; then
  echo "[seedemu-solana] waiting for bootstrap shared full genesis ledger ..."
  until [ -f "$GENESIS_LEDGER_TAR" ]; do sleep 1; done
  rm -rf "$LEDGER_DIR/rocksdb" "$LEDGER_DIR/accounts_index" \\
    "$LEDGER_DIR/genesis.bin" "$LEDGER_DIR/genesis.tar.bz2" "$LEDGER_DIR/ledger.lock"
  tar -C "$LEDGER_DIR" -xf "$GENESIS_LEDGER_TAR"
  rm -f "$LEDGER_DIR/ledger.lock" "$LEDGER_DIR/rocksdb/LOCK"
  touch "$GENESIS_LEDGER_MARKER"
fi

echo "[seedemu-solana] genesis-preseeded voting validator: shared full genesis ledger ready."

{_start_barrier_script("$LEDGER_DIR/identity.json", expected_validators)}

echo "[seedemu-solana] genesis-preseeded voting validator: account setup skipped."
echo "[seedemu-solana] starting without RPC account setup; entrypoint discovery happens through gossip."

"""

    if "genesis-preseeded voting validator: shared full genesis ledger ready" not in text:
        setup_start = text.find("# Build the same genesis ledger locally.")
        setup_end = text.find("START_BARRIER_DIR=", setup_start)
        if setup_start == -1 or setup_end == -1 or setup_end <= setup_start:
            setup_start = text.find("# Wait for the bootstrap RPC to come up.")
            setup_end = -1
        if setup_end == -1:
            start_marker = 'echo "[seedemu-solana] starting validator on $SELF_IP (entrypoint $ENTRYPOINT)"'
            setup_end = text.find(start_marker)
        else:
            genesis_setup = genesis_setup.rsplit(_start_barrier_script("$LEDGER_DIR/identity.json", expected_validators), 1)[0]
        start_marker = 'echo "[seedemu-solana] starting validator on $SELF_IP (entrypoint $ENTRYPOINT)"'
        if setup_start == -1 or setup_end == -1 or setup_end <= setup_start:
            return False
        text = text[:setup_start] + genesis_setup + text[setup_end:]
        changed = True

    lines = text.splitlines(keepends=True)
    if _ensure_agave_flag(lines, "--no-genesis-fetch"):
        text = "".join(lines)
        changed = True
        lines = text.splitlines(keepends=True)
    if _ensure_agave_flag(lines, "--no-snapshot-fetch"):
        text = "".join(lines)
        changed = True
        lines = text.splitlines(keepends=True)
    if _ensure_agave_flag(lines, "--no-wait-for-vote-to-start-leader"):
        text = "".join(lines)
        changed = True
        lines = text.splitlines(keepends=True)
    if tpu_disable_quic and _ensure_agave_flag_each_command(lines, "--tpu-disable-quic"):
        text = "".join(lines)
        changed = True
        lines = text.splitlines(keepends=True)
    if tpu_enable_udp and _ensure_agave_flag_each_command(lines, "--tpu-enable-udp"):
        text = "".join(lines)
        changed = True

    text, restart_changed = _patch_validator_restart_loop(
        text,
        "genesis voting",
        "VALIDATOR_RESTART_DELAY",
    )
    changed = changed or restart_changed
    text, light_rpc_changed = _patch_voting_validator_light_rpc(text)
    changed = changed or light_rpc_changed

    if agave_low_memory_profile:
        text, low_memory_changed = _patch_agave_low_memory_profile(text, agave_low_memory_level)
        changed = changed or low_memory_changed
        if agave_low_memory_level == "ultra":
            text, rpc_changed = _patch_voting_validator_disable_rpc(text)
            changed = changed or rpc_changed
            if validator_index > core_validator_count:
                peer_ids = [_solana_pubkey("bootstrap:identity")]
                peer_ids.extend(
                    _solana_pubkey(f"validator:{index:03d}:identity")
                    for index in range(1, core_validator_count + 1)
                )
                text, peers_changed = _patch_voting_validator_restricted_peers(text, peer_ids)
                changed = changed or peers_changed

    if changed:
        path.write_text(text)
    return changed


def _patch_validator_stake(
    path: Path,
    validator_index: int,
    stake_sol: int,
    agave_low_memory_profile: bool,
    agave_low_memory_level: str,
) -> bool:
    text = path.read_text(errors="ignore")
    if "# seedemu-solana: validator joining the private cluster" not in text:
        return False
    if "delegate-stake" in text and "--no-wait-for-vote-to-start-leader" in text and not agave_low_memory_profile:
        return False

    stake_keypair_line = (
        '[ -f "$LEDGER_DIR/stake-account.json" ]  || '
        'solana-keygen new --no-passphrase -so "$LEDGER_DIR/stake-account.json"\n'
    )
    stake_block = [
        "\n",
        "# Create and delegate stake so this validator becomes active in the validator set.\n",
        f'VALIDATOR_STAKE_SOL="${{VALIDATOR_STAKE_SOL:-{stake_sol}}}"\n',
        'STAKE_PUBKEY=$(solana-keygen pubkey "$LEDGER_DIR/stake-account.json")\n',
        'VOTE_PUBKEY=$(solana-keygen pubkey "$LEDGER_DIR/vote-account.json")\n',
        'STAKE_STATUS=$(solana --url "$RPC_URL" stake-account "$STAKE_PUBKEY" 2>/dev/null || true)\n',
        'if echo "$STAKE_STATUS" | grep -q "Delegated Stake"; then\n',
        '  echo "[seedemu-solana] stake account already delegated."\n',
        "else\n",
        '  echo "[seedemu-solana] creating/delegating ${VALIDATOR_STAKE_SOL} SOL stake to validator."\n',
        '  until solana --url "$RPC_URL" airdrop "$NODE_SOL" "$IDENTITY_PUBKEY" >/dev/null 2>&1; do\n',
        '    echo "[seedemu-solana] airdrop not ready for stake account, retrying ..."; sleep 2\n',
        "  done\n",
        '  if ! solana --url "$RPC_URL" stake-account "$STAKE_PUBKEY" >/dev/null 2>&1; then\n',
        '    until solana --url "$RPC_URL" create-stake-account "$LEDGER_DIR/stake-account.json" "$VALIDATOR_STAKE_SOL" \\\n',
        '      --from "$LEDGER_DIR/identity.json" \\\n',
        '      --fee-payer "$LEDGER_DIR/identity.json" \\\n',
        '      --stake-authority "$LEDGER_DIR/identity.json" \\\n',
        '      --withdraw-authority "$LEDGER_DIR/withdrawer.json" \\\n',
        '      >/dev/null 2>&1; do\n',
        '      echo "[seedemu-solana] create-stake-account not ready, retrying ..."; sleep 2\n',
        "    done\n",
        "  fi\n",
        '  until solana --url "$RPC_URL" delegate-stake "$LEDGER_DIR/stake-account.json" "$VOTE_PUBKEY" \\\n',
        '    --stake-authority "$LEDGER_DIR/identity.json" \\\n',
        '    --fee-payer "$LEDGER_DIR/identity.json" \\\n',
        '    >/dev/null 2>&1; do\n',
        '    echo "[seedemu-solana] delegate-stake not ready, retrying ..."; sleep 2\n',
        "  done\n",
        "fi\n",
    ]

    lines = text.splitlines(keepends=True)
    changed = False

    if "delegate-stake" not in text:
        for index, line in enumerate(lines):
            if 'withdrawer.json" ]' in line:
                lines.insert(index + 1, stake_keypair_line)
                changed = True
                break
        else:
            return False

        create_vote_index = next(
            (index for index, line in enumerate(lines) if "create-vote-account" in line),
            None,
        )
        if create_vote_index is None:
            return False

        insert_at = next(
            (index + 1 for index in range(create_vote_index, len(lines)) if lines[index].strip() == "fi"),
            None,
        )
        if insert_at is None:
            return False

        lines[insert_at:insert_at] = stake_block
        changed = True

    if _ensure_agave_flag(lines, "--no-wait-for-vote-to-start-leader"):
        changed = True

    text = "".join(lines)
    text, light_rpc_changed = _patch_core_validator_light_public_rpc(text)
    changed = changed or light_rpc_changed

    if agave_low_memory_profile:
        text, low_memory_changed = _patch_agave_low_memory_profile(text, agave_low_memory_level)
        changed = changed or low_memory_changed
        if agave_low_memory_level == "ultra":
            text, rpc_changed = _patch_core_validator_light_public_rpc(text)
            changed = changed or rpc_changed

    if changed:
        path.write_text(text)
    return changed


def _patch_non_staked_no_voting(
    path: Path,
    validator_index: int,
    core_validator_count: int,
    core_peer_ids: list[str],
    core_entrypoints: list[str],
    agave_low_memory_profile: bool,
    agave_low_memory_level: str,
) -> bool:
    text = path.read_text(errors="ignore")
    if "# seedemu-solana: validator joining the private cluster" not in text:
        return False
    changed = False

    observer_setup = """# Wait for the bootstrap RPC to come up. Non-staked observers do not
# need RPC-funded vote/stake accounts; avoiding per-node airdrops keeps large
# clusters from overloading the bootstrap RPC before the observers start.
echo "[seedemu-solana] waiting for bootstrap RPC at $RPC_URL ..."
until solana --url "$RPC_URL" cluster-version >/dev/null 2>&1; do sleep 2; done
echo "[seedemu-solana] non-staked observer mode: skipping airdrop/vote-account setup; validator will restart until RPC peers are discoverable."

"""

    if "[seedemu-solana] non-staked observer mode" not in text:
        setup_start = text.find("# Wait for the bootstrap RPC to come up.")
        start_marker = 'echo "[seedemu-solana] starting validator on $SELF_IP (entrypoint $ENTRYPOINT)"'
        setup_end = text.find(start_marker)
        if setup_start == -1 or setup_end == -1 or setup_end <= setup_start:
            return False
        text = text[:setup_start] + observer_setup + text[setup_end:]
        changed = True

    if '  --vote-account "$LEDGER_DIR/vote-account.json" \\\n' in text:
        text = text.replace('  --vote-account "$LEDGER_DIR/vote-account.json" \\\n', "")
        changed = True
    if "  --no-voting \\\n" not in text:
        text = text.replace(
            '  --entrypoint "$ENTRYPOINT" \\\n',
            '  --entrypoint "$ENTRYPOINT" \\\n  --no-voting \\\n',
        )
        changed = True
    if "  --private-rpc \\\n" not in text:
        text = text.replace(
            "  --rpc-bind-address 0.0.0.0 \\\n",
            "  --rpc-bind-address 0.0.0.0 \\\n  --private-rpc \\\n",
        )
        changed = True
    if "  --full-rpc-api \\\n" in text:
        text = text.replace("  --full-rpc-api \\\n", "")
        changed = True
    if "  --enable-rpc-transaction-history \\\n" in text:
        text = text.replace("  --enable-rpc-transaction-history \\\n", "")
        changed = True
    if "observer agave-validator restart loop" not in text:
        text = text.replace(
            "exec agave-validator \\\n",
            'echo "[seedemu-solana] observer agave-validator restart loop enabled."\n'
            "while true; do\n"
            "  agave-validator \\\n",
            1,
        )
        text = text.replace(
            "  --log -\n",
            '  --log -\n'
            '  status="$?"\n'
            '  echo "[seedemu-solana] agave-validator exited with status ${status}; retrying in ${OBSERVER_RESTART_DELAY:-15}s ..."\n'
            '  sleep "${OBSERVER_RESTART_DELAY:-15}"\n'
            "done\n",
            1,
        )
        changed = True

    if agave_low_memory_profile:
        text, low_memory_changed = _patch_agave_low_memory_profile(text, agave_low_memory_level)
        changed = changed or low_memory_changed
        if agave_low_memory_level == "ultra":
            text, rpc_changed = _patch_voting_validator_disable_rpc(text)
            changed = changed or rpc_changed
            if validator_index > core_validator_count:
                text, peers_changed = _patch_voting_validator_restricted_peers(text, core_peer_ids)
                changed = changed or peers_changed
                text, entrypoints_changed = _patch_validator_extra_entrypoints(text, core_entrypoints)
                changed = changed or entrypoints_changed

    if changed:
        path.write_text(text)
    return changed


def patch_runtime_scripts(
    output_dir: Path,
    slots_per_epoch: int,
    disable_warmup_epochs: bool,
    target_tick_duration_ms: int,
    ticks_per_slot: int,
    stake_sol: int,
    staked_validators: int,
    non_staked_no_voting: bool,
    genesis_voting_validators: bool,
    genesis_start_barrier_validators: int,
    genesis_creation_time: str,
    genesis_bootstrap_stake_lamports: int,
    genesis_bootstrap_balance_lamports: int,
    genesis_core_validator_count: int,
    tpu_disable_quic: bool,
    tpu_enable_udp: bool,
    agave_low_memory_profile: bool,
    agave_low_memory_level: str,
) -> dict[str, int]:
    bootstrap_updates = 0
    bootstrap_genesis_updates = 0
    validator_updates = 0
    non_staked_no_voting_updates = 0
    genesis_validator_updates = 0
    validator_indices = _validator_script_indices(output_dir)
    validator_self_ips = _validator_self_ips(output_dir, validator_indices)
    expected_genesis_validators = len(validator_indices) + 1
    if staked_validators < 0:
        legacy_core_validator_count = max(0, expected_genesis_validators - 1)
    else:
        legacy_core_validator_count = max(0, min(staked_validators, expected_genesis_validators - 1))
    legacy_core_peer_ids = _core_peer_ids(legacy_core_validator_count) if non_staked_no_voting else []
    if genesis_start_barrier_validators == 0:
        genesis_start_barrier_validators = expected_genesis_validators
    if genesis_start_barrier_validators < 1:
        raise PatchError("--genesis-start-barrier-validators must be 0 or a positive integer")
    if genesis_start_barrier_validators > expected_genesis_validators:
        raise PatchError(
            "--genesis-start-barrier-validators cannot exceed the number of genesis voting validators"
        )
    bootstrap_genesis_config: dict[str, str] | None = None

    for path in output_dir.glob("hnode_*/*"):
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        if "# seedemu-solana: genesis / bootstrap validator" in text:
            bootstrap_genesis_config = _parse_bootstrap_genesis_config(
                path,
                slots_per_epoch,
                genesis_creation_time,
            )
            break

    if genesis_voting_validators and bootstrap_genesis_config is None:
        raise PatchError("could not find Solana bootstrap script for genesis-voting validator patch")

    for path in output_dir.glob("hnode_*/*"):
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        is_bootstrap = "# seedemu-solana: genesis / bootstrap validator" in text
        if is_bootstrap and _patch_genesis_epoch_config(
            path,
            slots_per_epoch,
            disable_warmup_epochs,
            target_tick_duration_ms,
            ticks_per_slot,
        ):
            bootstrap_updates += 1
        if is_bootstrap and genesis_voting_validators:
            if _patch_bootstrap_genesis_voting(
                path,
                genesis_creation_time,
                genesis_start_barrier_validators,
                genesis_bootstrap_stake_lamports,
                genesis_bootstrap_balance_lamports,
                tpu_disable_quic,
                tpu_enable_udp,
                agave_low_memory_profile,
                agave_low_memory_level,
            ):
                bootstrap_genesis_updates += 1
            continue
        validator_index = validator_indices.get(path.parent)
        if validator_index is None:
            continue
        if genesis_voting_validators:
            assert bootstrap_genesis_config is not None
            if _patch_genesis_voting_validator(
                path,
                validator_index,
                bootstrap_genesis_config,
                genesis_start_barrier_validators,
                genesis_core_validator_count,
                genesis_bootstrap_stake_lamports,
                genesis_bootstrap_balance_lamports,
                tpu_disable_quic,
                tpu_enable_udp,
                agave_low_memory_profile,
                agave_low_memory_level,
            ):
                genesis_validator_updates += 1
            continue
        if staked_validators >= 0 and validator_index > staked_validators:
            if non_staked_no_voting and _patch_non_staked_no_voting(
                path,
                validator_index,
                legacy_core_validator_count,
                legacy_core_peer_ids,
                _core_entrypoints(validator_self_ips, legacy_core_validator_count, validator_index),
                agave_low_memory_profile,
                agave_low_memory_level,
            ):
                non_staked_no_voting_updates += 1
            continue
        if _patch_validator_stake(
            path,
            validator_index,
            stake_sol,
            agave_low_memory_profile,
            agave_low_memory_level,
        ):
            validator_updates += 1

    return {
        "bootstrap_script_updates": bootstrap_updates,
        "bootstrap_genesis_voting_script_updates": bootstrap_genesis_updates,
        "validator_script_updates": validator_updates,
        "non_staked_no_voting_updates": non_staked_no_voting_updates,
        "genesis_voting_validator_script_updates": genesis_validator_updates,
        "genesis_start_barrier_validators": genesis_start_barrier_validators,
        "legacy_core_validator_count": legacy_core_validator_count,
        "legacy_core_entrypoint_count": min(legacy_core_validator_count, len(validator_self_ips)),
    }


def patch_output(
    output_dir: Path,
    slots_per_epoch: int,
    disable_warmup_epochs: bool,
    target_tick_duration_ms: int,
    ticks_per_slot: int,
    stake_sol: int,
    nofile: int,
    staked_validators: int,
    non_staked_no_voting: bool,
    genesis_voting_validators: bool,
    genesis_start_barrier_validators: int,
    genesis_creation_time: str,
    genesis_bootstrap_stake_lamports: int,
    genesis_bootstrap_balance_lamports: int,
    genesis_validator_stake_lamports: int,
    genesis_validator_balance_lamports: int,
    genesis_core_validator_count: int,
    genesis_core_validator_stake_lamports: int,
    tpu_disable_quic: bool,
    tpu_enable_udp: bool,
    agave_low_memory_profile: bool,
    agave_low_memory_level: str,
) -> dict[str, Any]:
    compose_file = output_dir / "docker-compose.yml"
    if not compose_file.exists():
        raise PatchError(f"compose file not found: {compose_file}")
    if genesis_voting_validators and non_staked_no_voting:
        raise PatchError("--genesis-voting-validators conflicts with --non-staked-no-voting")

    ulimit_updates = inject_ulimits(compose_file, nofile)
    validator_indices = _validator_script_indices(output_dir)
    genesis_updates: dict[str, int] = {}
    deterministic_keypair_updates: dict[str, int] = {}
    genesis_shared_mount_updates = 0
    start_barrier_mount_updates = 0
    if genesis_voting_validators:
        genesis_updates = install_genesis_voting_keypairs(
            output_dir,
            validator_indices,
            genesis_validator_stake_lamports,
            genesis_validator_balance_lamports,
            genesis_core_validator_count,
            genesis_core_validator_stake_lamports,
        )
        genesis_shared_mount_updates = mount_genesis_shared_dir(output_dir)
        start_barrier_mount_updates = mount_start_barrier_dir(output_dir)
    elif non_staked_no_voting:
        deterministic_keypair_updates = install_deterministic_runtime_keypairs(output_dir, validator_indices)
    runtime_updates = patch_runtime_scripts(
        output_dir,
        slots_per_epoch,
        disable_warmup_epochs,
        target_tick_duration_ms,
        ticks_per_slot,
        stake_sol,
        staked_validators,
        non_staked_no_voting,
        genesis_voting_validators,
        genesis_start_barrier_validators,
        genesis_creation_time,
        genesis_bootstrap_stake_lamports,
        genesis_bootstrap_balance_lamports,
        genesis_core_validator_count,
        tpu_disable_quic,
        tpu_enable_udp,
        agave_low_memory_profile,
        agave_low_memory_level,
    )
    summary: dict[str, Any] = {
        "output_dir": str(output_dir),
        "compose_file": str(compose_file),
        "nofile": nofile,
        "ulimit_service_updates": ulimit_updates,
        "slots_per_epoch": slots_per_epoch,
        "disable_warmup_epochs": disable_warmup_epochs,
        "target_tick_duration_ms": target_tick_duration_ms,
        "ticks_per_slot": ticks_per_slot,
        "validator_stake_sol": stake_sol,
        "staked_validators": staked_validators,
        "non_staked_no_voting": non_staked_no_voting,
        "genesis_voting_validators_enabled": genesis_voting_validators,
        "requested_genesis_start_barrier_validators": genesis_start_barrier_validators,
        "genesis_creation_time": genesis_creation_time,
        "genesis_bootstrap_stake_lamports": genesis_bootstrap_stake_lamports,
        "genesis_bootstrap_balance_lamports": genesis_bootstrap_balance_lamports,
        "genesis_validator_stake_lamports": genesis_validator_stake_lamports,
        "genesis_validator_balance_lamports": genesis_validator_balance_lamports,
        "genesis_core_validator_count": genesis_core_validator_count,
        "genesis_core_validator_stake_lamports": genesis_core_validator_stake_lamports,
        "tpu_disable_quic": tpu_disable_quic,
        "tpu_enable_udp": tpu_enable_udp,
        "agave_low_memory_profile": agave_low_memory_profile,
        "agave_low_memory_level": agave_low_memory_level if agave_low_memory_profile else None,
        "agave_low_memory_options": (
            dict(_agave_low_memory_options(agave_low_memory_level)) if agave_low_memory_profile else {}
        ),
        "agave_low_memory_flags": (
            list(_agave_low_memory_flags(agave_low_memory_level)) if agave_low_memory_profile else []
        ),
        "genesis_shared_mount_updates": genesis_shared_mount_updates,
        "start_barrier_mount_updates": start_barrier_mount_updates,
        **runtime_updates,
        **deterministic_keypair_updates,
        **genesis_updates,
    }
    (output_dir / SUMMARY_NAME).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patch generated Solana output for D61 large-scale runs.")
    parser.add_argument("--output", default="./output")
    parser.add_argument("--slots-per-epoch", type=int, default=64)
    parser.add_argument(
        "--target-tick-duration-ms",
        type=int,
        default=0,
        help="Optional solana-genesis --target-tick-duration value in milliseconds. 0 leaves the generated default.",
    )
    parser.add_argument(
        "--ticks-per-slot",
        type=int,
        default=0,
        help="Optional solana-genesis --ticks-per-slot value. 0 leaves the generated default.",
    )
    parser.add_argument(
        "--disable-warmup-epochs",
        action="store_true",
        help="Remove solana-genesis --enable-warmup-epochs so --slots-per-epoch applies from epoch 0.",
    )
    parser.add_argument("--validator-stake-sol", type=int, default=2)
    parser.add_argument(
        "--staked-validators",
        type=int,
        default=-1,
        help="Legacy mode: number of non-bootstrap validators to stake/delegate at runtime. -1 means all.",
    )
    parser.add_argument(
        "--non-staked-no-voting",
        action="store_true",
        help="Legacy observer mode: patch non-bootstrap validators outside --staked-validators to run with --no-voting.",
    )
    parser.add_argument(
        "--genesis-voting-validators",
        action="store_true",
        help="Preload all validators into genesis and patch non-bootstrap validators to start voting without runtime account setup.",
    )
    parser.add_argument(
        "--genesis-start-barrier-validators",
        type=int,
        default=0,
        help=(
            "Genesis-voting startup barrier size. 0 means wait for all genesis voting validators; "
            "use a smaller value for staged startup."
        ),
    )
    parser.add_argument(
        "--genesis-creation-time",
        default="2026-01-01T00:00:00Z",
        help="Fixed RFC3339 creation time for locally generated genesis ledgers.",
    )
    parser.add_argument(
        "--genesis-bootstrap-stake-lamports",
        type=int,
        default=500_000_000_000,
        help="Stake lamports assigned to the bootstrap validator in genesis-voting mode.",
    )
    parser.add_argument(
        "--genesis-bootstrap-balance-lamports",
        type=int,
        default=500_000_000_000,
        help="Identity-account lamports assigned to the bootstrap validator in genesis-voting mode.",
    )
    parser.add_argument(
        "--genesis-validator-stake-lamports",
        type=int,
        default=500_000_000,
        help="Stake lamports assigned to each non-bootstrap validator in genesis-voting mode.",
    )
    parser.add_argument(
        "--genesis-validator-balance-lamports",
        type=int,
        default=500_000_000_000,
        help="Identity-account lamports assigned to each non-bootstrap validator in genesis-voting mode.",
    )
    parser.add_argument(
        "--genesis-core-validator-count",
        type=int,
        default=0,
        help="Number of non-bootstrap validators that receive the core validator stake in genesis-voting mode.",
    )
    parser.add_argument(
        "--genesis-core-validator-stake-lamports",
        type=int,
        default=10_000_000_000,
        help="Stake lamports assigned to the first N non-bootstrap validators selected by --genesis-core-validator-count.",
    )
    parser.add_argument(
        "--tpu-disable-quic",
        action="store_true",
        help="Add agave-validator --tpu-disable-quic to bootstrap and validator command blocks.",
    )
    parser.add_argument(
        "--tpu-enable-udp",
        action="store_true",
        help="Add agave-validator --tpu-enable-udp to bootstrap and validator command blocks.",
    )
    parser.add_argument(
        "--agave-low-memory-profile",
        action="store_true",
        help="Add conservative Agave cache/RPC/snapshot flags for high-density validator runs.",
    )
    parser.add_argument(
        "--agave-low-memory-level",
        choices=("standard", "aggressive", "ultra"),
        default="standard",
        help="Low-memory profile level used when --agave-low-memory-profile is set.",
    )
    parser.add_argument("--nofile", type=int, default=1_000_000)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.staked_validators < -1:
        raise PatchError("--staked-validators must be -1 or a non-negative integer")
    if args.target_tick_duration_ms < 0:
        raise PatchError("--target-tick-duration-ms must be a non-negative integer")
    if args.ticks_per_slot < 0:
        raise PatchError("--ticks-per-slot must be a non-negative integer")
    for name in (
        "genesis_bootstrap_stake_lamports",
        "genesis_bootstrap_balance_lamports",
        "genesis_validator_stake_lamports",
        "genesis_validator_balance_lamports",
        "genesis_core_validator_stake_lamports",
    ):
        if getattr(args, name) <= 0:
            raise PatchError(f"--{name.replace('_', '-')} must be positive")
    if args.genesis_core_validator_count < 0:
        raise PatchError("--genesis-core-validator-count must be non-negative")
    if args.genesis_start_barrier_validators < 0:
        raise PatchError("--genesis-start-barrier-validators must be non-negative")
    output_dir = Path(args.output).resolve()
    if not output_dir.is_dir():
        raise PatchError(f"output directory not found: {output_dir}")
    summary = patch_output(
        output_dir=output_dir,
        slots_per_epoch=args.slots_per_epoch,
        disable_warmup_epochs=args.disable_warmup_epochs,
        target_tick_duration_ms=args.target_tick_duration_ms,
        ticks_per_slot=args.ticks_per_slot,
        stake_sol=args.validator_stake_sol,
        nofile=args.nofile,
        staked_validators=args.staked_validators,
        non_staked_no_voting=args.non_staked_no_voting,
        genesis_voting_validators=args.genesis_voting_validators,
        genesis_start_barrier_validators=args.genesis_start_barrier_validators,
        genesis_creation_time=args.genesis_creation_time,
        genesis_bootstrap_stake_lamports=args.genesis_bootstrap_stake_lamports,
        genesis_bootstrap_balance_lamports=args.genesis_bootstrap_balance_lamports,
        genesis_validator_stake_lamports=args.genesis_validator_stake_lamports,
        genesis_validator_balance_lamports=args.genesis_validator_balance_lamports,
        genesis_core_validator_count=args.genesis_core_validator_count,
        genesis_core_validator_stake_lamports=args.genesis_core_validator_stake_lamports,
        tpu_disable_quic=args.tpu_disable_quic,
        tpu_enable_udp=args.tpu_enable_udp,
        agave_low_memory_profile=args.agave_low_memory_profile,
        agave_low_memory_level=args.agave_low_memory_level,
    )
    print(
        "solana output patch complete: "
        f"ulimits={summary['ulimit_service_updates']} "
        f"bootstrap={summary['bootstrap_script_updates']} "
        f"validators={summary['validator_script_updates']} "
        f"genesis_voting={summary.get('genesis_voting_validators', 0)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except PatchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
