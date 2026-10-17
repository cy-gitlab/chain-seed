#!/usr/bin/env python3
"""Summarize the transfer window using retained resource, health and miner evidence."""
import argparse
from collections import defaultdict
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.blockchain.lib.project_resources import project_resources
from _transactions import summarize as summarize_transfers


def plot(directory, series):
    for field, label in [('total_mem_gib', 'Total container memory (GiB)'),
                         ('total_cpu_cores', 'Total container CPU (logical cores)')]:
        points = [r for r in series[1:] if r['complete']]
        if not points:
            continue
        xmax = max(r['elapsed_sec'] for r in points) or 1
        ymax = max(r[field] for r in points)*1.1 or 1
        coords = ' '.join(f'{70+r["elapsed_sec"]/xmax*680:.2f},{350-r[field]/ymax*270:.2f}' for r in points)
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="820" height="420" viewBox="0 0 820 420">',
               '<rect width="100%" height="100%" fill="white"/>',
               f'<text x="70" y="30" font-size="18">{label}</text>',
               '<path d="M70 70 V350 H750" stroke="black" fill="none"/>',
               f'<polyline points="{coords}" fill="none" stroke="#176ca4" stroke-width="2"/>',
               '<text x="340" y="405">Sampling time (hours)</text>']
        for i in range(6):
            svg.append(f'<text x="{70+i*136}" y="375">{xmax*i/5/3600:.1f}</text>')
            svg.append(f'<text x="5" y="{350-i*54}">{ymax*i/5:.1f}</text>')
        (directory/(field+'.svg')).write_text('\n'.join(svg+['</svg>']))


def quantile(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    p = (len(xs)-1)*q
    i = int(p)
    return xs[i]+(xs[min(i+1, len(xs)-1)]-xs[i])*(p-i)


def write_csv(path, rows):
    if rows:
        with path.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            w.writeheader()
            w.writerows(rows)


def coverage(times, start, duration, interval):
    end = start+duration
    intervals = sorted((max(start, t-interval), min(end, t)) for t in set(times)
                       if t >= start and t-interval <= end)
    total, right = 0, start
    for left, finish in intervals:
        total += max(0, finish-max(left, right))
        right = max(right, finish)
    return min(100, 100*total/duration)


def summarize(directory):
    destination = directory/'summary'
    destination.mkdir(parents=True, exist_ok=True)
    result, reasons = dict(chain='monero'), []
    try:
        measurement = json.loads((directory/'measurement.json').read_text())
        sampling = json.loads((directory/'transactions/sampling.json').read_text())
        start, duration = float(sampling['start']), float(sampling['duration'])
        interval, count = measurement['interval'], measurement['nodes']
        if duration <= 0 or interval <= 0:
            raise ValueError('Invalid sampling window')
        result.update(nodes=count, duration_seconds=duration, target_tps=sampling['rate'])
        if measurement['status'] != 'complete':
            reasons.append('measurement_incomplete')
        if measurement['started_at'] > start or measurement.get('stopped_at', 0) < start+duration:
            reasons.append('measurement_does_not_cover_transfer_window')
    except (OSError, ValueError, KeyError) as error:
        result.update(status='failed', failure_reasons=['sampling_configuration_missing'], error=str(error))
        (destination/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        return result

    try:
        resources = project_resources(directory, destination)
    except (OSError, ValueError, KeyError) as error:
        resources = []
        result['resource_error'] = str(error)
        reasons.append('resource_identity_unverified')
    groups = defaultdict(list)
    for row in resources:
        groups[row['sample']].append(row)
    first_sample = min(groups, key=int) if groups else None
    series = []
    roles = {'monero_seed', 'monero_miner', 'monero_full', 'monero_active_miner', 'monero_non_mining', 'monero_transition'}
    for sample_id in sorted(groups, key=int):
        rows = groups[sample_id]
        timestamp = datetime.fromisoformat(rows[0]['timestamp']).timestamp()
        if not start <= timestamp <= start+duration:
            continue
        present = sum(int(row['containers']) for row in rows if row['node_type'] in roles)
        item = dict(sample=sample_id, timestamp=timestamp, elapsed_sec=timestamp-start,
            nodes=present, complete=present == count and all(row['project_complete'] for row in rows),
            cpu_valid=sample_id != first_sample,
            total_mem_gib=sum(float(row['mem_total_bytes']) for row in rows)/2**30,
            total_cpu_cores=sum(float(row['cpu_avg_percent'])*int(row['containers']) for row in rows)/100)
        for row in rows:
            role = row['node_type']
            item[role+'_mem_gib'] = float(row['mem_total_bytes'])/2**30
            item[role+'_cpu_cores'] = float(row['cpu_avg_percent'])*int(row['containers'])/100
        series.append(item)
    write_csv(destination/'resources.csv', series)
    plot(destination, series)
    complete = [row for row in series if row['complete'] and row['cpu_valid']]
    resource_coverage = coverage([row['timestamp'] for row in complete], start, duration, interval)
    result['resource_coverage_pct'] = resource_coverage
    if resource_coverage < 95:
        reasons.append('incomplete_resource_window')
    if any(not row['complete'] for row in series):
        reasons.append('node_count_mismatch')
    for metric in ('total_mem_gib', 'total_cpu_cores'):
        values = [row[metric] for row in complete]
        result[metric+'_mean'] = statistics.mean(values) if values else None
        result[metric+'_p95'] = quantile(values, .95)
        result[metric+'_max'] = max(values) if values else None
    for role in sorted({row['node_type'] for row in resources}):
        for metric in ('mem_gib', 'cpu_cores'):
            values = [row[role+'_'+metric] for row in complete if role+'_'+metric in row]
            if values:
                result[f'{role}_{metric}_mean'] = statistics.mean(values)
    health = []
    try:
        with (directory/'final_health.jsonl').open() as handle:
            for line in handle:
                row = json.loads(line)
                if start <= row['timestamp'] <= start+duration:
                    health.append({k:v for k,v in row.items() if k != 'nodes'})
    except (OSError, ValueError, KeyError) as error:
        reasons.append('health_evidence_missing')
        result['health_error'] = str(error)
    healthy = sum(bool(row.get('healthy')) for row in health)
    result.update(health_coverage_pct=coverage([row['timestamp'] for row in health], start, duration, interval),
        healthy_round_pct=100*healthy/len(health) if health else None)
    if result['health_coverage_pct'] < 95 or not health or healthy/len(health) < .99:
        reasons.append('chain_health_or_coverage')
    progress = [row['progress'] for row in health if 'progress' in row]
    result['chain_progress_delta'] = progress[-1]-progress[0] if len(progress) > 1 else None
    if len(progress) < 2 or progress[-1] <= progress[0] or any(row.get('stalled') for row in health):
        reasons.append('no_continuous_chain_progress')
    mining = [row for row in health if 'expected_miners' in row and 'active_miners' in row]
    if mining:
        result.update(expected_miners=mining[0]['expected_miners'],
            active_miners_min=min(row['active_miners'] for row in mining),
            active_miners_max=max(row['active_miners'] for row in mining))
        if any(row['active_miners'] != row['expected_miners'] for row in mining):
            reasons.append('miner_count_mismatch')
    try:
        transfers = summarize_transfers(directory)
        result['transactions'] = transfers
        if transfers['status'] != 'passed':
            reasons.append('transaction_load_or_inclusion_failed')
    except (OSError, ValueError, KeyError) as error:
        result['transaction_error'] = str(error)
        reasons.append('transaction_evidence_missing')
    rotation = directory/'miner-rotation.jsonl'
    if measurement.get('miner_state'):
        try:
            events = [json.loads(line) for line in rotation.read_text().splitlines()]
            commits = [event for event in events if event['event'] == 'transition_commit']
            failures = [event for event in events if event['event'] == 'transition_failed']
            result.update(miner_rotation_commits=len(commits), miner_rotation_failures=len(failures),
                transition_resource_samples=sum('monero_transition_mem_gib' in row for row in complete))
            if not commits or failures:
                reasons.append('miner_transition_failed')
            controller_path = directory/'miner-controller.json'
            if controller_path.exists():
                controller = json.loads(controller_path.read_text())
                observed = [event for event in commits if start <= event['time'] <= start+duration]
                result.update(miner_rotation_interval_seconds=controller['interval'], rotations_in_window=len(observed))
                if controller['status'] not in ('complete', 'stopped') or controller['started_at'] > start or controller.get('stopped_at', 0) < start+duration:
                    reasons.append('rotation_does_not_cover_transfer_window')
                if duration >= controller['interval']*1.5 and not observed:
                    reasons.append('no_miner_rotation_in_window')
        except (OSError, ValueError, KeyError) as error:
            result['rotation_error'] = str(error)
            reasons.append('miner_transition_evidence_missing')
    result.update(status='passed' if not reasons else 'failed', failure_reasons=reasons)
    (destination/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    write_csv(destination/'result.csv', [{**result, 'failure_reasons': ';'.join(reasons),
        'transactions': json.dumps(result.get('transactions', {}))}])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.run_dir.resolve())
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
