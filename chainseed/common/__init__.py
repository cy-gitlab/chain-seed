"""Shared contracts used by more than one blockchain integration."""

from .systems import (
    CHAINLINK,
    CHAINSEED_SYSTEMS,
    ETHEREUM_LEGACY,
    ETHEREUM_POS,
    MONERO,
    SOLANA,
    ChainSeedSystem,
)
from .labels import (
    CHAINSEED_LABEL_PREFIX,
    SEED_META_LABEL_PREFIX,
    ethereum_label,
)

__all__ = [
    "CHAINLINK",
    "CHAINSEED_LABEL_PREFIX",
    "CHAINSEED_SYSTEMS",
    "ChainSeedSystem",
    "ETHEREUM_LEGACY",
    "ETHEREUM_POS",
    "MONERO",
    "SOLANA",
    "SEED_META_LABEL_PREFIX",
    "ethereum_label",
]

