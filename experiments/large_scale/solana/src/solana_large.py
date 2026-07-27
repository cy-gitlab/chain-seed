#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import argparse
import platform as host_platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

SCRIPT_PATH = Path(__file__).resolve()
SOLANA_DIR = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[5]
if (REPO_ROOT / "seedemu").is_dir():
    sys.path.insert(0, str(REPO_ROOT))

from seedemu import *  # noqa: F403
from chainseed.solana import *
from chainseed.solana import SolanaService


DOMAIN = ".large-solana.net"

DEFAULT_VALIDATOR_COUNT = 300
DEFAULT_STUB_AS_COUNT = 64
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

ANCHOR_TRANSIT_ASN = 2
ANCHOR_TRANSIT_IX = 100

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


def _detect_platform() -> Platform:  # noqa: F405
    machine = host_platform.machine().lower()
    return Platform.ARM64 if machine in ("arm64", "aarch64") else Platform.AMD64  # noqa: F405


def _parse_platform(value: str) -> Platform:  # noqa: F405
    value = value.lower()
    if value == "amd":
        return Platform.AMD64  # noqa: F405
    if value == "arm":
        return Platform.ARM64  # noqa: F405
    raise argparse.ArgumentTypeError("platform must be amd or arm")


def _stub_asns(stub_as_count: int) -> List[int]:
    return list(range(STUB_AS_START, STUB_AS_START + stub_as_count))


def _validate_counts(validator_count: int, stub_as_count: int) -> None:
    assert validator_count >= 1, "--validators must be >= 1"
    assert 1 <= stub_as_count <= MAX_STUB_AS_COUNT, (
        f"--stub-as-count must be between 1 and {MAX_STUB_AS_COUNT}"
    )


def _build_service_placements(
    stub_asns: Sequence[int],
    validator_count: int,
) -> PlacementPlanner:
    planner = PlacementPlanner(stub_asns)
    stub_count = len(stub_asns)

    planner.place("sol-boot-000", stub_asns[0])
    for index in range(1, validator_count):
        planner.place(f"sol-validator-{index:03d}", stub_asns[index % stub_count])

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


def _create_transit_as(base: Base, asn: int, exchanges: Sequence[int]) -> None:  # noqa: F405
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
    base: Base,  # noqa: F405
    asn: int,
    host_count: int,
    primary_ix: int,
    secondary_ix: Optional[int],
    anchor_ix: int,
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
    if anchor_ix not in (primary_ix, secondary_ix):
        router.joinNetwork(f"ix{anchor_ix}")

    assert host_count <= MAX_HOSTS_PER_STUB_AS, (
        f"AS{asn} needs {host_count} hosts, but net0 only supports "
        f"{MAX_HOSTS_PER_STUB_AS}. Increase --stub-as-count."
    )
    for host_id in range(host_count):
        stub_as.createHost(f"host_{host_id:03d}").joinNetwork("net0")


def _safe_private_peering(
    ebgp: Ebgp,  # noqa: F405
    seen: set[Tuple[int, int, int]],
    ix: int,
    provider: int,
    customer: int,
    relationship: PeerRelationship = PeerRelationship.Provider,  # noqa: F405
) -> None:
    key = (ix, provider, customer)
    if key in seen:
        return
    ebgp.addPrivatePeering(ix, provider, customer, relationship)
    seen.add(key)


def _pick_provider(
    providers: Sequence[int],
    offset: int,
    used_provider_asns: set[int],
) -> Optional[int]:
    candidates = [provider for provider in providers if provider not in used_provider_asns]
    if not candidates:
        return None
    return candidates[offset % len(candidates)]


def build_large_topology(stub_asns: Sequence[int], host_counts: Dict[int, int]) -> Emulator:  # noqa: F405
    emu = Emulator()  # noqa: F405
    base = Base()  # noqa: F405
    routing = Routing()  # noqa: F405
    ebgp = Ebgp()  # noqa: F405
    ibgp = Ibgp()  # noqa: F405
    ospf = Ospf()  # noqa: F405

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
            anchor_ix=ANCHOR_TRANSIT_IX,
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
        used_provider_asns = {ANCHOR_TRANSIT_ASN}
        primary_pool = tier1_by_ix[primary_ix] if index % 7 == 0 else regional_by_ix[primary_ix]
        primary_provider = _pick_provider(primary_pool, index, used_provider_asns)
        if primary_provider is not None:
            _safe_private_peering(ebgp, seen_private_peerings, primary_ix, primary_provider, stub_asn)
            used_provider_asns.add(primary_provider)

        _safe_private_peering(
            ebgp,
            seen_private_peerings,
            ANCHOR_TRANSIT_IX,
            ANCHOR_TRANSIT_ASN,
            stub_asn,
        )

        if secondary_ix is not None and secondary_ix != primary_ix:
            secondary_provider = _pick_provider(
                tier1_by_ix[secondary_ix],
                index // 4,
                used_provider_asns,
            )
            if secondary_provider is not None:
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
            relationship=PeerRelationship.Peer,  # noqa: F405
        )

    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    return emu


def _bind(emu: Emulator, vnode: str, placement: Placement) -> None:  # noqa: F405
    emu.addBinding(
        Binding(  # noqa: F405
            vnode,
            filter=Filter(asn=placement.asn, nodeName=f"^{placement.host}$"),  # noqa: F405
            action=Action.FIRST,  # noqa: F405
        )
    )


def add_solana_layer(
    emu: Emulator,  # noqa: F405
    placements: Dict[str, Placement],
    validator_count: int,
    faucet_lamports: int,
    hashes_per_tick: str,
    node_sol: int,
) -> None:
    solana = SolanaService()
    blockchain = solana.createBlockchain("seed-solana-large")

    boot = blockchain.createBootstrapValidator("sol-boot-000")
    boot.setDisplayName("Solana-Bootstrap-000")
    boot.appendClassName("Solana-Bootstrap")
    boot.addHostName(f"sol-boot-000{DOMAIN}")
    boot.setFaucetLamports(faucet_lamports)
    boot.setHashesPerTick(hashes_per_tick)
    _bind(emu, "sol-boot-000", placements["sol-boot-000"])

    for index in range(1, validator_count):
        vnode = f"sol-validator-{index:03d}"
        validator = blockchain.createValidator(vnode)
        validator.setDisplayName(f"Solana-Validator-{index:03d}")
        validator.appendClassName("Solana-Validator")
        validator.appendClassName(f"Solana-Validator-{index:03d}")
        validator.addHostName(f"{vnode}{DOMAIN}")
        validator.setNodeSol(node_sol)
        _bind(emu, vnode, placements[vnode])

    emu.addLayer(solana)
    emu.addLayer(EtcHosts())  # noqa: F405


def run(
    validators: int,
    stub_as_count: int,
    output: str,
    platform: Platform,  # noqa: F405
    internet_map: bool,
    faucet_lamports: int,
    hashes_per_tick: str,
    node_sol: int,
) -> None:
    _validate_counts(validators, stub_as_count)
    stub_asns = _stub_asns(stub_as_count)
    planner = _build_service_placements(stub_asns, validators)
    host_counts = planner.host_counts()
    max_hosts = max(host_counts.values(), default=0)
    assert max_hosts <= MAX_HOSTS_PER_STUB_AS, (
        f"largest stub AS needs {max_hosts} hosts, but only {MAX_HOSTS_PER_STUB_AS} "
        "are available in one /24 net0. Increase --stub-as-count."
    )

    emu = build_large_topology(stub_asns, host_counts)
    add_solana_layer(
        emu=emu,
        placements=planner.placements,
        validator_count=validators,
        faucet_lamports=faucet_lamports,
        hashes_per_tick=hashes_per_tick,
        node_sol=node_sol,
    )

    emu.render()
    docker = Docker(internetMapEnabled=internet_map, platform=platform)  # noqa: F405
    emu.compile(docker, output, override=True)

    print(
        "generated large Solana topology: "
        f"validators={validators} bootstrap=1 non_bootstrap_validators={max(0, validators - 1)} "
        f"stub_as={len(stub_asns)} service_hosts={sum(host_counts.values())} "
        f"max_hosts_per_stub={max_hosts} output={output}"
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a large private Solana/Agave cluster on a D61-style "
            "multi-transit SEED topology."
        )
    )
    parser.add_argument("--validators", type=int, default=DEFAULT_VALIDATOR_COUNT)
    parser.add_argument("--stub-as-count", type=int, default=DEFAULT_STUB_AS_COUNT)
    parser.add_argument("--output", default=str(SOLANA_DIR / "output"))
    parser.add_argument("--platform", type=_parse_platform, default=_detect_platform())
    parser.add_argument("--no-internet-map", action="store_true")
    parser.add_argument("--faucet-lamports", type=int, default=500000000000000000)
    parser.add_argument("--hashes-per-tick", default="sleep")
    parser.add_argument("--node-sol", type=int, default=500)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    run(
        validators=args.validators,
        stub_as_count=args.stub_as_count,
        output=args.output,
        platform=args.platform,
        internet_map=not args.no_internet_map,
        faucet_lamports=args.faucet_lamports,
        hashes_per_tick=args.hashes_per_tick,
        node_sol=args.node_sol,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
