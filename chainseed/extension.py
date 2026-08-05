"""Versioned metadata describing the ChainSeed extension boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChainSeedExtension:
    distribution: str
    version: str
    extension_api_version: int
    seedemu_min_version: str
    owned_layer_names: tuple[str, ...]
    artifact_schema_versions: tuple[str, ...]


descriptor = ChainSeedExtension(
    distribution="chain-seed",
    version="0.1.0a0",
    extension_api_version=1,
    seedemu_min_version="0.0.7",
    owned_layer_names=(
        "EthereumService",
        "MoneroService",
        "SolanaService",
        "ChainlinkService",
    ),
    artifact_schema_versions=("chainseed.labels/v1",),
)

