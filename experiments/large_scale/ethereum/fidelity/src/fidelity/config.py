from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REQUIRED_TABLES = (
    "libp2p_synthetic_heartbeat",
    "libp2p_connected",
    "libp2p_disconnected",
)
OPTIONAL_TABLES = (
    "libp2p_identify",
    "libp2p_graft",
    "libp2p_prune",
    "libp2p_gossipsub_beacon_block",
)


@dataclass(frozen=True)
class PipelineConfig:
    source: str = "xatu"
    network: str = "mainnet"
    date: str = "latest"
    lookback_days: int = 14
    snapshot_bucket_seconds: int = 60
    required_tables: tuple[str, ...] = REQUIRED_TABLES
    optional_tables: tuple[str, ...] = OPTIONAL_TABLES

    @property
    def tables(self) -> tuple[str, ...]:
        return self.required_tables + self.optional_tables

    @classmethod
    def from_file(cls, path: Path) -> "PipelineConfig":
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        unknown = set(raw) - {
            "source",
            "network",
            "date",
            "lookback_days",
            "snapshot_bucket_seconds",
            "required_tables",
            "optional_tables",
        }
        if unknown:
            raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}")
        config = cls(
            source=str(raw.get("source", "xatu")),
            network=str(raw.get("network", "mainnet")),
            date=str(raw.get("date", "latest")),
            lookback_days=int(raw.get("lookback_days", 14)),
            snapshot_bucket_seconds=int(raw.get("snapshot_bucket_seconds", 60)),
            required_tables=tuple(raw.get("required_tables", REQUIRED_TABLES)),
            optional_tables=tuple(raw.get("optional_tables", OPTIONAL_TABLES)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.source != "xatu":
            raise ValueError("the primary topology source must be xatu")
        if not self.network:
            raise ValueError("network must not be empty")
        if self.lookback_days < 1:
            raise ValueError("lookback_days must be at least 1")
        if self.snapshot_bucket_seconds < 1:
            raise ValueError("snapshot_bucket_seconds must be at least 1")
        missing = set(REQUIRED_TABLES) - set(self.required_tables)
        if missing:
            raise ValueError(
                "the initial reconstruction requires tables: "
                + ", ".join(sorted(missing))
            )
        overlap = set(self.required_tables) & set(self.optional_tables)
        if overlap:
            raise ValueError(f"tables cannot be both required and optional: {sorted(overlap)}")
