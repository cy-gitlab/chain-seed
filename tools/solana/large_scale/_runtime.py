"""Compile-time helpers for the final genesis-voting Solana deployment."""
from __future__ import annotations
import json
from pathlib import Path
import re
from typing import Any
import yaml
from _keys import _solana_keypair, _solana_pubkey

class PatchError(RuntimeError):
    pass

GENESIS_SHARED_DIR_NAME = "solana_genesis_shared"


GENESIS_SHARED_TARGET = "/opt/solana/config/genesis-shared"


GENESIS_VALIDATOR_ACCOUNTS_FILE = "validator-accounts.yml"


START_BARRIER_DIR_NAME = "solana_start_barrier"


START_BARRIER_TARGET = "/opt/solana/config/start-barrier"


PATCH_HELPER_DIR_NAMES = {GENESIS_SHARED_DIR_NAME, START_BARRIER_DIR_NAME}


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
    target_tick_duration: int,
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
    if target_tick_duration > 0 and _ensure_genesis_option(
        lines,
        "--target-tick-duration",
        target_tick_duration,
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
