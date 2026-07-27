#!/usr/bin/env python3
"""Collect Docker memory usage for D61 large-scale Solana experiments."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Dict


SCRIPT_DIR = Path(__file__).resolve().parent
SOLANA_DIR = SCRIPT_DIR.parent
D61_DIR = SOLANA_DIR.parent
ETH_COLLECTOR = D61_DIR / "ethereum" / "scripts" / "collect_node_memory.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("d61_collect_node_memory", ETH_COLLECTOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load collector: {ETH_COLLECTOR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_collector()
    original_classify = module.classify_container

    def classify_container(name: str, labels: Dict[str, str], cmdline: str = "") -> str:
        meta_class = labels.get("org.seedsecuritylabs.seedemu.meta.class", "")
        display_name = labels.get("org.seedsecuritylabs.seedemu.meta.displayname", "")
        role = labels.get("org.seedsecuritylabs.seedemu.meta.role", "")
        service = labels.get("com.docker.compose.service", "")
        combined = " ".join([name, service, meta_class, display_name, role, cmdline])

        if "Solana-Bootstrap" in combined:
            return "solana_bootstrap"
        if "Solana-Validator" in combined:
            return "solana_validator"
        if "agave-validator" in cmdline and "bootstrap-validator" in cmdline:
            return "solana_bootstrap"
        if "agave-validator" in cmdline and "validator" in cmdline:
            return "solana_validator"
        return original_classify(name, labels, cmdline)

    module.classify_container = classify_container
    module.DEFAULT_COMPOSE_FILE = SOLANA_DIR / "output" / "docker-compose.yml"
    module.DEFAULT_OUTPUT_DIR = SOLANA_DIR / "perf-data" / "manual"
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
