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
CHAINLINK = SystemProfile(
    "chainseed.chainlink", subset=BaseSystem.UBUNTU_20_04
)

CHAINSEED_SYSTEMS = (
    ETHEREUM_LEGACY,
    ETHEREUM_POS,
    MONERO,
    SOLANA,
    CHAINLINK,
)


class ChainSeedSystem:
    """Namespace for ChainSeed system profiles."""

    ETHEREUM_LEGACY = ETHEREUM_LEGACY
    ETHEREUM_POS = ETHEREUM_POS
    MONERO = MONERO
    SOLANA = SOLANA
    CHAINLINK = CHAINLINK


__all__ = [
    "CHAINLINK",
    "CHAINSEED_SYSTEMS",
    "ChainSeedSystem",
    "ETHEREUM_LEGACY",
    "ETHEREUM_POS",
    "MONERO",
    "SOLANA",
]
