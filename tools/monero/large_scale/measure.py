#!/usr/bin/env python3
"""Collect resources and chain observations from an existing Monero deployment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.blockchain.lib.processes import Processes, interrupted

import yaml
from _health import read_nodes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--miner-state', type=Path)
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--duration', type=int, default=0, help='Seconds; 0 until Ctrl-C')
    args = parser.parse_args()
    if args.interval <= 0 or args.duration < 0:
        parser.error('interval must be positive; duration must be nonnegative')
    deployment, directory = args.deployment.resolve(), args.run_dir.resolve()
    nodes = read_nodes(deployment)
    for name in ('measurement.json', 'memory_raw.csv', 'container-identity.json', 'final_health.jsonl', 'host.jsonl'):
        if (directory/name).exists():
            parser.error(f'existing {name}; choose a new run directory')
    if args.miner_state and args.miner_state.resolve().parent != directory:
        parser.error('keep miner state and its rotation journal in --run-dir')
    directory.mkdir(parents=True, exist_ok=True)
    compose = deployment/'docker-compose.yml'
    (directory/'observed-compose.yml').write_bytes(compose.read_bytes())
    (directory/'nodes.json').write_text(json.dumps(nodes, indent=2)+'\n')
    (directory/'deployment.json').write_bytes((deployment/'deployment.json').read_bytes())
    images = sorted({s['image'] for s in yaml.safe_load(compose.read_text())['services'].values()})
    common = Path(__file__).resolve().parents[2]/'blockchain/lib'
    sources = list(Path(__file__).parent.rglob('*.py')) + list(common.glob('*.py'))
    config = dict(status='running', started_at=time.time(), interval=args.interval,
        duration=args.duration, nodes=len(nodes), miner_state=str(args.miner_state) if args.miner_state else None,
        compose_sha256=hashlib.sha256(compose.read_bytes()).hexdigest(),
        source_sha256={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        images=json.loads(subprocess.check_output(['docker', 'image', 'inspect', *images], text=True, timeout=60)))
    metadata = directory/'measurement.json'
    with metadata.open('x') as handle:
        json.dump(config, handle, indent=2)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    group = Processes(directory/'logs')
    deadline = time.monotonic()+args.duration if args.duration else float('inf')
    try:
        group.start('resources', [sys.executable, common/'collect_project_resources.py',
            '--compose-file', compose, '--output-dir', directory, '--interval', args.interval,
            '--scope', 'compose', '--backend', 'cgroup-scan-enriched'])
        health = [sys.executable, Path(__file__).with_name('_health.py'), '--deployment', deployment,
            '--output', directory/'final_health.jsonl', '--interval', args.interval]
        if args.miner_state:
            health += ['--miner-state', args.miner_state.resolve()]
        group.start('health', health)
        next_host = 0
        with (directory/'host.jsonl').open('x') as handle:
            while time.monotonic() < deadline:
                group.require_alive()
                if time.monotonic() >= next_host:
                    handle.write(json.dumps(dict(time=time.time(), cpu_count=os.cpu_count(),
                        clock_ticks=os.sysconf('SC_CLK_TCK'), meminfo=Path('/proc/meminfo').read_text(),
                        cpu_stat=Path('/proc/stat').read_text().splitlines()[0],
                        loadavg=Path('/proc/loadavg').read_text()))+'\n')
                    handle.flush()
                    next_host = time.monotonic()+args.interval
                time.sleep(0.5)
        config['status'] = 'complete'
    except KeyboardInterrupt:
        config['status'] = 'complete'
    except BaseException:
        config['status'] = 'failed'
        raise
    finally:
        group.stop()
        config['stopped_at'] = time.time()
        metadata.write_text(json.dumps(config, indent=2)+'\n')


if __name__ == '__main__':
    main()
