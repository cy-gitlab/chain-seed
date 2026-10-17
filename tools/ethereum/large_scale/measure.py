#!/usr/bin/env python3
"""Collect observations only; does not generate, start or stop the deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import sys
import time
from _deployment import read_deployment
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.blockchain.lib.processes import Processes, interrupted

LIB = Path(__file__).with_name('_lib')

RESOURCE_COLLECTOR = Path(__file__).resolve().parents[2] / 'blockchain/lib/collect_node_memory.py'


def start(compose, data_dir):
    cfg = read_deployment(compose)
    data_dir = Path(data_dir)
    # Never append a second observation run to old raw records.
    for path in ('memory_raw.csv', 'block_metrics.csv', 'consensus', 'health/samples.jsonl'):
        if (data_dir / path).exists():
            raise ValueError(f'Existing collection: {data_dir / path}; choose a new run directory')
    group = Processes(data_dir / 'logs')
    (data_dir / 'deployment.json').write_text(json.dumps(cfg, indent=2) + '\n')
    compose_bytes = Path(compose).read_bytes()
    (data_dir / 'observed-compose.yml').write_bytes(compose_bytes)
    (data_dir / 'observed-compose.sha256').write_text(hashlib.sha256(compose_bytes).hexdigest() + '\n')
    commands = {
        'memory': [RESOURCE_COLLECTOR, '--compose-file', compose, '--output-dir', data_dir,
            '--interval', '60', '--backend', 'cgroup-scan-enriched', '--scope', 'compose',
            '--expect-beacons', cfg['beacons'], '--expect-geth', cfg['geth'], '--expect-validators', cfg['validator_clients']],
        'blocks': ['collect_block_metrics.py', '--compose-file', compose, '--output', data_dir / 'block_metrics.csv', '--interval', '2'],
        'consensus': ['collect_consensus_benchmark.py', '--compose-file', compose, '--output-dir', data_dir / 'consensus',
            '--interval', '12', '--beacon-sample', min(40, cfg['beacons']), '--expected-active-validators', cfg['active_validators'],
            '--validator-liveness-timeout', '60'],
        'health': ['health.py', '--compose', compose, '--data-dir', data_dir,
            '--expected-validators', cfg['active_validators']],
    }
    try:
        for name, command in commands.items():
            group.start(name, [sys.executable, LIB / command[0], *command[1:]])
        return group
    except BaseException:
        group.stop()
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compose', type=Path, required=True)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--duration', type=int, default=0, help='Collection seconds; 0 until Ctrl-C')
    a = p.parse_args()
    if a.duration < 0:
        p.error('duration must be nonnegative')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    group = start(a.compose.resolve(), a.run_dir.resolve())
    deadline = time.monotonic() + a.duration if a.duration else float('inf')
    try:
        while time.monotonic() < deadline:
            group.require_alive()
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        group.stop()


if __name__ == '__main__':
    main()
