"""Monero RPC observations shared by readiness, measurement and miner control."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
from pathlib import Path
import signal
import time
import urllib.request


def nodes_digest(nodes):
    return hashlib.sha256(json.dumps(nodes, sort_keys=True).encode()).hexdigest()


def read_nodes(deployment):
    nodes = json.loads((Path(deployment)/'nodes.json').read_text())
    indices = [n['index'] for n in nodes]
    if len(nodes) < 8 or len(set(indices)) != len(indices) or 6 not in indices:
        raise ValueError('Invalid Monero node manifest')
    return nodes


def request(ip, port, path, payload=None):
    req = urllib.request.Request(f'http://{ip}:{port}{path}',
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as response:
        body = json.load(response)
    if body.get('error') or body.get('status', 'OK') != 'OK':
        raise RuntimeError(str(body))
    return body


def rpc(node, method, params):
    return request(node['ip'], node['rpc_port'], '/json_rpc',
        dict(jsonrpc='2.0', id=1, method=method, params=params))['result']


def monero_round(nodes, pool, expected_miners=None, active_indices=None):
    selected = set(active_indices) if active_indices is not None else {n['index'] for n in nodes if n['role'] == 'miner'}
    if not selected or not selected <= {n['index'] for n in nodes}:
        raise ValueError('Invalid active miner indices')
    reference = next(n for n in nodes if n['index'] in selected)
    info = request(reference['ip'], reference['rpc_port'], '/get_info')
    anchor = max(0, int(info['height']) - 11)
    block_hash = rpc(reference, 'get_block_header_by_height', dict(height=anchor))['block_header']['hash']

    def observe(node):
        row = dict(index=node['index'], ip=node['ip'], role=node['role'])
        try:
            data = request(node['ip'], node['rpc_port'], '/get_info')
            header = rpc(node, 'get_block_header_by_height', dict(height=anchor))['block_header']
            mining = request(node['ip'], node['rpc_port'], '/mining_status')
            row.update(info=data, anchor_hash=header['hash'], mining=mining)
            row['healthy'] = (data.get('status') == 'OK' and header['hash'] == block_hash
                and int(data['height']) >= anchor + 1
                and data.get('incoming_connections_count', 0) + data.get('outgoing_connections_count', 0) > 0)
        except Exception as error:
            row.update(healthy=False, error=str(error))
        return row

    observations = list(pool.map(observe, nodes))
    active = {row['index'] for row in observations if row.get('mining', {}).get('active')}
    miners = [row['mining'] for row in observations if row['index'] in active]
    mining = next(row.get('mining', {}) for row in observations if row['index'] == reference['index'])
    expected = len(selected) if expected_miners is None else expected_miners
    valid = (active == selected and len(active) == expected
        and all(m.get('threads_count') == 1 for m in miners)
        and bool(mining.get('address'))
        and all(m.get('address') == mining['address'] for m in miners))
    return dict(progress=int(info['height']), anchor_height=anchor, anchor_hash=block_hash,
        mining=mining, nodes=observations, expected_miners=expected, active_miners=len(active),
        mining_hashrate_hps=sum(m.get('speed', 0) for m in miners),
        healthy=valid and all(row['healthy'] for row in observations))


def sample(nodes, pool, state_path=None):
    if state_path is None:
        return monero_round(nodes, pool)
    with state_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        state = json.loads(state_path.read_text())
        if state['nodes_sha256'] != nodes_digest(nodes):
            raise ValueError('Miner state belongs to a different deployment')
        row = monero_round(nodes, pool, state['expected_miners'], state['active_indices'])
        row.update(miner_epoch=state['epoch'], active_indices=state['active_indices'])
        return row


def monitor(nodes, output, state=None, interval=60, duration=0, gate=False, stall_seconds=600):
    stop = False
    def stopping(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, stopping)
    signal.signal(signal.SIGTERM, stopping)
    start = changed = time.monotonic()
    initial = previous = None
    consecutive = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as handle, ThreadPoolExecutor(max_workers=32) as pool:
        while not stop and (not duration or time.monotonic() - start < duration):
            began = time.monotonic()
            row = dict(timestamp=time.time(), elapsed_sec=began-start)
            try:
                row.update(sample(nodes, pool, state))
                progress = row['progress']
                if initial is None:
                    initial = progress
                if previous is None or progress > previous:
                    changed = time.monotonic()
                previous = progress
            except Exception as error:
                row.update(healthy=False, error=str(error))
            row['stalled'] = time.monotonic() - changed > stall_seconds
            row['healthy'] = row['healthy'] and not row['stalled']
            row['round_seconds'] = time.monotonic() - began
            consecutive = consecutive + 1 if row['healthy'] else 0
            handle.write(json.dumps(row)+'\n')
            handle.flush()
            print(json.dumps({k:v for k,v in row.items() if k != 'nodes'}), flush=True)
            if gate and consecutive >= 2 and previous > initial:
                return 0
            deadline = began + interval
            if duration:
                deadline = min(deadline, start + duration)
            while not stop and time.monotonic() < deadline:
                time.sleep(min(1, deadline-time.monotonic()))
    return (130 if stop else 1) if gate else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--miner-state', type=Path)
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--duration', type=int, default=0)
    args = parser.parse_args()
    if args.interval <= 0 or args.duration < 0:
        parser.error('interval must be positive; duration must be nonnegative')
    raise SystemExit(monitor(read_nodes(args.deployment), args.output, args.miner_state,
        interval=args.interval, duration=args.duration))
