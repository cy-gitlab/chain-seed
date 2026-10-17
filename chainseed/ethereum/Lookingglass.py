"""Optional Docker deployment component for the Ethereum Lookingglass."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from urllib.parse import urlparse

import yaml
from seedemu.core import Emulator
from seedemu.core.enums import NetworkType

from ..common.labels import CHAINSEED_LABEL_PREFIX, SEED_META_LABEL_PREFIX
from .EthereumServer import PoSBeaconServer, PoSGethServer, PoSVcServer
from .EthereumService import EthereumService

LOOKINGGLASS_SERVICE = "chainseed-lookingglass"
LOOKINGGLASS_CONTEXT = "lookingglass"


class Lookingglass:
    """Attach a read-only viewer to one rendered Ethereum PoS chain.

    Node queries work without measurement scripts. An optional run_dir adds
    existing resource, consensus and transaction observations via a read-only mount.
    """
    def __init__(self, chain_name: str, port: int = 8765, *,
                 probe_peers: bool = True, run_dir: str | Path | None = None,
                 explorer_url: str = "", peer_interval: float = 10,
                 peer_jobs: int = 8, metrics_interval: float = 10):
        if not isinstance(chain_name, str) or not chain_name.strip():
            raise ValueError("chain_name must be a non-empty string")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be an integer between 1 and 65535")
        if type(probe_peers) is not bool:
            raise ValueError("probe_peers must be a boolean")
        if type(peer_jobs) is not int or not 1 <= peer_jobs <= 32:
            raise ValueError("peer_jobs must be an integer between 1 and 32")
        if not 1 <= peer_interval <= 3600 or not 2 <= metrics_interval <= 3600:
            raise ValueError("peer_interval must be 1..3600; metrics_interval must be 2..3600")
        if explorer_url and urlparse(explorer_url).scheme not in ("http", "https"):
            raise ValueError("explorer_url must use http or https")
        self.chain_name, self.port = chain_name, port
        self.probe_peers, self.explorer_url = probe_peers, explorer_url
        # Resolve against the user's directory, before the compiler changes cwd.
        self.run_dir = Path(run_dir).expanduser().resolve() if run_dir is not None else None
        self.peer_interval, self.peer_jobs = peer_interval, peer_jobs
        self.metrics_interval = metrics_interval

    def _targets(self, emulator):
        if not isinstance(emulator, Emulator) or not emulator.rendered():
            raise RuntimeError("render the emulator before attaching Lookingglass")
        try:
            ethereum = emulator.getLayer("EthereumService")
        except AssertionError as exc:
            raise RuntimeError("Lookingglass requires EthereumService") from exc
        if not isinstance(ethereum, EthereumService):
            raise RuntimeError("Lookingglass requires EthereumService")
        roles = {PoSBeaconServer: "beacon", PoSGethServer: "geth", PoSVcServer: "validator"}
        targets = []
        for server, node in ethereum.getTargets():
            role = next((name for kind, name in roles.items() if isinstance(server, kind)), None)
            if not role or server.getBlockchain().getChainName() != self.chain_name:
                continue
            interfaces = [i for i in node.getInterfaces() if i.getNet().getType() == NetworkType.Local]
            if not interfaces or interfaces[0].getAddress() is None:
                raise RuntimeError("Lookingglass targets need an addressed local network interface")
            targets.append((server, node, role, interfaces[0]))
        if not {"geth", "beacon"}.issubset({t[2] for t in targets}):
            raise RuntimeError(f"Lookingglass chain {self.chain_name!r} needs PoS Geth and Beacon nodes")
        return targets

    def getComposeServices(self, emulator, network_name):
        targets = self._targets(emulator)
        command = ["python", "/app/server.py", "--inventory", "/app/inventory.json",
                   "--host", "0.0.0.0", "--port", "8765",
                   "--peer-interval", str(self.peer_interval), "--peer-jobs", str(self.peer_jobs),
                   "--metrics-interval", str(self.metrics_interval)]
        if self.probe_peers:
            command.append("--probe-peers")
        if self.explorer_url:
            command += ["--explorer-url", self.explorer_url]
        volumes = [{"type": "bind", "source": "./lookingglass/inventory.json",
                    "target": "/app/inventory.json", "read_only": True,
                    "bind": {"create_host_path": False}}]
        if self.run_dir is not None:
            command += ["--run-dir", "/observations"]
            volumes.append({"type": "bind", "source": str(self.run_dir),
                            "target": "/observations", "read_only": True,
                            "bind": {"create_host_path": False}})
        return {LOOKINGGLASS_SERVICE: {
            "build": {"context": "./" + LOOKINGGLASS_CONTEXT},
            "command": command, "restart": "unless-stopped",
            "ports": [f"127.0.0.1:{self.port}:8765/tcp"],
            "networks": {network_name(t[3].getNet()): {} for t in targets},
            "volumes": volumes,
            "labels": {CHAINSEED_LABEL_PREFIX + "component": "lookingglass"},
            "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "2"}},
        }}

    def writeOutputFiles(self, emulator, output_dir):
        """Export a movable build context and inventory after Compose is written."""
        output = Path(output_dir)
        context = output / LOOKINGGLASS_CONTEXT
        context.mkdir()
        runtime = Path(__file__).with_name("lookingglass")
        for name in ("server.py", "sources.py"):
            shutil.copyfile(runtime / name, context / name)
        shutil.copytree(runtime / "static", context / "static")
        (context / "Dockerfile").write_text(
            'FROM python:3.10-slim\nWORKDIR /app\n'
            'RUN pip install --no-cache-dir "PyYAML>=6.0,<7"\n'
            'COPY server.py sources.py /app/\nCOPY static /app/static/\n'
            'ENV PYTHONUNBUFFERED=1\nEXPOSE 8765\n'
        )
        compose = yaml.safe_load((output / "docker-compose.yml").read_text())
        services = {}
        for service, spec in compose["services"].items():
            labels = spec.get("labels", {})
            if isinstance(labels, list):
                labels = dict(item.split("=", 1) for item in labels if "=" in item)
            key = (str(labels.get(SEED_META_LABEL_PREFIX + "asn")), labels.get(SEED_META_LABEL_PREFIX + "nodename"))
            services[key] = (service, spec)
        nodes, dependencies = [], []
        for server, node, role, interface in self._targets(emulator):
            service, spec = services[(str(node.getAsn()), node.getName())]
            identity = spec.get("container_name", service)
            nodes.append({"id": identity, "service": service, "role": role,
                          "label": node.getDisplayName() or node.getName(),
                          "asn": str(node.getAsn()), "ip": str(interface.getAddress()),
                          "rpc_port": server.getGethHttpPort() if role == "geth" else 8000})
            target = (server.getConnectedGethIp() if role == "beacon" else
                      server.getConnectedBeaconIp() if role == "validator" else None)
            if target:
                dependencies.append((identity, str(target)))
        by_ip = {node["ip"]: node["id"] for node in nodes}
        edges = [{"source": source, "target": by_ip[ip], "kind": "service", "at": None}
                 for source, ip in dependencies if ip in by_ip]
        (context / "inventory.json").write_text(json.dumps(
            {"chain": self.chain_name, "nodes": nodes, "edges": edges}, indent=2) + "\n")
        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)


__all__ = ["Lookingglass"]
