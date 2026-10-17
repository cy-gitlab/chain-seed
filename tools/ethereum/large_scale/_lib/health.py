#!/usr/bin/env python3
"""Bounded, read-only CL/EL coverage and finality evidence, independent of Docker exec."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import os
from pathlib import Path
import signal
import time
import urllib.request
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "transaction_generator"))
from compose_endpoints import discover_role_ips


def query(url, body=None, timeout=5):
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                    headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as r:
        result = json.load(r)
    if 'error' in result:
        raise RuntimeError(str(result['error']))
    return result


def rpc(ip, method, params):
    return query(f'http://{ip}:8545', dict(jsonrpc='2.0', id=1, method=method, params=params))['result']


def observe_beacon(item):
    index, ip = item
    result = dict(index=index, ip=ip, errors={})
    base = f'http://{ip}:8000'
    for name, path in [('head', '/eth/v1/beacon/headers/head'),
                       ('finality', '/eth/v1/beacon/states/head/finality_checkpoints'),
                       ('syncing', '/eth/v1/node/syncing'), ('peers', '/eth/v1/node/peer_count'),
                       ('peer_details', '/eth/v1/node/peers')]:
        try:
            result[name] = query(base + path)
        except Exception as error:
            result['errors'][name] = str(error)
    return result


def observe_geth(item, anchor):
    index, ip = item
    result = dict(index=index, ip=ip)
    try:
        result['head'] = int(rpc(ip, 'eth_blockNumber', []), 16)
        block = rpc(ip, 'eth_getBlockByNumber', [hex(anchor['number']), False])
        result['anchor_hash'] = block['hash'] if block else None
        result['same_anchor'] = result['anchor_hash'] == anchor['hash']
    except Exception as error:
        result['error'] = str(error)
        result['same_anchor'] = False
    return result


def health(row, slots_per_epoch):
    beacons = row.get('beacons', [])
    geths = row.get('geths', [])
    good_bn = all(not b['errors'] and
                  not b['syncing']['data'].get('is_syncing', True) and
                  not b['syncing']['data'].get('is_optimistic', True) and
                  not b['syncing']['data'].get('el_offline', True) and
                  int(b['finality']['data']['finalized']['epoch']) > 0 and
                  int(b['head']['data']['header']['message']['slot']) // slots_per_epoch -
                  int(b['finality']['data']['finalized']['epoch']) <= 3
                  for b in beacons)
    return bool(beacons and geths and good_bn and all(g.get('same_anchor') for g in geths))


def cpu_snapshot():
    line = Path('/proc/stat').read_text().splitlines()[0]
    ticks = list(map(int, line.split()[1:9]))
    return dict(total=sum(ticks), idle=ticks[3], iowait=ticks[4], cpus=os.cpu_count())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compose', type=Path, required=True)
    p.add_argument('--data-dir', type=Path, required=True)
    p.add_argument('--gate', action='store_true')
    p.add_argument('--timeout', type=int, default=7200)
    p.add_argument('--interval', type=int, default=60)
    p.add_argument('--epoch-advances', type=int, default=2)
    p.add_argument('--expected-validators', type=int, required=True)
    a = p.parse_args()
    root = a.data_dir / 'health'
    root.mkdir(parents=True, exist_ok=True)
    beacons = discover_role_ips(a.compose, 'Ethereum-POS-Beacon')
    geths = discover_role_ips(a.compose, 'Ethereum-POS-Geth')
    sample = sorted(set(round(i * (len(beacons) - 1) / 5) for i in range(6)))
    base = f'http://{beacons[0]}:8000'
    stopped = False
    def stopping(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    deadline = time.monotonic() + a.timeout
    first_finalized = None
    with ThreadPoolExecutor(max_workers=8) as pool, (root / ('gate.jsonl' if a.gate else 'samples.jsonl')).open('x') as out:
        while not stopped:
            started = time.monotonic()
            row = dict(time=time.time(), host_cpu=cpu_snapshot(), host_meminfo=Path('/proc/meminfo').read_text(), healthy=False)
            try:
                spec = query(base + '/eth/v1/config/spec')['data']
                row['beacons'] = list(pool.map(observe_beacon, [(i, beacons[i]) for i in sample]))
                head = int(rpc(geths[0], 'eth_blockNumber', []), 16)
                anchor = rpc(geths[0], 'eth_getBlockByNumber', [hex(max(0, head - 2)), False])
                row['anchor'] = dict(number=int(anchor['number'], 16), hash=anchor['hash'])
                row['geths'] = list(pool.map(lambda item: observe_geth(item, row['anchor']), enumerate(geths)))
                row['healthy'] = head > 0 and health(row, int(spec['SLOTS_PER_EPOCH']))
                finalized = int(row['beacons'][0]['finality']['data']['finalized']['epoch'])
                row['finalized_epoch'] = finalized
                if first_finalized is None:
                    first_finalized = finalized
                if a.gate and row['healthy'] and finalized >= first_finalized + a.epoch_advances:
                    registry = query(base + '/eth/v1/beacon/states/head/validators?status=active_ongoing', timeout=120)
                    if len(registry['data']) != a.expected_validators:
                        raise RuntimeError('Active validator count mismatch')
                    with gzip.open(root / 'active-validators.json.gz', 'wt') as handle:
                        json.dump(registry, handle)
                    (root / 'metadata.json').write_text(json.dumps(dict(spec=spec,
                        genesis=query(base + '/eth/v1/beacon/genesis'), observers=sample), indent=2))
                    out.write(json.dumps(row) + '\n'); out.flush()
                    return 0
            except Exception as exc:
                row['healthy'] = False
                row['error'] = str(exc)
            out.write(json.dumps(row) + '\n'); out.flush()
            if a.gate and time.monotonic() >= deadline:
                return 1
            while not stopped and time.monotonic() - started < a.interval:
                time.sleep(.25)
    return 130 if a.gate else 0


if __name__ == '__main__':
    raise SystemExit(main())
