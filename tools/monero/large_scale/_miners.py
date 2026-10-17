#!/usr/bin/env python3
"""Select real single-thread miners through RPC, retaining every transition."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import random
import time
from _health import request, nodes_digest


def select_indices(nodes, count, seed, epoch):
    return sorted(random.Random(seed + epoch).sample([n['index'] for n in nodes], count))


def rotate(nodes, count, seed, state_path, grow=False):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    journal = state_path.parent/'miner-rotation.jsonl'
    def record(event, **values):
        with journal.open('a') as handle:
            handle.write(json.dumps(dict(time=time.time(), event=event, **values))+'\n')
            handle.flush()
    with state_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(state_path.read_text()) if state_path.exists() else None
        if previous and previous['nodes_sha256'] != nodes_digest(nodes):
            raise ValueError('Miner state belongs to a different deployment')
        epoch = previous['epoch'] + 1 if previous else 0
        chosen = select_indices(nodes, count, seed, epoch)
        by_index = {n['index']:n for n in nodes}
        def status(node):
            return node['index'], request(node['ip'], node['rpc_port'], '/mining_status')
        with ThreadPoolExecutor(max_workers=8) as pool:
            current = dict(pool.map(status, nodes))
        if any(s.get('status') != 'OK' for s in current.values()):
            raise RuntimeError('Cannot verify candidate mining states')
        active = {i for i,s in current.items() if s.get('active')}
        if grow:
            if len(active) > count:
                raise RuntimeError('Ramp cannot reduce an existing active cohort')
            available = sorted(set(by_index)-active)
            additions = random.Random(seed + epoch).sample(available, count-len(active))
            chosen = sorted(active | set(additions))
        address = previous['address'] if previous else current[6].get('address')
        if not address:
            raise RuntimeError('Bootstrap reward address is unavailable')
        begin = time.time()
        record('transition_start', epoch=epoch, previous=sorted(active), target=chosen)
        try:
            # Start replacements first to avoid stopping the only miner.
            for i in chosen:
                s = current[i]
                if i in active and (s.get('threads_count') != 1 or s.get('address') != address):
                    response = request(by_index[i]['ip'], by_index[i]['rpc_port'], '/stop_mining', {})
                    if response.get('status') != 'OK':
                        raise RuntimeError(f'Cannot stop misconfigured miner {i}')
                    active.remove(i)
                if i not in active:
                    response = request(by_index[i]['ip'], by_index[i]['rpc_port'], '/start_mining',
                        dict(miner_address=address, threads_count=1,
                             do_background_mining=False, ignore_battery=True))
                    record('start_response', index=i, response=response)
                    if response.get('status') != 'OK':
                        raise RuntimeError(f'Cannot start miner {i}')
            for i in sorted(active-set(chosen)):
                response = request(by_index[i]['ip'], by_index[i]['rpc_port'], '/stop_mining', {})
                record('stop_response', index=i, response=response)
                if response.get('status') != 'OK':
                    raise RuntimeError(f'Cannot stop miner {i}')
            with ThreadPoolExecutor(max_workers=8) as pool:
                verified = dict(pool.map(status, nodes))
            actual = sorted(i for i,s in verified.items() if s.get('active'))
            if actual != chosen or any(s.get('status') != 'OK' for s in verified.values()) or any(
                    verified[i].get('threads_count') != 1 or verified[i].get('address') != address for i in chosen):
                raise RuntimeError('Actual mining states do not match requested cohort')
            state = dict(epoch=epoch, active_indices=chosen, expected_miners=count,
                         address=address, seed=seed, committed_at=time.time(),
                         nodes_sha256=nodes_digest(nodes))
            temporary = state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state, indent=2)+'\n')
            temporary.replace(state_path)
            record('transition_commit', epoch=epoch, active_indices=chosen,
                   started_at=begin, verified=True, states=verified)
            return state
        except BaseException as error:
            record('transition_failed', epoch=epoch, error=str(error))
            raise
