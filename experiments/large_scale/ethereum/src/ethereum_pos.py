#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

from chainseed.compiler import ChainSeedDocker
from chainseed.common.systems import ETHEREUM_POS
import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# The repository's Ethereum helpers were written against web3.py v5, while
# this experiment runner may use a newer host-side web3.py for tx generation.
try:
    from web3 import Web3
    from eth_account.signers.local import LocalAccount

    if not hasattr(Web3, "toChecksumAddress") and hasattr(Web3, "to_checksum_address"):
        Web3.toChecksumAddress = Web3.to_checksum_address  # type: ignore[attr-defined]
    if not hasattr(LocalAccount, "privateKey") and hasattr(LocalAccount, "key"):
        LocalAccount.privateKey = property(lambda self: self.key)  # type: ignore[attr-defined]
except ImportError:
    pass

from seedemu import *
from chainseed.ethereum import *


DOMAIN = ".large-pos.net"

DEFAULT_BEACON_COUNT = 300
DEFAULT_STUB_AS_COUNT = 64
DEFAULT_BEACON_PEERS = 16
DEFAULT_BEACON_BOOTNODES = 4
DEFAULT_GETH_BOOTNODES = 3
DEFAULT_BEACON_HISTORIC_STATES = "all"
DEFAULT_LIGHTHOUSE_CACHE_PROFILE = "default"
DEFAULT_VALIDATOR_KEYS_PER_CLIENT = 1
DEFAULT_LOCAL_ACCOUNT_COUNT = 20
DEFAULT_LIGHTHOUSE_METRICS_PORT = 5054
LIGHTHOUSE_LOW_CACHE_ARGS = (
    "--state-cache-size 16 "
    "--block-cache-size 1 "
    "--hdiff-buffer-cache-size 1 "
    "--shuffling-cache-size 1"
)
LIGHTHOUSE_MANAGED_CACHE_FLAGS = (
    "--state-cache-size",
    "--block-cache-size",
    "--hdiff-buffer-cache-size",
    "--shuffling-cache-size",
)

STUB_AS_START = 150
MAX_STUB_AS_COUNT = 100
MAX_HOSTS_PER_STUB_AS = 129

IXES: Dict[int, str] = {
    100: "New-York-100",
    101: "San-Jose-101",
    102: "Chicago-102",
    103: "Atlanta-103",
    104: "Dallas-104",
    105: "Seattle-105",
    106: "Amsterdam-106",
    107: "Singapore-107",
    108: "Frankfurt-108",
    109: "Tokyo-109",
}

TIER1_TRANSITS: Dict[int, List[int]] = {
    2: [100, 101, 102, 105],
    3: [100, 103, 104, 106],
    4: [101, 102, 104, 107],
    5: [102, 103, 105, 106],
    6: [100, 104, 105, 107],
    7: [103, 106, 108, 109],
    8: [104, 107, 108, 109],
}

REGIONAL_TRANSITS: Dict[int, List[int]] = {
    20: [100, 101],
    21: [101, 105],
    22: [102, 104],
    23: [103, 104],
    24: [105, 106],
    25: [106, 107],
    26: [100, 102, 103],
    27: [104, 105, 107],
    28: [108, 109],
    29: [100, 108],
    30: [103, 109],
    31: [104, 108],
    32: [105, 109],
    33: [102, 108, 109],
}

REGIONAL_UPSTREAMS: Dict[int, List[Tuple[int, int]]] = {
    20: [(100, 2), (101, 4)],
    21: [(101, 2), (105, 6)],
    22: [(102, 5), (104, 4)],
    23: [(103, 3), (104, 6)],
    24: [(105, 5), (106, 3)],
    25: [(106, 3), (107, 4)],
    26: [(100, 2), (102, 5), (103, 3)],
    27: [(104, 4), (105, 6), (107, 6)],
    28: [(108, 7), (109, 8)],
    29: [(100, 2), (108, 7)],
    30: [(103, 3), (109, 7)],
    31: [(104, 8), (108, 7)],
    32: [(105, 6), (109, 8)],
    33: [(102, 5), (108, 7), (109, 8)],
}


@dataclass(frozen=True)
class Placement:
    asn: int
    host: str


class PlacementPlanner:
    """Assign one Ethereum service vnode to one physical host."""

    def __init__(self, stub_asns: Sequence[int]):
        self._stub_asns = list(stub_asns)
        self._next_host_index = {asn: 0 for asn in self._stub_asns}
        self._round_robin_index = 0
        self.placements: Dict[str, Placement] = {}

    def place(self, vnode: str, preferred_asn: Optional[int] = None) -> Placement:
        if preferred_asn is None:
            preferred_asn = self._stub_asns[self._round_robin_index % len(self._stub_asns)]
            self._round_robin_index += 1

        assert preferred_asn in self._next_host_index, f"unknown stub AS{preferred_asn}"

        host_index = self._next_host_index[preferred_asn]
        self._next_host_index[preferred_asn] += 1
        placement = Placement(preferred_asn, f"host_{host_index:03d}")
        self.placements[vnode] = placement
        return placement

    def host_counts(self) -> Dict[int, int]:
        return dict(self._next_host_index)


def _validate_counts(
    beacon_count: int,
    geth_count: int,
    validator_count: int,
    stub_as_count: int,
    beacon_bootnodes: int,
    geth_bootnodes: int,
) -> None:
    assert beacon_count >= 1, "beacon_count must be >= 1"
    assert geth_count == beacon_count, "geth_count must equal beacon_count"
    assert validator_count == beacon_count * 3, (
        "validator_count must equal beacon_count * 3"
    )
    assert validator_count >= 4, "validator_count must be >= 4 for this genesis config"
    assert 1 <= stub_as_count <= MAX_STUB_AS_COUNT, (
        f"stub_as_count must be between 1 and {MAX_STUB_AS_COUNT}"
    )
    assert 1 <= beacon_bootnodes <= beacon_count, "beacon_bootnodes must be in [1, beacon_count]"
    assert 1 <= geth_bootnodes <= geth_count, "geth_bootnodes must be in [1, geth_count]"


def _stub_asns(stub_as_count: int) -> List[int]:
    return list(range(STUB_AS_START, STUB_AS_START + stub_as_count))


def _derive_geth_count(beacon_count: int, requested_geth_count: Optional[int]) -> int:
    geth_count = beacon_count if requested_geth_count is None else requested_geth_count
    assert geth_count == beacon_count, "--geth must equal --beacons for this topology"
    return geth_count


def _derive_validator_count(
    beacon_count: int,
    requested_validator_count: Optional[int],
) -> int:
    validator_count = beacon_count * 3 if requested_validator_count is None else requested_validator_count
    assert validator_count == beacon_count * 3, (
        "--validators must equal 3 * --beacons for this topology"
    )
    return validator_count


def _build_service_placements(
    stub_asns: Sequence[int],
    beacon_count: int,
    geth_count: int,
    validator_count: int,
    include_tools: bool,
) -> PlacementPlanner:
    planner = PlacementPlanner(stub_asns)
    stub_count = len(stub_asns)

    planner.place("beaconsetup", stub_asns[0])

    for i in range(beacon_count):
        group_asn = stub_asns[i % stub_count]
        planner.place(f"gethnode{i:03d}", group_asn)
        planner.place(f"beaconnode{i:03d}", group_asn)
        for offset in range(3):
            validator_index = i * 3 + offset
            planner.place(f"vcnode{validator_index:03d}", group_asn)

    if include_tools:
        planner.place("faucet", stub_asns[1 % stub_count])
        planner.place("utility", stub_asns[2 % stub_count])

    return planner


def _asns_by_ix(transits: Dict[int, List[int]]) -> Dict[int, List[int]]:
    by_ix = {ix: [] for ix in IXES}
    for asn, exchanges in transits.items():
        for ix in exchanges:
            by_ix[ix].append(asn)
    return by_ix


def _backbone_links(exchanges: Sequence[int]) -> List[Tuple[int, int]]:
    links = [(exchanges[i], exchanges[i + 1]) for i in range(len(exchanges) - 1)]
    if len(exchanges) > 3:
        links.append((exchanges[0], exchanges[-1]))
    return links


def _backbone_name(left_ix: int, right_ix: int) -> str:
    return f"bb{left_ix}_{right_ix}"


def _create_transit_as(base: Base, asn: int, exchanges: Sequence[int]) -> None:
    transit_as = base.createAutonomousSystem(asn)
    routers = {}

    for ix in exchanges:
        routers[ix] = transit_as.createRouter(f"r{ix}")
        routers[ix].joinNetwork(f"ix{ix}")

    for index, (left_ix, right_ix) in enumerate(_backbone_links(exchanges)):
        net = transit_as.createNetwork(_backbone_name(left_ix, right_ix))
        net.setDefaultLinkProperties(
            latency=2 + (index % 4),
            bandwidth=1_000_000_000,
            packetDrop=0,
        )
        routers[left_ix].joinNetwork(net.getName())
        routers[right_ix].joinNetwork(net.getName())


def _create_stub_as(
    base: Base,
    asn: int,
    host_count: int,
    primary_ix: int,
    secondary_ix: Optional[int],
    stub_index: int,
) -> None:
    stub_as = base.createAutonomousSystem(asn)
    net = stub_as.createNetwork("net0")
    net.setHostIpRange(hostStart=71, hostEnd=199, hostStep=1)
    net.setDefaultLinkProperties(
        latency=1 + (stub_index % 6),
        bandwidth=250_000_000,
        packetDrop=0,
    )

    router = stub_as.createRouter("router0")
    router.joinNetwork("net0")
    router.joinNetwork(f"ix{primary_ix}")
    if secondary_ix is not None and secondary_ix != primary_ix:
        router.joinNetwork(f"ix{secondary_ix}")

    assert host_count <= MAX_HOSTS_PER_STUB_AS, (
        f"AS{asn} needs {host_count} hosts, but net0 only supports "
        f"{MAX_HOSTS_PER_STUB_AS}. Increase --stub-as-count."
    )
    for host_id in range(host_count):
        stub_as.createHost(f"host_{host_id:03d}").joinNetwork("net0")


def _safe_private_peering(
    ebgp: Ebgp,
    seen: set[Tuple[int, int, int]],
    ix: int,
    provider: int,
    customer: int,
    relationship: PeerRelationship = PeerRelationship.Provider,
) -> None:
    key = (ix, provider, customer)
    if key in seen:
        return
    ebgp.addPrivatePeering(ix, provider, customer, relationship)
    seen.add(key)


def build_large_topology(stub_asns: Sequence[int], host_counts: Dict[int, int]) -> Emulator:
    emu = Emulator()
    base = Base()
    routing = Routing()
    ebgp = Ebgp()
    ibgp = Ibgp()
    ospf = Ospf()

    for ix, display_name in IXES.items():
        base.createInternetExchange(ix).getPeeringLan().setDisplayName(display_name)

    for asn, exchanges in TIER1_TRANSITS.items():
        _create_transit_as(base, asn, exchanges)

    for asn, exchanges in REGIONAL_TRANSITS.items():
        _create_transit_as(base, asn, exchanges)

    ix_sequence = list(IXES.keys())
    stub_profiles: Dict[int, Tuple[int, Optional[int]]] = {}
    for index, asn in enumerate(stub_asns):
        primary_ix = ix_sequence[index % len(ix_sequence)]
        secondary_ix = ix_sequence[(index * 3 + 1) % len(ix_sequence)] if index % 4 == 0 else None
        stub_profiles[asn] = (primary_ix, secondary_ix)
        _create_stub_as(
            base=base,
            asn=asn,
            host_count=host_counts.get(asn, 0),
            primary_ix=primary_ix,
            secondary_ix=secondary_ix,
            stub_index=index,
        )

    transit_by_ix = _asns_by_ix({**TIER1_TRANSITS, **REGIONAL_TRANSITS})
    for ix, peers in transit_by_ix.items():
        if peers:
            ebgp.addRsPeers(ix, sorted(peers))

    seen_private_peerings: set[Tuple[int, int, int]] = set()

    for regional_asn, upstreams in REGIONAL_UPSTREAMS.items():
        for ix, provider_asn in upstreams:
            _safe_private_peering(ebgp, seen_private_peerings, ix, provider_asn, regional_asn)

    tier1_by_ix = _asns_by_ix(TIER1_TRANSITS)
    regional_by_ix = _asns_by_ix(REGIONAL_TRANSITS)

    for index, stub_asn in enumerate(stub_asns):
        primary_ix, secondary_ix = stub_profiles[stub_asn]
        primary_pool = tier1_by_ix[primary_ix] if index % 7 == 0 else regional_by_ix[primary_ix]
        primary_provider = primary_pool[index % len(primary_pool)]
        _safe_private_peering(ebgp, seen_private_peerings, primary_ix, primary_provider, stub_asn)

        if secondary_ix is not None and secondary_ix != primary_ix:
            # A stub AS uses one border router for both IX links. Peering with the
            # same provider on both IXes creates duplicate BIRD protocol names.
            secondary_pool = [
                provider for provider in tier1_by_ix[secondary_ix]
                if provider != primary_provider
            ]
            if not secondary_pool:
                secondary_pool = tier1_by_ix[secondary_ix]
            secondary_provider = secondary_pool[(index // 4) % len(secondary_pool)]
            _safe_private_peering(
                ebgp,
                seen_private_peerings,
                secondary_ix,
                secondary_provider,
                stub_asn,
            )

    for offset in range(0, max(0, len(stub_asns) - len(ix_sequence)), len(ix_sequence)):
        left = stub_asns[offset]
        right = stub_asns[offset + len(ix_sequence)]
        ix, _ = stub_profiles[left]
        _safe_private_peering(
            ebgp,
            seen_private_peerings,
            ix,
            left,
            right,
            relationship=PeerRelationship.Peer,
        )

    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    return emu


def _bind(emu: Emulator, vnode: str, placement: Placement) -> None:
    emu.addBinding(
        Binding(
            vnode,
            filter=Filter(asn=placement.asn, nodeName=f"^{placement.host}$"),
            action=Action.FIRST,
        )
    )


def add_ethereum_pos_layer(
    emu: Emulator,
    placements: Dict[str, Placement],
    beacon_count: int,
    geth_count: int,
    validator_count: int,
    beacon_peers: int,
    beacon_bootnodes: int,
    geth_bootnodes: int,
    include_tools: bool,
    local_account_count: int,
) -> None:
    eth = EthereumService()
    blockchain = eth.createBlockchain(
        chainName="pos-large",
        consensus=ConsensusMechanism.POS,
        chainId=1337,
    )
    blockchain.setLocalAccountParameters(
        mnemonic="gentle always fun glass foster produce north tail security list example gain",
        balance=1_000_000,
        total=local_account_count,
    )
    blockchain.setTargetCommitteeSize(4)
    blockchain.setTargetAggregatorPerCommittee(2)

    setup: PoSBeaconSetupServer = blockchain.createBeaconSetupNode("beaconsetup")
    setup.setDisplayName("Ethereum-POS-BeaconSetup")
    setup.appendClassName("Ethereum-POS-BeaconSetup")
    setup.addHostName(f"beaconsetup{DOMAIN}")
    _bind(emu, "beaconsetup", placements["beaconsetup"])

    geth_nodes: List[PoSGethServer] = []
    for i in range(geth_count):
        vnode = f"gethnode{i:03d}"
        geth: PoSGethServer = blockchain.createGethNode(vnode)
        geth.enableGethHttp()
        geth.appendClassName("Ethereum-POS-Geth")
        geth.appendClassName(f"Ethereum-POS-Geth-{i:03d}")
        geth.setDisplayName(f"Ethereum-POS-Geth-{i:03d}")
        geth.addHostName(f"{vnode}{DOMAIN}")
        if i < geth_bootnodes:
            geth.setBootNode(True)
        geth_nodes.append(geth)
        _bind(emu, vnode, placements[vnode])

    beacon_nodes: List[PoSBeaconServer] = []
    for i in range(beacon_count):
        vnode = f"beaconnode{i:03d}"
        beacon: PoSBeaconServer = blockchain.createBeaconNode(vnode)
        beacon.appendClassName("Ethereum-POS-Beacon")
        beacon.appendClassName(f"Ethereum-POS-Beacon-{i:03d}")
        beacon.setDisplayName(f"Ethereum-POS-Beacon-{i:03d}")
        beacon.addHostName(f"{vnode}{DOMAIN}")
        beacon.connectToGethNode(f"gethnode{i % len(geth_nodes):03d}")
        beacon.setBeaconPeerCounts(beacon_peers)
        if i < beacon_bootnodes:
            beacon.setBootNode(True)
        beacon_nodes.append(beacon)
        _bind(emu, vnode, placements[vnode])

    for i in range(validator_count):
        vnode = f"vcnode{i:03d}"
        validator: PoSVcServer = blockchain.createVcNode(vnode)
        validator.appendClassName("Ethereum-POS-Validator")
        validator.appendClassName(f"Ethereum-POS-Validator-{i:03d}")
        validator.setDisplayName(f"Ethereum-POS-Validator-{i:03d}")
        validator.addHostName(f"{vnode}{DOMAIN}")
        validator.connectToBeaconNode(f"beaconnode{i // 3:03d}")
        validator.enablePOSValidatorAtGenesis()
        _bind(emu, vnode, placements[vnode])

    if include_tools:
        linked_geth = "gethnode000"
        faucet: FaucetServer = blockchain.createFaucetServer(
            vnode="faucet",
            port=80,
            linked_eth_node=linked_geth,
            balance=10_000,
            max_fund_amount=10,
        )
        # Reuse the Ethereum POS base image so tool nodes do not rebuild
        # python3/python3-pip with apt in every generated output.
        faucet.setBaseSystem(ETHEREUM_POS)
        faucet.setDisplayName("Faucet")
        faucet.addHostName(f"faucet{DOMAIN}")
        _bind(emu, "faucet", placements["faucet"])

        util_server: EthUtilityServer = blockchain.createEthUtilityServer(
            vnode="utility",
            port=5000,
            linked_eth_node=linked_geth,
            linked_faucet_node="faucet",
        )
        util_server.setBaseSystem(ETHEREUM_POS)
        util_server.setDisplayName("UtilityServer")
        util_server.addHostName(f"utility{DOMAIN}")
        _bind(emu, "utility", placements["utility"])

    emu.addLayer(eth)
    emu.addLayer(EtcHosts())


def _dedupe_etherview_networks(output: str) -> None:
    compose_path = Path(output) / "docker-compose.yml"
    if not compose_path.exists():
        return

    target_services = {
        "seedemu-ethexplorer-backend",
        "seedemu-ethexplorer-web",
    }
    lines = compose_path.read_text().splitlines(keepends=True)
    result: List[str] = []
    current_service: Optional[str] = None
    in_target_networks = False
    seen_networks: set[str] = set()

    for line in lines:
        stripped = line.strip()

        if len(line) - len(line.lstrip()) == 4 and stripped.endswith(":"):
            current_service = stripped[:-1]
            in_target_networks = False
            seen_networks = set()
        elif current_service in target_services and line.startswith("        ") and not line.startswith("            "):
            in_target_networks = stripped == "networks:"
            seen_networks = set()
        elif in_target_networks and line.startswith("            - "):
            network = stripped[2:].strip()
            if network in seen_networks:
                continue
            seen_networks.add(network)
        elif in_target_networks and stripped and not line.startswith("            "):
            in_target_networks = False

        result.append(line)

    compose_path.write_text("".join(result))


def _compose_service_blocks(compose_path: Path) -> List[List[str]]:
    blocks: List[List[str]] = []
    current_block: List[str] = []

    for line in compose_path.read_text().splitlines():
        stripped = line.strip()
        if len(line) - len(line.lstrip()) == 4 and stripped.endswith(":"):
            if current_block:
                blocks.append(current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)
    if current_block:
        blocks.append(current_block)
    return blocks


def _etherview_host(compose_path: Path, env_name: str) -> Optional[str]:
    import yaml

    compose = yaml.safe_load(compose_path.read_text())
    environment = compose.get("services", {}).get(
        "seedemu-ethexplorer-backend", {}
    ).get("environment", [])
    if isinstance(environment, dict):
        return environment.get(env_name)
    for entry in environment:
        if entry.startswith(f"{env_name}="):
            return entry.split("=", 1)[1]
    return None


def _etherview_el_host(compose_path: Path) -> Optional[str]:
    return _etherview_host(compose_path, "EL_HOST")


def _etherview_cl_host(compose_path: Path) -> Optional[str]:
    return _etherview_host(compose_path, "CL_HOST")


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


def _service_build_dir_for_ip(compose_path: Path, ip: str) -> Optional[Path]:
    blocks = _compose_service_blocks(compose_path)
    for block in blocks:
        block_text = "\n".join(block)
        if f"ipv4_address: {ip}" not in block_text:
            continue
        if "Ethereum-POS-Geth" not in block_text:
            continue
        return _build_dir_from_service_block(compose_path, block)
    return None


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


def _set_beacon_historic_state_flag(script_path: Path, enabled: bool) -> bool:
    flag = "--reconstruct-historic-states"
    original = script_path.read_text()

    if enabled:
        if flag in original:
            return False
        patched = original.replace(" --staking ", f" --staking {flag} ", 1)
        if patched == original:
            patched = original.replace(" --enr-udp-port ", f" {flag} --enr-udp-port ", 1)
    else:
        patched = re.sub(r"\s+--reconstruct-historic-states(?=\s)", " ", original)

    if patched == original:
        return False

    script_path.write_text(patched)
    return True


def _is_beacon_bootnode_script(script_text: str) -> bool:
    return "--boot-nodes" not in script_text


def _set_beacon_target_peers(script_path: Path, target_peers: int) -> bool:
    original = script_path.read_text()
    patched = re.sub(
        r"--target-peers\s+\S+",
        f"--target-peers {target_peers}",
        original,
        count=1,
    )
    if patched == original:
        return False
    script_path.write_text(patched)
    return True


def _patch_beacon_peer_targets(
    output: str,
    normal_peers: int,
    bootnode_peers: Optional[int],
) -> None:
    if bootnode_peers is None:
        return

    compose_path = Path(output) / "docker-compose.yml"
    if not compose_path.exists():
        return

    patched = 0
    bootnodes = 0
    normal = 0
    missing = 0

    for _ip, service_dir in _beacon_service_build_dirs(compose_path):
        script_path = _beacon_bootstrapper_path(service_dir)
        if script_path is None or not script_path.exists():
            missing += 1
            continue

        original = script_path.read_text()
        is_bootnode = _is_beacon_bootnode_script(original)
        target = bootnode_peers if is_bootnode else normal_peers
        if is_bootnode:
            bootnodes += 1
        else:
            normal += 1

        if _set_beacon_target_peers(script_path, target):
            patched += 1

    print(
        "Beacon peer targets: "
        f"normal={normal}x{normal_peers}, bootnodes={bootnodes}x{bootnode_peers}, "
        f"patched={patched}, missing_bootstrappers={missing}"
    )


def _remove_managed_lighthouse_cache_args(script_text: str) -> str:
    patched = script_text
    for flag in LIGHTHOUSE_MANAGED_CACHE_FLAGS:
        patched = re.sub(rf"\s+{re.escape(flag)}\s+\S+", " ", patched)
    return patched


def _set_lighthouse_cache_profile(script_path: Path, profile: str) -> bool:
    original = script_path.read_text()
    patched = _remove_managed_lighthouse_cache_args(original)

    if profile == "low":
        insertion = f" {LIGHTHOUSE_LOW_CACHE_ARGS} "
        if " --execution-endpoint " in patched:
            patched = patched.replace(" --execution-endpoint ", f"{insertion}--execution-endpoint ", 1)
        else:
            patched = patched.replace(" &", f"{insertion}&", 1)

    patched = re.sub(r"[ \t]+", " ", patched)
    if patched == original:
        return False
    script_path.write_text(patched)
    return True


def _patch_lighthouse_cache_profile(output: str, profile: str) -> None:
    if profile == "default":
        return

    compose_path = Path(output) / "docker-compose.yml"
    if not compose_path.exists():
        return

    patched = 0
    total = 0
    missing = 0

    for _ip, service_dir in _beacon_service_build_dirs(compose_path):
        total += 1
        script_path = _beacon_bootstrapper_path(service_dir)
        if script_path is None or not script_path.exists():
            missing += 1
            continue
        if _set_lighthouse_cache_profile(script_path, profile):
            patched += 1

    print(
        "Lighthouse cache profile: "
        f"profile={profile}, patched={patched}/{total}, missing_bootstrappers={missing}"
    )


def _patch_beacon_historic_states(output: str, mode: str) -> None:
    compose_path = Path(output) / "docker-compose.yml"
    if not compose_path.exists():
        return

    cl_host = _etherview_cl_host(compose_path) if mode == "etherview" else None
    if mode == "etherview" and cl_host is None:
        print("warning: --beacon-historic-states etherview requested, but CL_HOST was not found")

    patched = 0
    enabled = 0
    total = 0
    missing = 0

    for ip, service_dir in _beacon_service_build_dirs(compose_path):
        total += 1
        script_path = _beacon_bootstrapper_path(service_dir)
        if script_path is None or not script_path.exists():
            missing += 1
            continue

        keep_enabled = mode == "all" or (mode == "etherview" and ip == cl_host)
        if keep_enabled:
            enabled += 1
        if _set_beacon_historic_state_flag(script_path, keep_enabled):
            patched += 1

    print(
        "Beacon historic states: "
        f"mode={mode}, enabled={enabled}/{total}, patched={patched}, missing_bootstrappers={missing}"
    )


def _set_lighthouse_metrics(script_path: Path, ip: str, port: int) -> bool:
    original = script_path.read_text()
    if " --metrics " in original:
        return False

    metrics_args = (
        f' --http-enable-beacon-processor true '
        f'--metrics --metrics-address {ip} --metrics-port {port} '
        '--metrics-allow-origin "*" '
    )
    if " --execution-endpoint " in original:
        patched = original.replace(
            " --execution-endpoint ",
            f"{metrics_args}--execution-endpoint ",
            1,
        )
    else:
        patched = original.replace(" &", f"{metrics_args}&", 1)

    if patched == original:
        return False
    script_path.write_text(patched)
    return True


def _patch_lighthouse_metrics(output: str, enabled: bool, port: int) -> None:
    if not enabled:
        return

    compose_path = Path(output) / "docker-compose.yml"
    if not compose_path.exists():
        return

    patched = 0
    total = 0
    missing = 0
    for ip, service_dir in _beacon_service_build_dirs(compose_path):
        total += 1
        script_path = _beacon_bootstrapper_path(service_dir)
        if ip is None or script_path is None or not script_path.exists():
            missing += 1
            continue
        if _set_lighthouse_metrics(script_path, ip, port):
            patched += 1

    print(
        "Lighthouse metrics: "
        f"enabled={total - missing}/{total}, port={port}, "
        f"patched={patched}, missing_bootstrappers={missing}"
    )


def _patch_geth_script_for_etherview(script_path: Path) -> bool:
    original = script_path.read_text()
    patched_lines: List[str] = []

    for line in original.splitlines(keepends=True):
        patched = line
        if "geth --datadir " in patched and " init /tmp/eth-genesis.json" in patched:
            if "--state.scheme=" not in patched:
                patched = patched.replace(
                    " init /tmp/eth-genesis.json",
                    " --state.scheme=hash init /tmp/eth-genesis.json",
                    1,
                )
        elif patched.startswith("geth --datadir ") and "--authrpc.jwtsecret /tmp/jwt.hex" in patched:
            if "--state.scheme=" not in patched:
                patched = patched.replace(" --networkid=", " --state.scheme=hash --networkid=", 1)
            if "--gcmode=" not in patched:
                patched = patched.replace(
                    " --state.scheme=hash ",
                    " --state.scheme=hash --gcmode=archive ",
                    1,
                )
        patched_lines.append(patched)

    patched_text = "".join(patched_lines)
    if patched_text == original:
        return False

    script_path.write_text(patched_text)
    return True


def _patch_etherview_geth_archive_mode(output: str) -> None:
    compose_path = Path(output) / "docker-compose.yml"
    if not compose_path.exists():
        return

    el_host = _etherview_el_host(compose_path)
    if el_host is None:
        print("warning: could not find EtherView EL_HOST; Geth archive patch skipped")
        return

    service_dir = _service_build_dir_for_ip(compose_path, el_host)
    if service_dir is None:
        print(f"warning: could not find Geth service for EtherView EL_HOST={el_host}")
        return

    script_path = _start_script_path(service_dir)
    if script_path is None or not script_path.exists():
        print(f"warning: could not find start.sh source for {service_dir.name}")
        return

    changed = _patch_geth_script_for_etherview(script_path)
    status = "patched" if changed else "already configured"
    print(
        "EtherView Geth archive mode: "
        f"{status} {service_dir.name} ({el_host}) with state.scheme=hash and gcmode=archive"
    )


def run(
    dumpfile: Optional[str] = None,
    output: str = "./output",
    platform: Platform = Platform.AMD64,
    beacon_count: int = DEFAULT_BEACON_COUNT,
    geth_count: Optional[int] = None,
    validator_count: Optional[int] = None,
    validator_keys_per_client: int = DEFAULT_VALIDATOR_KEYS_PER_CLIENT,
    local_account_count: int = DEFAULT_LOCAL_ACCOUNT_COUNT,
    stub_as_count: int = DEFAULT_STUB_AS_COUNT,
    beacon_peers: int = DEFAULT_BEACON_PEERS,
    beacon_bootnode_peers: Optional[int] = None,
    beacon_bootnodes: int = DEFAULT_BEACON_BOOTNODES,
    geth_bootnodes: int = DEFAULT_GETH_BOOTNODES,
    include_tools: bool = True,
    internet_map: bool = True,
    ether_view: bool = False,
    beacon_historic_states: str = DEFAULT_BEACON_HISTORIC_STATES,
    lighthouse_cache_profile: str = DEFAULT_LIGHTHOUSE_CACHE_PROFILE,
    lighthouse_metrics: bool = False,
    lighthouse_metrics_port: int = DEFAULT_LIGHTHOUSE_METRICS_PORT,
) -> Emulator:
    geth_count = _derive_geth_count(beacon_count, geth_count)
    validator_count = _derive_validator_count(beacon_count, validator_count)

    _validate_counts(
        beacon_count=beacon_count,
        geth_count=geth_count,
        validator_count=validator_count,
        stub_as_count=stub_as_count,
        beacon_bootnodes=beacon_bootnodes,
        geth_bootnodes=geth_bootnodes,
    )
    assert beacon_peers >= 1, "--beacon-peers must be >= 1"
    assert validator_keys_per_client >= 1, "--validator-keys-per-client must be >= 1"
    assert local_account_count >= 2, "--local-account-count must be >= 2"
    assert 1 <= lighthouse_metrics_port <= 65535, (
        "--lighthouse-metrics-port must be between 1 and 65535"
    )
    if beacon_bootnode_peers is not None:
        assert beacon_bootnode_peers >= 1, "--beacon-bootnode-peers must be >= 1"

    stub_asns = _stub_asns(stub_as_count)
    planner = _build_service_placements(
        stub_asns=stub_asns,
        beacon_count=beacon_count,
        geth_count=geth_count,
        validator_count=validator_count,
        include_tools=include_tools,
    )
    host_counts = planner.host_counts()
    max_hosts = max(host_counts.values())
    assert max_hosts <= MAX_HOSTS_PER_STUB_AS, (
        f"largest stub AS needs {max_hosts} hosts, but only {MAX_HOSTS_PER_STUB_AS} "
        "are available in one /24 net0. Increase --stub-as-count."
    )

    emu = build_large_topology(stub_asns, host_counts)
    add_ethereum_pos_layer(
        emu=emu,
        placements=planner.placements,
        beacon_count=beacon_count,
        geth_count=geth_count,
        validator_count=validator_count,
        beacon_peers=beacon_peers,
        beacon_bootnodes=beacon_bootnodes,
        geth_bootnodes=geth_bootnodes,
        include_tools=include_tools,
        local_account_count=local_account_count,
    )

    total_services = 1 + beacon_count + geth_count + validator_count + (2 if include_tools else 0)
    print(
        "large PoS topology: "
        f"{beacon_count} beacon, {geth_count} geth, {validator_count} validator clients, "
        f"{validator_count * validator_keys_per_client} active validators "
        f"({validator_keys_per_client} keys/client), "
        f"{len(stub_asns)} stub AS, {sum(host_counts.values())} service hosts, "
        f"{total_services} Ethereum service containers"
    )

    if dumpfile is not None:
        emu.dump(dumpfile)
        return emu

    emu.render()
    docker = ChainSeedDocker(
        internetMapEnabled=internet_map,
        platform=platform,
    )
    if ether_view:
        docker.enableEthExplorer(chain_name="pos-large")
    emu.compile(docker, output, override=True)
    _patch_validator_key_distribution(
        output,
        validator_clients=validator_count,
        keys_per_client=validator_keys_per_client,
    )
    _patch_beacon_historic_states(output, beacon_historic_states)
    _patch_beacon_peer_targets(output, beacon_peers, beacon_bootnode_peers)
    _patch_lighthouse_cache_profile(output, lighthouse_cache_profile)
    _patch_lighthouse_metrics(output, lighthouse_metrics, lighthouse_metrics_port)
    if ether_view:
        _dedupe_etherview_networks(output)
        _patch_etherview_geth_archive_mode(output)
    return emu


def _platform_from_name(name: str) -> Platform:
    return Platform.ARM64 if name == "arm" else Platform.AMD64


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a large Ethereum PoS topology for beacon-node scaling tests."
    )
    parser.add_argument(
        "legacy_platform",
        nargs="?",
        choices=["amd", "arm"],
        help="Optional compatibility positional platform argument.",
    )
    parser.add_argument("--platform", choices=["amd", "arm"], default=None)
    parser.add_argument("--beacons", type=int, default=DEFAULT_BEACON_COUNT)
    parser.add_argument(
        "--geth",
        type=int,
        default=None,
        help="Compatibility option. If set, it must equal --beacons.",
    )
    parser.add_argument(
        "--validators",
        type=int,
        default=None,
        help="Compatibility option. If set, it must equal 3 * --beacons.",
    )
    parser.add_argument(
        "--validator-keys-per-client",
        type=int,
        default=DEFAULT_VALIDATOR_KEYS_PER_CLIENT,
        help=(
            "Number of genesis validator keys loaded by each Validator client. "
            "This increases active validator pressure without adding containers."
        ),
    )
    parser.add_argument(
        "--local-account-count",
        type=int,
        default=DEFAULT_LOCAL_ACCOUNT_COUNT,
        help="Number of prefunded HD accounts available to transaction generators.",
    )
    parser.add_argument("--stub-as-count", type=int, default=DEFAULT_STUB_AS_COUNT)
    parser.add_argument("--beacon-peers", type=int, default=DEFAULT_BEACON_PEERS)
    parser.add_argument(
        "--beacon-bootnode-peers",
        type=int,
        default=None,
        help=(
            "Override Lighthouse --target-peers for beacon bootnodes only. "
            "Normal beacon nodes keep --beacon-peers."
        ),
    )
    parser.add_argument("--beacon-bootnodes", type=int, default=DEFAULT_BEACON_BOOTNODES)
    parser.add_argument("--geth-bootnodes", type=int, default=DEFAULT_GETH_BOOTNODES)
    parser.add_argument("--output", default="./output")
    parser.add_argument("--dumpfile", default=None)
    parser.add_argument("--no-tools", action="store_true", help="Do not add faucet/utility containers.")
    parser.add_argument("--no-internet-map", action="store_true")
    parser.add_argument("--ether-view", action="store_true", help="Enable EtherView containers.")
    parser.add_argument(
        "--beacon-historic-states",
        choices=["all", "none", "etherview"],
        default=DEFAULT_BEACON_HISTORIC_STATES,
        help=(
            "Control Lighthouse --reconstruct-historic-states in generated "
            "beacon bootstrap scripts. Default: all, preserving current behavior."
        ),
    )
    parser.add_argument(
        "--lighthouse-cache-profile",
        choices=["default", "low"],
        default=DEFAULT_LIGHTHOUSE_CACHE_PROFILE,
        help=(
            "Patch generated beacon bootstrap scripts with a D61 cache profile. "
            "low adds: "
            f"{LIGHTHOUSE_LOW_CACHE_ARGS}"
        ),
    )
    parser.add_argument(
        "--lighthouse-metrics",
        action="store_true",
        help="Expose Lighthouse Prometheus metrics on every Beacon node.",
    )
    parser.add_argument(
        "--lighthouse-metrics-port",
        type=int,
        default=DEFAULT_LIGHTHOUSE_METRICS_PORT,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> None:
    args = _parse_args(argv)
    platform_name = args.platform or args.legacy_platform or "amd"
    run(
        dumpfile=args.dumpfile,
        output=args.output,
        platform=_platform_from_name(platform_name),
        beacon_count=args.beacons,
        geth_count=args.geth,
        validator_count=args.validators,
        validator_keys_per_client=args.validator_keys_per_client,
        local_account_count=args.local_account_count,
        stub_as_count=args.stub_as_count,
        beacon_peers=args.beacon_peers,
        beacon_bootnode_peers=args.beacon_bootnode_peers,
        beacon_bootnodes=args.beacon_bootnodes,
        geth_bootnodes=args.geth_bootnodes,
        include_tools=not args.no_tools,
        internet_map=not args.no_internet_map,
        ether_view=args.ether_view,
        beacon_historic_states=args.beacon_historic_states,
        lighthouse_cache_profile=args.lighthouse_cache_profile,
        lighthouse_metrics=args.lighthouse_metrics,
        lighthouse_metrics_port=args.lighthouse_metrics_port,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
