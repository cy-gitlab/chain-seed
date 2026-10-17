"""Bootstrap RPC observations and Compose-scoped Agave process evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.blockchain.lib.collect_node_memory import cgroup_path_for_pid, read_cgroup_cmdlines
from types import SimpleNamespace


def deployment_info(deployment):
    deployment = Path(deployment)
    config = json.loads((deployment/'deployment.json').read_text())
    compose = yaml.safe_load((deployment/'docker-compose.yml').read_text())
    nodes = []
    for name, service in compose['services'].items():
        display = service.get('labels', {}).get('org.seedsecuritylabs.seedemu.meta.displayname', '')
        match = re.fullmatch(r'Solana-(Bootstrap|Validator)-(\d+)', display)
        if match:
            ip = next(n['ipv4_address'] for n in service['networks'].values() if n.get('ipv4_address'))
            nodes.append(dict(index=int(match[2]), ip=ip, service=name, container=service['container_name']))
    nodes.sort(key=lambda n:n['index'])
    if [n['index'] for n in nodes] != list(range(config['validators'])):
        raise ValueError('Solana services do not match deployment.json')
    return config, nodes


def rpc(url, method, params=None, timeout=240):
    request = urllib.request.Request(url,
        data=json.dumps(dict(jsonrpc='2.0', id=1, method=method, params=params or [])).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
        body = json.load(response)
    if body.get('error'):
        raise RuntimeError(str(body['error']))
    return body['result']


class Processes:
    def __init__(self, nodes):
        names = [n['container'] for n in nodes]
        data = json.loads(subprocess.check_output(['docker','inspect',*names], text=True, timeout=60))
        self.containers = {}
        for record in data:
            name = record['Name'].lstrip('/')
            pid = record['State']['Pid']
            if name not in names or pid <= 0 or name in self.containers:
                raise ValueError('Missing, stopped or unexpected Solana container')
            self.containers[name] = SimpleNamespace(cgroup_path=cgroup_path_for_pid(pid), image_id=record['Image'])
        if set(self.containers) != set(names):
            raise ValueError('Incomplete container snapshot')

    def sample(self):
        def check(item):
            name, info = item
            lines = read_cgroup_cmdlines(info.cgroup_path).splitlines()
            count = sum(bool(re.search(r'(^|/)agave-validator(?:\s|$)', line)) for line in lines)
            return dict(container=name, image_id=info.image_id, active_agave=count, healthy=count == 1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(check, self.containers.items()))
        return rows


def observe(url, processes, timeout=240):
    finalized = rpc(url, 'getSlot', [{'commitment':'finalized'}], timeout)
    processed = rpc(url, 'getSlot', [{'commitment':'processed'}], timeout)
    votes = rpc(url, 'getVoteAccounts', [{'commitment':'finalized'}], timeout)
    current = sum(v['activatedStake'] for v in votes['current'])
    delinquent = sum(v['activatedStake'] for v in votes['delinquent'])
    total = current+delinquent
    process_rows = processes.sample()
    row = dict(finalized_slot=finalized, processed_slot=processed, finalized_lag=processed-finalized,
        votes=votes, current_validators=len(votes['current']), delinquent_validators=len(votes['delinquent']),
        current_stake_pct=100*current/total if total else None,
        active_agave=sum(r['active_agave'] for r in process_rows), processes=process_rows,
        healthy=finalized > 0 and processed >= finalized and total > 0 and current/total > 2/3
            and all(r['healthy'] for r in process_rows))
    try:
        row['transaction_count'] = rpc(url, 'getTransactionCount', [{'commitment':'finalized'}], timeout)
    except Exception as error:
        row['transaction_count_error'] = str(error)
    return row


def monitor(deployment, output, duration=18000, interval=60, core=False, gate=False,
            settle_seconds=0, rpc_timeout=240):
    _, nodes = deployment_info(deployment)
    selected = nodes[:2] if core else nodes
    url = f'http://{nodes[0]["ip"]}:8899'
    stop = False
    def stopping(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, stopping)
    signal.signal(signal.SIGTERM, stopping)
    start = changed = time.monotonic()
    initial = previous = stable = None
    sampler = None
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as handle:
        while not stop and (not duration or time.monotonic()-start < duration):
            began = time.monotonic()
            row = dict(timestamp=time.time(), elapsed_sec=began-start, expected_processes=len(selected))
            try:
                if sampler is None:
                    sampler = Processes(selected)
                row.update(observe(url, sampler, rpc_timeout))
                progress = row['finalized_slot']
                if initial is None:
                    initial = progress
                if previous is None or progress > previous:
                    changed = time.monotonic()
                previous = progress
            except Exception as error:
                row.update(healthy=False, error=str(error))
                sampler = None
            row['stalled'] = time.monotonic()-changed > 1800
            row['healthy'] = row['healthy'] and not row['stalled']
            row['round_seconds'] = time.monotonic()-began
            stable = (stable if stable is not None else time.monotonic()) if row['healthy'] else None
            handle.write(json.dumps(row)+'\n'); handle.flush()
            print(json.dumps({k:v for k,v in row.items() if k not in ('votes','processes')}), flush=True)
            if gate and stable is not None and previous > initial and time.monotonic()-stable >= max(interval, settle_seconds):
                return 0
            deadline = began+interval
            if duration:
                deadline = min(deadline, start+duration)
            while not stop and time.monotonic() < deadline:
                time.sleep(min(1, deadline-time.monotonic()))
    return (130 if stop else 1) if gate else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--duration', type=int, default=0)
    args = parser.parse_args()
    if args.interval <= 0 or args.duration < 0:
        parser.error('Invalid interval or duration')
    raise SystemExit(monitor(args.deployment, args.output, args.duration, args.interval))
