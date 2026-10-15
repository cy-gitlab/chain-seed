"""Runtime system profiles owned by ChainSeed."""

from seedemu.core import BaseSystem, SystemProfile


ETHEREUM_LEGACY = SystemProfile(
    "chainseed.ethereum.legacy", subset=BaseSystem.SEEDEMU_BASE
)
ETHEREUM_POS = SystemProfile(
    "chainseed.ethereum.pos", subset=BaseSystem.SEEDEMU_BASE
)
MONERO = SystemProfile("chainseed.monero", subset=BaseSystem.SEEDEMU_BASE)
SOLANA = SystemProfile("chainseed.solana", subset=BaseSystem.SEEDEMU_BASE)
CHAINLINK = SystemProfile("chainseed.chainlink")
OP_STACK = SystemProfile("chainseed.ethereum.op-stack", subset=BaseSystem.SEEDEMU_BASE)
SC_DEPLOYER = SystemProfile("chainseed.ethereum.sc-deployer", subset=BaseSystem.SEEDEMU_BASE)

CHAINSEED_SYSTEMS = (
    ETHEREUM_LEGACY,
    ETHEREUM_POS,
    MONERO,
    SOLANA,
    CHAINLINK,
    OP_STACK,
    SC_DEPLOYER,
)


class ChainSeedSystem:
    """Namespace for ChainSeed system profiles."""

    ETHEREUM_LEGACY = ETHEREUM_LEGACY
    ETHEREUM_POS = ETHEREUM_POS
    MONERO = MONERO
    SOLANA = SOLANA
    CHAINLINK = CHAINLINK
    OP_STACK = OP_STACK
    SC_DEPLOYER = SC_DEPLOYER


__all__ = [
    "CHAINLINK",
    "CHAINSEED_SYSTEMS",
    "ChainSeedSystem",
    "ETHEREUM_LEGACY",
    "ETHEREUM_POS",
    "MONERO",
    "OP_STACK",
    "SC_DEPLOYER",
    "SOLANA",
]
