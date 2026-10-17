#!/usr/bin/env python3
"""Discover Ethereum PoS API endpoints from generated Compose metadata."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def _labels(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("labels", {})
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    labels: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            key, separator, value = str(item).partition("=")
            if separator:
                labels[key] = value
    return labels


def _display_name(service: dict[str, Any]) -> str:
    labels = _labels(service)
    return labels.get("org.seedsecuritylabs.seedemu.meta.displayname", "")


def _service_ip(service: dict[str, Any]) -> str:
    networks = service.get("networks", {})
    if isinstance(networks, dict):
        for config in networks.values():
            if isinstance(config, dict) and config.get("ipv4_address"):
                return str(config["ipv4_address"])
    raise ValueError("service has no static IPv4 address")


def _role_index(display_name: str, role_prefix: str) -> int:
    match = re.fullmatch(rf"{re.escape(role_prefix)}-(\d+)", display_name)
    return int(match.group(1)) if match else 2**31 - 1


def discover_role_ips(compose_file: Path, role_prefix: str) -> list[str]:
    if not compose_file.exists():
        raise FileNotFoundError(f"Compose file does not exist: {compose_file}")
    payload = yaml.safe_load(compose_file.read_text()) or {}
    services = payload.get("services", {})
    if not isinstance(services, dict):
        raise ValueError(f"Compose file has no services mapping: {compose_file}")

    candidates: list[tuple[int, str, str]] = []
    for service_name, raw_service in services.items():
        if not isinstance(raw_service, dict):
            continue
        display_name = _display_name(raw_service)
        if not re.fullmatch(rf"{re.escape(role_prefix)}-\d+", display_name):
            continue
        candidates.append(
            (_role_index(display_name, role_prefix), str(service_name), _service_ip(raw_service))
        )

    if not candidates:
        raise RuntimeError(f"could not find {role_prefix}-NNN in {compose_file}")
    candidates.sort()
    return [candidate[2] for candidate in candidates]


def discover_role_ip(compose_file: Path, role_prefix: str) -> str:
    return discover_role_ips(compose_file, role_prefix)[0]


def discover_execution_rpc_url(compose_file: Path) -> str:
    return f"http://{discover_role_ip(compose_file, 'Ethereum-POS-Geth')}:8545"


def discover_execution_rpc_urls(compose_file: Path, limit: int = 0) -> list[str]:
    ips = discover_role_ips(compose_file, "Ethereum-POS-Geth")
    if limit > 0:
        ips = ips[:limit]
    return [f"http://{ip}:8545" for ip in ips]


def discover_beacon_api_url(compose_file: Path) -> str:
    return f"http://{discover_role_ip(compose_file, 'Ethereum-POS-Beacon')}:8000"
