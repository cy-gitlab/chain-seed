#!/usr/bin/env python3
"""Grow miner cohorts only after recorded health and block-rate adaptation gates."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from _health import monero_round
from _miners import rotate


def levels(target, step):
    return list(range(step, target, step)) + [target]


def ready(rows, count, minimum_seconds, window_seconds, max_rate_factor):
    if len(rows) < 3 or not all(r.get('healthy') for r in rows[-3:]):
        return False, None
    if rows[-1]['elapsed_sec'] < minimum_seconds:
        return False, None
    end = rows[-1]
    earlier = [r for r in rows if r.get('healthy') and
               end['observed_at']-r['observed_at'] >= window_seconds]
    if not earlier:
        return False, None
    first = earlier[-1]
    elapsed = end['observed_at']-first['observed_at']
    blocks = end['progress']-first['progress']
    target = end.get('mining', {}).get('block_target', 0)
    if not target or elapsed <= 0 or blocks <= 0 or end.get('active_miners') != count:
        return False, None
    rate = blocks*3600/elapsed
    return rate <= max_rate_factor*3600/target, rate


def ramp(nodes, target, step, seed, state_path, output, minimum_seconds=600,
         window_seconds=600, timeout=21600, max_rate_factor=1.5):
    with output.open('x') as journal, ThreadPoolExecutor(max_workers=32) as pool:
        def record(row):
            journal.write(json.dumps(row)+'\n')
            journal.flush()
        for count in levels(target, step):
            state = rotate(nodes, count, seed, state_path, grow=True)
            print(f'Ramp active_miners={count}/{target}; waiting for health and block-rate gate', flush=True)
            start = time.monotonic()
            rows = []
            while time.monotonic()-start < timeout:
                began = time.monotonic()
                row = dict(event='observation', count=count, timestamp=time.time(),
                           elapsed_sec=began-start)
                try:
                    row.update(monero_round(nodes, pool, count, state['active_indices']))
                except Exception as error:
                    row.update(healthy=False, error=str(error))
                row['observed_at'] = time.time()
                row['host'] = dict(meminfo=Path('/proc/meminfo').read_text(),
                    cpu_stat=Path('/proc/stat').read_text().splitlines()[0],
                    loadavg=Path('/proc/loadavg').read_text())
                rows.append(row)
                passed, rate = ready(rows, count, minimum_seconds, window_seconds, max_rate_factor)
                row.update(gate_ready=passed, observed_blocks_per_hour=rate)
                record(row)
                if passed:
                    record(dict(event='level_ready', timestamp=time.time(), count=count,
                                observed_blocks_per_hour=rate, difficulty=row['mining'].get('difficulty')))
                    print(f'Ramp level ready active_miners={count} blocks_per_hour={rate:.2f}', flush=True)
                    break
                time.sleep(max(0, 60-(time.monotonic()-began)))
            else:
                record(dict(event='ramp_failed', timestamp=time.time(), count=count, reason='adaptation_timeout'))
                raise RuntimeError(f'Miner ramp level {count} did not pass adaptation gate within {timeout}s')
