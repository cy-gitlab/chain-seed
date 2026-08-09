"""Runtime system profiles owned by ChainSeed."""

from seedemu.core import SystemProfile


ETHEREUM_LEGACY = SystemProfile("chainseed.ethereum.legacy")
ETHEREUM_POS = SystemProfile("chainseed.ethereum.pos")
MONERO = SystemProfile("chainseed.monero")
SOLANA = SystemProfile("chainseed.solana")
CHAINLINK = SystemProfile("chainseed.chainlink")

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
